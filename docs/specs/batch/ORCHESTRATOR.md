# Dalston Orchestrator

## Overview

The Orchestrator is a background service responsible for expanding jobs into task DAGs, scheduling tasks, and managing the job lifecycle.

### Storage Architecture

| Data | Storage | Purpose |
|------|---------|---------|
| Jobs & Tasks | PostgreSQL | Persistent state, queryable |
| Work Queues | Redis | Ephemeral task scheduling |
| Events | Redis Pub/Sub | Real-time notifications |
| Audio & Outputs | S3 | Artifact storage |

---

## Responsibilities

1. **Job Expansion**: Convert user job requests into task DAGs based on parameters
2. **Engine Selection**: Choose optimal engines (single-stage or multi-stage)
3. **Task Scheduling**: Push ready tasks to engine queues
4. **Dependency Management**: Track task completion and advance dependents
5. **Failure Handling**: Retry failed tasks, handle optional task failures
6. **Progress Tracking**: Calculate and report job progress
7. **Completion**: Trigger final merge and webhooks

---

## Two-Level Queue Model

```
LEVEL 1: JOB QUEUE
──────────────────

"Process this audio file with these settings"

Jobs are high-level requests from users.
Each job expands into multiple tasks.

┌─────────┐  ┌─────────┐  ┌─────────┐
│  Job 1  │  │  Job 2  │  │  Job 3  │
└────┬────┘  └────┬────┘  └────┬────┘
     │            │            │
     ▼            ▼            ▼

ORCHESTRATOR expands each job into task DAG

     │            │            │
     ▼            ▼            ▼

LEVEL 2: TASK QUEUES (per engine)
─────────────────────────────────

"Run this specific engine on this specific input"

Tasks are atomic units of work. Each task:
- Belongs to one job
- Runs on one specific engine
- Has defined inputs and outputs
- May depend on other tasks

┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ dalston:queue: │  │ dalston:queue: │  │ dalston:queue: │
│ faster-whisper │  │ pyannote-3.1   │  │ whisperx-align │
│                │  │                │  │                │
│ [task] [task]  │  │ [task]         │  │ [task] [task]  │
└────────────────┘  └────────────────┘  └────────────────┘
```

---

## Task State Machine

```
                 ┌──────────────────────────────────┐
                 │                                  │
                 ▼                                  │
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ PENDING │───▶│  READY  │───▶│ RUNNING │───▶│COMPLETED│
└─────────┘    └─────────┘    └────┬────┘    └─────────┘
     │                             │
     │                             │
     │                             ▼
     │                        ┌─────────┐
     │                        │ FAILED  │
     │                        └────┬────┘
     │                             │
     │                             │ retry < max_retries
     │                             │
     └─────────────────────────────┘


PENDING:    Created, waiting for dependencies to complete
READY:      All dependencies met, queued for execution
RUNNING:    Picked up by engine worker, in progress
COMPLETED:  Successfully finished, output available
FAILED:     Error occurred (may retry)
SKIPPED:    Optional task failed, job continues
```

---

## DAG Building

### Input: Job Parameters

```json
{
  "speaker_detection": "diarize",
  "word_timestamps": true,
  "detect_emotions": true,
  "detect_events": false,
  "llm_cleanup": true,
  "engine_preference": null
}
```

### Step 1: Determine Required Stages

```python
required_stages = ["prepare"]  # Always required

if job.word_timestamps or job.speaker_detection == "diarize":
    required_stages.append("transcribe")
    required_stages.append("align")
    
if job.speaker_detection == "diarize":
    required_stages.append("diarize")
    
if job.speaker_detection == "per_channel":
    required_stages.append("transcribe")  # Per channel
    required_stages.append("align")       # Per channel
    # No diarize - speakers known from channels
    
if job.detect_emotions:
    required_stages.append("detect_emotions")
    
if job.detect_events:
    required_stages.append("detect_events")
    
if job.llm_cleanup:
    required_stages.append("refine")
    
required_stages.append("merge")  # Always required
```

### Step 2: Select Engines

