# ADR-003: Two-Level Queue Model (Jobs → Tasks)

## Status

Accepted

## Context

Users submit transcription requests with various options:
- Speaker diarization (yes/no)
- Word timestamps (yes/no)
- Emotion detection (yes/no)
- LLM cleanup (yes/no)

Each combination requires different processing steps. Some steps can run in parallel, others have dependencies.

We need a model that:
1. Handles variable pipeline configurations
2. Supports parallel execution where possible
3. Provides granular progress tracking
4. Enables retry of individual steps (not entire jobs)
5. Allows optional steps to fail without blocking the job

## Options Considered

### 1. Single Queue, Monolithic Processing

One queue, one worker type that does everything.

```
User Request → Queue → Worker (does all steps) → Result
```

**Pros:**
- Simple to understand
- No coordination needed
- Single point of monitoring

**Cons:**
- No parallelism within a job
- Retry means redoing everything
- Can't scale individual stages
- GPU memory must fit all models simultaneously

### 2. Fixed Pipeline with Stage Queues

Predefined stages, each with its own queue. Every job goes through all stages.

```
User Request → Prepare Queue → Transcribe Queue → Align Queue → ... → Result
```

**Pros:**
- Clear pipeline structure
- Each stage scales independently
- Retry at stage level

**Cons:**
- Inflexible (all jobs do all stages)
- No parallelism between independent stages
- Wasted work for simple jobs

### 3. Two-Level Model: Jobs → Task DAGs (Chosen)

Jobs are high-level requests. Each job expands into a DAG of tasks based on its parameters.

```
User Request → Job → [Task DAG] → Per-engine Queues → Results → Merge
```

**Pros:**
- Flexible pipeline per job
- Parallel execution of independent tasks
- Granular retry (single task, not whole job)
- Optional tasks can fail gracefully
- Clear separation of scheduling (orchestrator) and execution (engines)

**Cons:**
- More complex coordination
- Orchestrator becomes critical component
- Must track task dependencies

## Decision

Implement a two-level queue model:

### Level 1: Jobs

- User-facing abstraction
- Contains parameters and settings
- Stored in PostgreSQL
- Status: `pending` → `running` → `completed/failed`

### Level 2: Tasks

- Internal implementation detail
- Each task runs on one engine
- Has explicit dependencies (other task IDs)
- Stored in PostgreSQL, queued in Redis
- Status: `pending` → `ready` → `running` → `completed/failed/skipped`

### DAG Expansion Example

**Job parameters:**
```json
{
  "speaker_detection": "diarize",
  "word_timestamps": true,
  "detect_emotions": true
}
```

**Expanded to:**
```
prepare ──→ transcribe ──→ align ──→ diarize ──┬──→ emotions ──→ merge
                                               └──→ (parallel)
```

### Task State Machine

```
PENDING ──→ READY ──→ RUNNING ──→ COMPLETED
   ↑                      │
   └──────────────────────┘ (retry)
                          │
                          └──→ FAILED ──→ SKIPPED (if optional)
```

### Scheduling Rules

1. Task moves `PENDING` → `READY` when all dependencies are `COMPLETED`
2. `READY` tasks are pushed to engine-specific Redis queues
3. Engine pulls task, moves to `RUNNING`, processes, moves to `COMPLETED`
4. On `COMPLETED`, orchestrator checks dependents and advances them
5. On `FAILED`, retry if under limit, else mark `SKIPPED` if optional or fail job

## Consequences

### Easier

- Adding new pipeline stages (just add task type)
- Parallel processing (independent tasks run simultaneously)
- Partial retries (redo one task, not the whole job)
- Progress tracking (X of Y tasks complete)
- Graceful degradation (emotions fail? still get transcript)

### Harder

- Understanding job progress (must aggregate task states)
- Debugging failed jobs (which task? what dependencies?)
- Testing (more states and transitions to cover)
- Orchestrator complexity (dependency tracking, failure handling)

### Mitigations

- Job status API aggregates task states into simple progress
- Structured logging with job_id and task_id correlation
- Comprehensive test suite for orchestrator state machine
- Clear task state documentation
