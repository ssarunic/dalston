# Orchestrator

The orchestrator expands batch jobs into task DAGs, selects engines, schedules
distributed work, assembles transcripts, coordinates realtime capacity, and
runs lifecycle workers such as webhook delivery and retention cleanup.

## Entrypoints

- `dalston/orchestrator/main.py` selects the configured runtime mode.
- `dalston/orchestrator/lite_main.py` runs the lite execution path.
- `dalston/orchestrator/dag.py` builds distributed task DAGs.
- `dalston/orchestrator/handlers.py` advances jobs and assembles results.

## Distributed dispatch

Jobs and tasks are durable PostgreSQL records. Redis provides:

- `dalston:events:stream` for durable lifecycle events;
- one work stream per selected engine instance;
- engine/worker registry state and heartbeats; and
- rate-limit/session coordination state.

S3-compatible storage holds source audio and task/final artifacts.

Tasks move through:

```text
pending → ready → running → completed
                       ├──→ failed
                       ├──→ skipped
                       └──→ cancelled
```

Tasks start as `pending` when they have dependencies and `ready` when all
dependencies are satisfied. A ready task is assigned to a compatible engine
and dispatched to its stream.

## Engine selection

Selection uses current registry state plus catalog capabilities:

- supported stages and modes;
- loaded/available models;
- language support and language-forcing support;
- native word timestamps;
- included diarization;
- hardware/readiness/capacity; and
- requested model preference.

An engine’s name is not used as a substitute for capability declarations.
When no ready engine can satisfy a required stage, the job reports which
capability is unavailable.

## Current distributed DAGs

### Mono, no diarization

```text
prepare → transcribe → optional align
```

Align is included only for word-level requests when the selected transcriber
does not advertise native word timestamps.

### Mono with diarization

```text
prepare ──► transcribe ──► optional align
   │
   └─────────────────────► diarize
```

Diarization consumes prepared audio, so it depends on `prepare`, not
`transcribe` or `align`. The two branches execute in parallel.

If the transcriber advertises `includes_diarization`, the separate diarize task
is omitted.

### Per-channel

```text
prepare (split)
  ├──► transcribe_ch0 ──► optional align_ch0
  └──► transcribe_ch1 ──► optional align_ch1
```

The number of channel branches is derived from prepared media/channel
configuration.

### Completion

There is no distributed merge task. When all required terminal tasks finish,
the orchestrator:

1. selects the transcription or alignment response for each branch;
2. applies diarization turns when present;
3. assembles mono or per-channel transcript segments;
4. preserves language provenance, confidence, timestamp availability, and
   pipeline warnings;
5. writes the final transcript artifact; and
6. marks the job complete and publishes completion events.

Legacy merge contracts remain for lite/compatibility code but are not scheduled
by `dag.py`.

## PII post-processing

PII work is not part of the core completion DAG. After transcript assembly:

```text
transcript → optional pii_detect → optional audio_redact
```

PII detection can produce redacted text and timed entity metadata. Audio
redaction requires timed entities and retained audio. Post-processing state and
artifacts become available asynchronously.

## Task input/output contract

Every engine receives a `TaskRequest` containing:

- task/job/stage identifiers;
- validated stage configuration;
- optional payload;
- earlier responses keyed by stage;
- materialized local artifacts; and
- tracing metadata.

It returns `TaskResponse(data=..., produced_artifacts=[...])`. The runner owns
object-storage downloads/uploads and event publication.

## Execution profiles

Catalog entries declare one profile:

| Profile | Execution |
| --- | --- |
| `container` | Long-running distributed worker consuming Redis streams |
| `venv` | Lite subprocess in an engine-specific virtual environment |
| `inproc` | Lite execution inside the orchestrator process |

The same typed request/response envelope is used across profiles. Failure does
not silently fall back to a different profile.

## Failure and recovery

- Required prepare/transcribe failure fails the job after configured retries.
- Optional align/diarize failure may yield a degraded transcript with warnings,
  depending on task policy.
- Heartbeats and stale-task scanning recover work abandoned by dead workers.
- Reconciliation repairs divergence between durable database state and Redis
  delivery state.
- Cancellation moves the job through `cancelling` to `cancelled` and prevents
  further useful task dispatch.

## Durable event reliability

Lifecycle events are consumed from `dalston:events:stream` by the
`orchestrators` consumer group. Successfully handled events are acknowledged.
Handler and dispatch failures remain pending for retry until their delivery
count reaches `DALSTON_EVENTS_MAX_DELIVERIES` (default `5`).

Malformed payloads, invalid schemas, and unknown event types are
non-retryable. They are quarantined immediately in the dead-letter stream.
Retryable events that reach the delivery ceiling are quarantined there too.
The transfer deliberately writes the DLQ entry before acknowledging the source
event (`XADD` then `XACK`), favoring duplicate DLQ entries over event loss.

| Setting | Default | Purpose |
| --- | --- | --- |
| `DALSTON_EVENTS_MAX_DELIVERIES` | `5` | Delivery ceiling before quarantine |
| `DALSTON_EVENTS_DLQ_STREAM` | `dalston:events:dlq` | Dead-letter stream key |
| `DALSTON_EVENTS_DLQ_MAXLEN` | `10000` | Approximate bounded DLQ length |

DLQ entries preserve the source stream/group/message ID, event type, failure
reason, error, delivery count, consumer ID, failure time, and available
payload/raw fields. There is no automatic DLQ replay loop. Inspect the newest
entries and current primary-stream backlog with:

```bash
redis-cli XREVRANGE dalston:events:dlq + - COUNT 20
redis-cli XPENDING dalston:events:stream orchestrators
```

## Observability

Job detail exposes task stage, engine, status, dependencies, attempts, timing,
errors, and artifacts. Queue-wait and processing totals use interval unions so
parallel branches are not double-counted.

Trace context propagates from gateway request to lifecycle event and engine
task. See [Observability](../OBSERVABILITY.md).

## Tests as executable examples

- `tests/unit/test_dag.py`
- `tests/integration/test_linear_pipeline.py`
- `tests/integration/test_speaker_detection.py`
- `tests/integration/test_per_channel.py`
- `tests/unit/test_transcript_assembly.py`

These tests are the source of truth for dependency edges and transcript
assembly behavior.
