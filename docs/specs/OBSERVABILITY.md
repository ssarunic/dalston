# Task-Level Observability

## Strategic

### Goal

Provide visibility into individual pipeline stages within a transcription job — their status, timing, and artifacts — so that users can understand what happened during processing, and operators can debug quality and performance issues.

### Problem

Today, the job status API returns a flat view: `status`, `progress`, `current_stage`. When a job fails, produces unexpected output, or takes too long, there is no way to answer:

- Which stage failed, and what was the error?
- How long did each stage take? Where is the bottleneck?
- What did the transcriber produce before alignment adjusted it?
- Did diarization run, or was it skipped? Why?
- What input did the merge stage receive?

The data to answer all of these questions already exists — tasks are tracked in PostgreSQL with status and timing, and every task's input/output is persisted in S3. The gap is an API to surface this information.

### Two Audiences, Two Levels of Detail

| Audience | Needs | Example Questions |
|----------|-------|-------------------|
| **API consumers** | Stage-level status and timing | "Why did my job fail?" / "Why is it slow?" / "Was diarization skipped?" |
| **Operators / power users** | Full artifact inspection | "What segments did the transcriber produce?" / "What was the diarizer's input?" / "Why did alignment degrade?" |

These map to two distinct features:

1. **Stage breakdown** — a summary array on the job status response showing each stage's status, duration, engine, and error. Lightweight, always available, useful to everyone.

2. **Artifact inspection** — an endpoint to retrieve the raw input/output JSON for a specific task. Heavier, on-demand, primarily for debugging and advanced integration.

### Why Both Matter

**Stage breakdown** is not a nice-to-have — it is the minimum information needed to operate against an async multi-stage API. Without it, users treat the system as a black box and file support tickets for issues they could diagnose themselves. Every mature pipeline API (CI systems, data pipelines, media encoding services) provides step-level visibility.

**Artifact inspection** may seem admin-only at first, but it has real value for power users building on the API:

- A user who runs diarization and gets unexpected speaker assignments wants to see the raw diarization output to determine if the problem is in their audio or the model.
- A user comparing transcription quality across models wants to see intermediate outputs without running separate jobs.
- An operator troubleshooting a user's job needs the same data the engines saw.

The design separates these cleanly: stage breakdown is always embedded in the job response, while artifact inspection is a distinct endpoint that requires deliberate access.

### Non-Goals

This spec covers **user-facing and operator-facing observability through the API**. The following are out of scope:

- **Operational metrics** (Prometheus, Grafana dashboards) — separate concern, tracked by engine and orchestrator metrics
- **Distributed tracing** (OpenTelemetry spans) — valuable but orthogonal infrastructure work
- **Log aggregation** — structured logging improvements are independent of this feature
- **Real-time session observability** — streaming sessions have different data flows; this spec covers batch pipeline only

---

## Tactical

### Feature 1: Stage Breakdown in Job Status

Extend the existing `GET /v1/audio/transcriptions/{id}` response to include a `stages` array when the job has been expanded into tasks.

#### Response Schema Addition

The `stages` field appears on any job that has reached `RUNNING` status (i.e., the orchestrator has built its task DAG).

```json
{
  "id": "job_abc123",
  "status": "completed",
  "created_at": "2025-01-28T12:00:00Z",
  "completed_at": "2025-01-28T12:02:30Z",

  "stages": [
    {
      "stage": "prepare",
      "task_id": "550e8400-e29b-41d4-a716-446655440001",
      "engine_id": "audio-prepare",
      "status": "completed",
      "required": true,
      "started_at": "2025-01-28T12:00:01Z",
      "completed_at": "2025-01-28T12:00:02Z",
      "duration_ms": 1200,
      "error": null
    },
    {
      "stage": "transcribe",
      "task_id": "550e8400-e29b-41d4-a716-446655440002",
      "engine_id": "faster-whisper",
      "status": "completed",
      "required": true,
      "started_at": "2025-01-28T12:00:02Z",
      "completed_at": "2025-01-28T12:00:10Z",
      "duration_ms": 8400,
      "error": null
    },
    {
      "stage": "diarize",
      "task_id": "550e8400-e29b-41d4-a716-446655440004",
      "engine_id": "pyannote-4.0",
      "status": "failed",
      "required": false,
      "started_at": "2025-01-28T12:00:10Z",
      "completed_at": "2025-01-28T12:00:13Z",
      "duration_ms": 3100,
      "retries": 2,
      "error": "Too many speakers detected (>20)"
    },
    {
      "stage": "merge",
      "task_id": "550e8400-e29b-41d4-a716-446655440005",
      "engine_id": "final-merger",
      "status": "completed",
      "required": true,
      "started_at": "2025-01-28T12:00:13Z",
      "completed_at": "2025-01-28T12:00:14Z",
      "duration_ms": 800,
      "error": null
    }
  ],

  "text": "...",
  "segments": [...]
}
```