```python
def select_engines(required_stages, preference):
    """Select engines to cover all required stages."""
    
    available = get_available_engines()
    selected = []
    remaining = set(required_stages)
    
    # Check for multi-stage engine covering multiple needed stages
    if preference != "modular":
        for engine in available:
            coverage = remaining & set(engine.stages)
            if len(coverage) > 1:
                # Multi-stage engine covers multiple needs
                selected.append(engine)
                remaining -= coverage
    
    # Fill remaining with single-stage engines
    for stage in remaining:
        engine = find_best_engine_for_stage(stage, available, preference)
        selected.append(engine)
    
    return selected
```

### Step 3: Build Task Graph

#### Example 1: Simple Transcription

**Parameters**: `speaker_detection: none, word_timestamps: false`

```
┌─────────┐    ┌─────────────┐    ┌────────┐
│ prepare │───▶│ transcribe  │───▶│ merge  │
└─────────┘    └─────────────┘    └────────┘

3 tasks, sequential
```

#### Example 2: With Diarization (Modular)

**Parameters**: `speaker_detection: diarize, word_timestamps: true, engine_preference: modular`

```
┌─────────┐    ┌─────────────┐    ┌─────────┐    ┌──────────┐    ┌────────┐
│ prepare │───▶│ transcribe  │───▶│  align  │───▶│ diarize  │───▶│ merge  │
└─────────┘    │ (faster-    │    │(whisperx│    │(pyannote)│    └────────┘
               │  whisper)   │    │ -align) │    └──────────┘
               └─────────────┘    └─────────┘

5 tasks, sequential
```

#### Example 3: With Diarization (WhisperX Integrated)

**Parameters**: `speaker_detection: diarize, word_timestamps: true, engine_preference: whisperx`

```
┌─────────┐    ┌────────────────────────────────────┐    ┌────────┐
│ prepare │───▶│         whisperx-full              │───▶│ merge  │
└─────────┘    │  [transcribe + align + diarize]    │    └────────┘
               └────────────────────────────────────┘

3 tasks (multi-stage engine covers 3 stages in 1 task)
```

#### Example 4: Per-Channel (Stereo)

**Parameters**: `speaker_detection: per_channel, word_timestamps: true`

```
┌─────────┐
│ prepare │
│ (split) │
└────┬────┘
     │
┌────┴────┐
│         │
▼         ▼
┌───────┐ ┌───────┐
│trans. │ │trans. │   (parallel)
│ ch 0  │ │ ch 1  │
└───┬───┘ └───┬───┘
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│ align │ │ align │   (parallel)
│ ch 0  │ │ ch 1  │
└───┬───┘ └───┬───┘
    │         │
    └────┬────┘
         │
         ▼
    ┌────────┐
    │ merge  │
    └────────┘

6 tasks, with parallelization
```

#### Example 5: Full Pipeline with Enrichment

**Parameters**: All features enabled

```
┌─────────┐
│ prepare │
└────┬────┘
     │
     ▼
┌─────────────┐
│ transcribe  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    align    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   diarize   │
└──────┬──────┘
       │
┌──────┼──────┐
│      │      │
▼      ▼      ▼
┌─────┐┌─────┐┌─────┐
│emot.││event││topic│  (parallel enrichment)
└──┬──┘└──┬──┘└──┬──┘
   │      │      │
   └──────┼──────┘
          │
          ▼
   ┌─────────────┐
   │ llm-cleanup │
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │ final-merge │
   └─────────────┘

10 tasks, with parallelization
```

---

## Event Handling

### Main Event Loop

```python
async def orchestrator_loop():
    pubsub = redis.pubsub()
    pubsub.subscribe("dalston:events")
    
    while True:
        message = await pubsub.get_message()
        
        if message["type"] != "message":
            continue
            
        event = json.loads(message["data"])
        
        if event["type"] == "job.created":
            await handle_job_created(event["job_id"])
            
        elif event["type"] == "task.completed":
            await handle_task_completed(event["task_id"])
            
        elif event["type"] == "task.failed":
            await handle_task_failed(event["task_id"])
```

### Handle Job Created

