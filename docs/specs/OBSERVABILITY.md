# Task-level observability

Dalston exposes job-stage status, task dependency/timing data, raw task
request/response artifacts, live engine status, metrics, and structured logs.
Use these surfaces together: the job response is the customer result, while
task/artifact data explains how it was produced.

## Job stage summary

`GET /v1/audio/transcriptions/{job_id}` includes a `stages` array derived from
the job's tasks. A stage entry can include task/engine identity, status,
required/optional state, timing, retries, and error.

Parallel branches can overlap, so stage durations must not simply be summed to
calculate wall-clock processing time. The control plane computes wait and
processing intervals with overlap-aware logic.

## Task API

```http
GET /v1/audio/transcriptions/{job_id}/tasks
```

Returns the authorized job ID and tasks in topological order. Each task
includes:

- `task_id`, `stage`, and `engine_id`;
- status and whether it is required;
- dependency task IDs;
- start/completion timestamps and `duration_ms`;
- retry count and error.

Per-channel jobs have multiple transcription/alignment tasks. Consumers should
use task IDs and dependencies rather than assuming one task per stage.

```http
GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts
```

Returns raw materialized `request` and `response` JSON plus task identity and
status. A pending task returns `400` because no artifact exists yet. Missing or
unauthorized jobs/tasks return `404`.

Artifacts can be absent after retention purge or when a task failed before
materialization. Treat `null` request/response as operational evidence, not a
schema-valid engine output.

The console exposes equivalent aggregation routes under
`/api/console/jobs/{job_id}/tasks` and
`/api/console/jobs/{job_id}/tasks/{task_id}/artifacts`.

## Console

The queue board shows live job/task organization in grid, stage-board, and
job-strips views. Job detail displays the pipeline and result; task detail
shows dependencies, retries, timing, and raw artifacts. There is normally no
distributed merge task: final assembly is orchestrator completion logic.

Use the console for interactive diagnosis and the native APIs for automation.

## Engine and runtime profile visibility

`GET /v1/engines` combines the authored catalog with live registry heartbeats.
It includes `execution_profile` (`inproc`, `venv`, or `container`), status,
loaded/available models, capabilities, hardware metadata, and performance
planning metadata.

`GET /v1/engines/capabilities` summarizes currently available stages.
`GET /v1/realtime/status` and `/workers` expose realtime capacity.

Runtime-profile labels are carried on orchestrator/engine metrics so operators
can distinguish local in-process/venv execution from distributed containers.

## Metrics and logs

Prometheus metrics are served from `/metrics` when enabled. Relevant families
cover:

- API request count/latency;
- job/task state and queue wait;
- engine processing duration and storage transfer;
- orchestrator scheduling/completion by stage, engine, and execution profile;
- realtime workers, capacity, active sessions, lag, and termination;
- durable-event decisions.

Structured logs carry correlation, job, task, session, engine, model, stage,
and execution-profile fields where available. Use the correlation ID to join
gateway and downstream events for one request.

Metrics are operational aggregates and may have label-cardinality constraints;
do not add raw job/task IDs as Prometheus labels.

## Durable-event DLQ

The orchestrator applies a delivery ceiling to durable Redis Stream events.
After `DALSTON_EVENTS_MAX_DELIVERIES` (default 5), poison events are copied to
`dalston:events:dlq` by default and acknowledged from the source stream. The
DLQ is capped approximately by `DALSTON_EVENTS_DLQ_MAXLEN` (default 10,000).

Decision metrics/logs identify `ack`, `retry`, or `dlq`, plus event type,
delivery count, source, and failure reason.

Operational inspection:

```bash
redis-cli XREVRANGE dalston:events:dlq + - COUNT 20
redis-cli XRANGE dalston:events:dlq - + COUNT 20
redis-cli XPENDING dalston:events:stream orchestrators
```

There is no automatic DLQ replay. Inspect and correct the cause before manually
re-emitting an event; blind replay can recreate the poison-pill loop.

## Retention and security

Task artifacts follow the owning job's storage/retention lifecycle. Explicit
job deletion removes them; scheduled purge can make them unavailable while
audit history remains.

Task artifacts can contain raw transcript text, model inputs, PII, and storage
references. Access is tenant-scoped and requires job-read permission. Do not
publish them in logs or metrics, and avoid sharing presigned URLs.

## Troubleshooting sequence

1. Read job status, current stage, and error.
2. List tasks and identify pending/failed dependencies.
3. Inspect the failed task artifact when present.
4. Check the matching engine in `/v1/engines` and its logs.
5. Check stream backlog/consumer state and orchestrator logs.
6. Use metrics to determine whether the issue is isolated or systemic.
7. Inspect the durable-event DLQ when state changes are missing.