#### Stage Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `stage` | string | Pipeline stage name (`prepare`, `transcribe`, `align`, `diarize`, `merge`, etc.) |
| `task_id` | string | UUID of the underlying task (used for artifact inspection) |
| `engine_id` | string | Engine that executed (or will execute) this task |
| `status` | string | `pending`, `ready`, `running`, `completed`, `failed`, `skipped` |
| `required` | boolean | Whether this stage was required for job success |
| `ready_at` | string | ISO 8601 timestamp when task became ready / enqueued (null if not yet ready) |
| `started_at` | string | ISO 8601 timestamp when execution began (null if not started) |
| `completed_at` | string | ISO 8601 timestamp when execution finished (null if not finished) |
| `wait_ms` | integer | Queue wait time in milliseconds: `ready_at` to `started_at` (null if not started) |
| `duration_ms` | integer | Processing duration in milliseconds: `started_at` to `completed_at` (null if not finished) |
| `retries` | integer | Number of retries attempted (omitted if 0) |
| `error` | string | Error message if failed (null otherwise) |

#### Per-Channel Stages

For `per_channel` speaker detection, channel-specific tasks appear with a suffix:

```json
{
  "stages": [
    {"stage": "prepare", "status": "completed", "duration_ms": 900},
    {"stage": "transcribe_ch0", "status": "completed", "duration_ms": 4200},
    {"stage": "transcribe_ch1", "status": "completed", "duration_ms": 3800},
    {"stage": "align_ch0", "status": "completed", "duration_ms": 2100},
    {"stage": "align_ch1", "status": "completed", "duration_ms": 1900},
    {"stage": "merge", "status": "completed", "duration_ms": 600}
  ]
}
```

#### Ordering

Stages are returned in **execution order** (topological sort of the DAG). Stages at the same dependency level (parallel tasks) are ordered alphabetically.

#### ElevenLabs-Compatible Endpoint

The `GET /v1/speech-to-text/transcripts/{transcription_id}` endpoint does **not** include the `stages` array. Stage breakdown is a Dalston-native extension. The ElevenLabs response shape remains unchanged.

---

### Feature 2: Task Artifact Inspection

A new endpoint to retrieve the raw input and output artifacts for a specific task within a job.

#### Endpoint

```
GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts
```

#### Authentication & Authorization

- Requires `jobs:read` scope (same as reading job status)
- Tenant-isolated: task must belong to a job owned by the requesting tenant
- No additional scope required — artifact data is derived from processing the user's own audio