```python
async def handle_job_created(job_id: str):
    job = await db.jobs.get(job_id)  # PostgreSQL

    # Download and analyze audio from S3
    audio_info = await analyze_audio(job.audio_uri)

    # Build task DAG
    tasks = build_task_dag(job, audio_info)

    # Save all tasks to PostgreSQL
    for task in tasks:
        await db.tasks.create(task)

    # Queue tasks with no dependencies (Redis)
    for task in tasks:
        if not task.dependencies:
            await db.tasks.update(task.id, status="ready")
            await redis.lpush(f"dalston:queue:{task.engine_id}", str(task.id))

    # Update job status
    await db.jobs.update(job_id, status="running", started_at=datetime.utcnow())
```

### Handle Task Completed

```python
async def handle_task_completed(task_id: str):
    task = await db.tasks.get(task_id)  # PostgreSQL
    job = await db.jobs.get(task.job_id)

    # Find tasks that depend on this one
    all_tasks = await db.tasks.get_by_job(job.id)
    dependents = [t for t in all_tasks if task.id in t.dependencies]

    for dependent in dependents:
        # Check if ALL dependencies are complete
        dep_tasks = await db.tasks.get_many(dependent.dependencies)

        if all(t.status == "completed" for t in dep_tasks):
            # All dependencies met - queue this task (Redis)
            await db.tasks.update(dependent.id, status="ready")
            await redis.lpush(f"dalston:queue:{dependent.engine_id}", str(dependent.id))

    # Check if job is complete
    if all(t.status in ["completed", "skipped"] for t in all_tasks):
        await db.jobs.update(job.id, status="completed", completed_at=datetime.utcnow())

        # Trigger webhook
        if job.webhook_url:
            await send_webhook(job)
```

### Handle Task Failed

```python
async def handle_task_failed(task_id: str):
    task = await db.tasks.get(task_id)  # PostgreSQL
    job = await db.jobs.get(task.job_id)

    if task.retries < task.max_retries:
        # Retry - update PostgreSQL, queue to Redis
        await db.tasks.update(task.id, retries=task.retries + 1, status="ready", error=None)
        await redis.lpush(f"dalston:queue:{task.engine_id}", str(task.id))

    elif not task.required:
        # Optional task - skip and continue
        await db.tasks.update(task.id, status="skipped")

        # Publish completion event to unblock dependents (Redis pub/sub)
        await redis.publish("dalston:events", json.dumps({
            "type": "task.completed",  # Treat as complete for dependency purposes
            "task_id": str(task.id),
            "job_id": str(task.job_id)
        }))

    else:
        # Required task failed - fail job
        await db.jobs.update(job.id, status="failed", error=f"Task {task.stage} failed: {task.error}")

        # Send failure webhook
        if job.webhook_url:
            await send_webhook(job)
```

---

## Stage Requirements & Fallbacks

Each pipeline stage has a default `required` setting and fallback behavior when it fails.

### Default Stage Configuration

| Stage | Required | Max Retries | Fallback Behavior |
|-------|----------|-------------|-------------------|
| `prepare` | **Yes** | 3 | — (cannot proceed without audio prep) |
| `transcribe` | **Yes** | 3 | — (core functionality) |
| `align` | No | 2 | Use word timestamps from transcription engine |
| `diarize` | No | 2 | Mark all segments as `SPEAKER_00` |
| `detect_emotions` | No | 1 | Omit emotion data from output |
| `detect_events` | No | 1 | Omit audio events from output |
| `refine` (LLM cleanup) | No | 1 | Use raw transcription text |
| `merge` | **Yes** | 3 | — (must produce final output) |

### Override Per-Job

Users can override the default `required` setting per job:

```json
{
  "speaker_detection": "diarize",
  "pipeline": {
    "stages": {
      "diarize": { "required": true },
      "detect_emotions": { "required": false }
    }
  }
}
```

### Fallback Behavior Details

When an optional stage fails and is skipped:

**align (skipped):**
- Uses `words` array from transcription output if available
- Falls back to segment-level timestamps only
- Sets `alignment_source: "transcription"` in metadata