#### Response

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440002",
  "job_id": "job_abc123",
  "stage": "transcribe",
  "engine_id": "faster-whisper",
  "status": "completed",

  "input": {
    "audio_uri": "s3://dalston-artifacts/jobs/job_abc123/audio/prepared.wav",
    "previous_responses": {
      "prepare": {
        "duration": 150.5,
        "channels": 1,
        "sample_rate": 16000
      }
    },
    "config": {
      "model": "large-v3",
      "language": "auto",
      "beam_size": 5,
      "vad_filter": true
    }
  },

  "output": {
    "data": {
      "text": "Welcome to the show. Thanks for having me...",
      "segments": [
        {
          "start": 0.0,
          "end": 3.5,
          "text": "Welcome to the show.",
          "words": [
            {"word": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98}
          ]
        }
      ],
      "language": "en",
      "language_confidence": 0.98
    }
  }
}
```

#### Schema

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Task UUID |
| `job_id` | string | Parent job ID |
| `stage` | string | Pipeline stage |
| `engine_id` | string | Engine that processed this task |
| `status` | string | Task status |
| `input` | object | Task input as passed to the engine (from `request.json` in S3) |
| `input.audio_uri` | string | S3 URI of the audio file this task received |
| `input.previous_responses` | object | Outputs from upstream stages, keyed by stage name |
| `input.config` | object | Engine-specific configuration parameters |
| `output` | object / null | Task output (from `response.json` in S3). Null if task has not completed. |
| `output.data` | object | The engine's result data |

#### Error Responses

| Status | Condition | Response |
|--------|-----------|----------|
| 404 | Job not found or wrong tenant | `{"error": {"code": "job_not_found", "message": "Job not found"}}` |
| 404 | Task not found or not in this job | `{"error": {"code": "task_not_found", "message": "Task not found"}}` |
| 400 | Task has no artifacts yet (pending) | `{"error": {"code": "no_artifacts", "message": "Task has not started yet"}}` |

#### For Failed Tasks

When a task has failed, the `output` field is null, and the error is available in the stage breakdown. The `input` field is still returned so the operator can see what was passed to the engine:

```json
{
  "task_id": "...",
  "stage": "diarize",
  "status": "failed",
  "input": {
    "previous_responses": {
      "transcribe": {"segments": [...]},
      "align": {"segments": [...]}
    },
    "config": {"num_speakers": null}
  },
  "output": null
}
```

---

### Feature 3: Task List Endpoint

A convenience endpoint to list all tasks for a job without fetching full artifacts.

#### Endpoint

```
GET /v1/audio/transcriptions/{job_id}/tasks
```

#### Response

```json
{
  "job_id": "job_abc123",
  "tasks": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440001",
      "stage": "prepare",
      "engine_id": "audio-prepare",
      "status": "completed",
      "required": true,
      "dependencies": [],
      "started_at": "2025-01-28T12:00:01Z",
      "completed_at": "2025-01-28T12:00:02Z",
      "duration_ms": 1200,
      "retries": 0,
      "error": null
    },
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440002",
      "stage": "transcribe",
      "engine_id": "faster-whisper",
      "status": "completed",
      "required": true,
      "dependencies": ["550e8400-e29b-41d4-a716-446655440001"],
      "started_at": "2025-01-28T12:00:02Z",
      "completed_at": "2025-01-28T12:00:10Z",
      "duration_ms": 8400,
      "retries": 0,
      "error": null
    }
  ]
}
```

This endpoint returns the full dependency graph, useful for rendering a pipeline visualization in the console UI.

#### Authentication

Same as job status: `jobs:read` scope, tenant-isolated.

---

### Console Integration

The web console (`web/`) should use these endpoints to provide a pipeline drill-down view.

#### Job Detail Page

When viewing a completed or failed job, the DAG viewer shows:

**Timing breakdown** (top-level summary):

- **Wall**: Total time from job creation to completion (what the user waited)
- **Processing**: Sum of all tasks' `duration_ms` (actual engine work)
- **Wait**: Sum of all tasks' `wait_ms` (time queued before engine pickup)
- **Speed ratio**: `audio_duration / processing_time` (uses processing time, not wall time)

**Per-task nodes** show processing duration and queue wait (if > 500ms).

```
┌─────────────────────────────────────────────────────────────────────┐
│  Wall: 14.5s | Processing: 13.5s (6.7x) | Wait: 1.0s              │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐       │
│  │ prepare  │─▶│  transcribe  │─▶│ diarize  │─▶│  merge   │       │
│  │  1.2s ✓  │  │   8.4s ✓     │  │ 3.1s ✗   │  │  0.8s ✓  │       │
│  └──────────┘  └──────────────┘  └──────────┘  └──────────┘       │
│                                                                     │
│  Click any stage to inspect its input and output artifacts.        │
└─────────────────────────────────────────────────────────────────────┘
```

#### Stage Detail Panel

Clicking a stage opens a detail panel showing:

- Stage metadata (engine, duration, retries, error)
- **Input tab**: engine config, previous stage outputs passed as context
- **Output tab**: raw output JSON from the engine (segments, text, speaker data, etc.)

This reuses the artifact inspection endpoint and renders the JSON in a structured viewer.

---

### Artifact Retention

Task artifacts in S3 follow the same lifecycle as the job they belong to:

| Event | Behavior |
|-------|----------|
| Job completed | Artifacts retained (same as today) |
| Job deleted via API | All task artifacts deleted along with job data (see [JOB_DELETION.md](batch/JOB_DELETION.md)) |
| Retention policy (future) | When tenant-level retention is implemented, task artifacts expire with the job |

No additional retention logic is needed — artifacts are already stored under `s3://{bucket}/jobs/{job_id}/tasks/{task_id}/` and are naturally scoped to the job.

---

## Plan

### Data Model Changes

No schema migrations are required. The existing `tasks` table already contains all fields needed for the stage breakdown. The `request_uri` and `response_uri` columns point to S3 objects containing the full artifact data.

One minor addition to support clean stage naming for per-channel pipelines:

| File | Change |
|------|--------|
| `dalston/common/models.py` | Add `display_stage` property to Task model (returns `transcribe_ch0` for channel tasks) |

### Files to Create

| File | Purpose |
|------|---------|
| `dalston/gateway/api/v1/tasks.py` | New router for task list and artifact endpoints |
| `dalston/gateway/models/responses.py` | Add `StageResponse`, `TaskListResponse`, `TaskArtifactResponse` models |
| `web/src/pages/JobDetail.tsx` | Pipeline timeline component (or extend existing job detail) |
| `web/src/components/ArtifactViewer.tsx` | JSON viewer for task input/output |

### Files to Modify

| File | Change |
|------|--------|
| `dalston/gateway/api/v1/transcription.py` | Include `stages` in job status response |
| `dalston/gateway/services/jobs.py` | Add `get_job_tasks()` and `get_task_artifacts()` service methods |
| `dalston/gateway/services/storage.py` | Add `get_task_request()` and `get_task_response()` to fetch from S3 |
| `dalston/gateway/main.py` | Register tasks router |
| `dalston/gateway/api/console.py` | Add task list and artifact endpoints for console API |
| `web/src/api/client.ts` | Add `getJobTasks()` and `getTaskArtifacts()` methods |
| `web/src/pages/BatchJobs.tsx` | Link job rows to detail page |

### Implementation Tasks

- [ ] Add `StageResponse` Pydantic model with stage, task_id, engine_id, status, timing, error fields
- [ ] Add `TaskListResponse` and `TaskArtifactResponse` Pydantic models
- [ ] Add `get_job_tasks(job_id, tenant_id)` to jobs service — queries tasks table, returns ordered list
- [ ] Add `get_task_artifacts(job_id, task_id, tenant_id)` to storage service — fetches request.json and response.json from S3
- [ ] Modify job status endpoint to include `stages` array (query tasks, map to StageResponse)
- [ ] Create `GET /v1/audio/transcriptions/{job_id}/tasks` endpoint
- [ ] Create `GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts` endpoint
- [ ] Add console API equivalents: `GET /jobs/{job_id}/tasks` and `GET /jobs/{job_id}/tasks/{task_id}/artifacts`
- [ ] Register new router in gateway main
- [ ] Add unit tests for stage breakdown in job status response
- [ ] Add unit tests for task list endpoint
- [ ] Add unit tests for artifact endpoint (completed task, failed task, pending task)
- [ ] Add integration test: submit job, wait for completion, verify stages array matches expected pipeline
- [ ] Add web API client methods
- [ ] Build pipeline timeline component in console
- [ ] Build artifact viewer component in console

### Verification

1. **Stage breakdown**: Submit a job with diarization. Poll status. Verify `stages` array appears once job is RUNNING, with correct stages in DAG order. Verify timing and status update as stages complete.
2. **Failed stage visibility**: Submit a job that triggers an optional stage failure (e.g., diarization with bad audio). Verify the failed stage shows in the breakdown with error message and `skipped` status.
3. **Artifact inspection**: After job completion, call artifact endpoint for the transcribe task. Verify `input` contains the audio URI and config, and `output` contains segments and text.
4. **Pending task artifacts**: Call artifact endpoint for a task that hasn't started. Verify 400 response.
5. **Tenant isolation**: Attempt to access tasks from a job belonging to a different tenant. Verify 404 response.
6. **Per-channel**: Submit a per-channel job. Verify stage names include channel suffixes (`transcribe_ch0`, `transcribe_ch1`).
7. **Console**: Navigate to a completed job in the web console. Verify pipeline timeline renders. Click a stage. Verify artifact viewer shows input and output.

---

## M54 Durable Event DLQ Observability