**diarize (skipped):**
- All segments assigned to `SPEAKER_00`
- `speaker_count: 1` in metadata
- Sets `diarization_source: "default"` in metadata

**detect_emotions (skipped):**
- `emotion` and `emotion_confidence` fields omitted from segments
- No emotion summary in metadata

**detect_events (skipped):**
- `events` array empty in all segments
- No audio events in output

**refine (skipped):**
- Raw transcription text used without LLM cleanup
- Speaker labels remain as `SPEAKER_00`, `SPEAKER_01` (no name inference)
- No paragraph/topic segmentation

### Pipeline Warnings

When fallbacks are activated, the final transcript includes warnings in metadata:

```json
{
  "metadata": {
    "pipeline_warnings": [
      {
        "stage": "diarize",
        "status": "skipped",
        "fallback": "single_speaker",
        "reason": "pyannote engine unavailable",
        "timestamp": "2025-01-28T12:01:30Z"
      },
      {
        "stage": "align",
        "status": "skipped",
        "fallback": "transcription_timestamps",
        "reason": "whisperx-align failed after 2 retries",
        "timestamp": "2025-01-28T12:00:45Z"
      }
    ]
  }
}
```

This allows clients to understand why certain features are missing from the output.

---

## Progress Calculation

```python
async def calculate_job_progress(job_id: str) -> dict:
    tasks = await db.tasks.get_by_job(job_id)  # PostgreSQL

    completed = sum(1 for t in tasks if t.status == "completed")
    total = len(tasks)

    # Find currently running task
    running = next((t for t in tasks if t.status == "running"), None)

    return {
        "overall": int(completed / total * 100),
        "current_stage": running.stage if running else None,
        "current_stage_progress": running.progress if running else None,
        "stages": {
            t.stage: t.status for t in tasks
        }
    }
```

---

## Engine Selection Algorithm

```python
def select_engines_for_stages(required_stages: list[str], preference: str) -> list[Engine]:
    """
    Select engines to cover all required stages.
    
    Strategy:
    1. If preference is "modular", only use single-stage engines
    2. Otherwise, prefer multi-stage engines when they cover multiple needed stages
    3. Fall back to single-stage engines for remaining stages
    """
    
    available = get_available_engines()
    selected = []
    remaining = set(required_stages)
    
    if preference == "modular":
        # Only single-stage engines
        for stage in required_stages:
            engine = find_single_stage_engine(stage, available)
            selected.append((stage, engine))
        return selected
    
    # Sort engines by coverage (most stages first)
    def coverage_score(engine):
        return len(remaining & set(engine.stages))
    
    while remaining:
        # Find engine with best coverage
        best = max(available, key=coverage_score, default=None)
        
        if best is None or coverage_score(best) == 0:
            raise ValueError(f"No engine available for stages: {remaining}")
        
        covered = remaining & set(best.stages)
        
        for stage in covered:
            selected.append((stage, best))
        
        remaining -= covered
        
        # Don't reuse multi-stage engine for different stage groups
        if len(best.stages) > 1:
            available = [e for e in available if e.id != best.id]
    
    return selected
```

---

## Configuration

```yaml
# config/orchestrator.yaml

database:
  url: postgresql://dalston:password@localhost:5432/dalston

redis:
  url: redis://localhost:6379

s3:
  bucket: dalston-artifacts
  region: eu-west-2

queues:
  # How long workers wait on empty queue
  poll_timeout: 30

tasks:
  # Default retry settings
  max_retries: 2
  retry_delay: 5

  # Task timeout (fail if running longer)
  timeout: 3600  # 1 hour

engines:
  # Preference for engine selection
  default_preference: null  # null = auto, "modular", or specific engine

  # Health check interval
  heartbeat_interval: 30
  heartbeat_timeout: 90
```

---

## Monitoring

The orchestrator exposes metrics for monitoring:

- `dalston_jobs_total` — Total jobs by status
- `dalston_tasks_total` — Total tasks by stage and status
- `dalston_queue_depth` — Current queue depth per engine
- `dalston_task_duration_seconds` — Task execution duration histogram
- `dalston_job_duration_seconds` — Job total duration histogram