M54 adds explicit durable-event reliability visibility for orchestrator Redis Stream
consumption.

### Metrics

- `dalston_orchestrator_event_decisions_total{decision,event_type,failure_reason}`
  - `decision="ack"`: successfully dispatched and ACKed
  - `decision="retry"`: retryable failure below delivery threshold (left pending)
  - `decision="dlq"`: quarantined into DLQ (non-retryable or max deliveries exceeded)

### Structured Logs

Durable event decision logs include:

- `message_id`
- `event_type`
- `delivery_count`
- `source` (`live_consumer` or `crash_recovery`)
- `decision`
- `failure_reason` (for retry/dlq)
- `error` (when present)

### Operational DLQ Runbook

```bash
# Newest DLQ entries first
redis-cli XREVRANGE dalston:events:dlq + - COUNT 20

# Oldest DLQ entries first
redis-cli XRANGE dalston:events:dlq - + COUNT 20

# Durable stream pending summary
redis-cli XPENDING dalston:events:stream orchestrators
```

DLQ entries include source message id, failure reason, delivery count, consumer id,
raw fields/payload, and parseable payload (when available). M54 does not include
automatic DLQ replay; replay is manual/operator-driven only.

---

## M59 Runtime Isolation Profile Observability

M59 adds explicit engine-ID isolation visibility across engine status and
Prometheus metrics so operators can tell whether an engine is expected to run
`inproc`, in an isolated `venv`, or as a long-running `container` worker.

### Status Surface

`GET /v1/engines` now returns `execution_profile` for every catalog engine_id.
This is the catalog-declared routing policy used by the orchestrator and lite
pipeline:

- `inproc`: execute inside the current process through `InProcExecutor`
- `venv`: execute in a dedicated Python virtual environment through
  `VenvExecutor`
- `container`: execute through the existing distributed Redis stream + engine
  worker path

Example:

```json
{
  "id": "nemo-msdd",
  "stage": "diarize",
  "version": "1.0.0",
  "execution_profile": "venv",
  "status": "available"
}
```

### Metrics

The following metrics now carry an `execution_profile` label:

- `dalston_orchestrator_tasks_scheduled_total{engine_id,stage,execution_profile}`
- `dalston_orchestrator_tasks_completed_total{engine_id,status,execution_profile}`
- `dalston_engine_tasks_processed_total{engine_id,model,status,execution_profile}`
- `dalston_engine_task_duration_seconds{engine_id,model,execution_profile}`
- `dalston_engine_queue_wait_seconds{engine_id,execution_profile}`
- `dalston_engine_s3_download_seconds{engine_id,execution_profile}`
- `dalston_engine_s3_upload_seconds{engine_id,execution_profile}`

This enables dashboards and alerts such as:

- isolate failing `venv` engine IDs without mixing them with container workers
- confirm `inproc` lite stages are staying on the local execution path
- detect unexpected `container` queue growth for engine IDs meant to stay local

### Structured Logs

Relevant logs should include the execution profile at the dispatch boundary:

- lite pipeline executor selection (`stage_started` metadata includes
  `execution_profile`)
- engine runner startup (`engine_loop_starting`)
- distributed queue metadata (`execution_profile` stored with task metadata)

### Runtime Profile Runbook

1. Confirm the expected policy:

```bash
curl -s http://localhost:8000/v1/engines | jq '.engines[] | {id, stage, execution_profile, status}'
```

2. Confirm the engine ID is emitting the expected metric labels:

```bash
curl -s http://localhost:9100/metrics | rg 'execution_profile='
```

3. If a `container` worker refuses to start with an error like:

```text
Runtime 'nemo-msdd' declares execution_profile 'venv' and cannot start as a distributed container worker.
```

stop deploying that engine as a long-running engine worker. The catalog is
declaring it as local-only (`venv`/`inproc`), so it must run through the lite
executor path instead.

4. If lite execution fails with a missing-executor or venv boot error:

- verify the engine `engine.yaml` has the intended `execution_profile`
- verify the catalog was regenerated after editing engine metadata
- verify the target virtualenv/interpreter is healthy for the engine
- rerun the failing stage with focused tests before retrying full flows

5. If status and worker behavior disagree:

- regenerate the catalog: `python scripts/generate_catalog.py`
- restart the gateway/orchestrator/worker processes
- check for stale engine registrations in Redis before retrying
