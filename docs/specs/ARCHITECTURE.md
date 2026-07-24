# Dalston architecture

Dalston is a self-hosted batch and realtime speech-to-text platform with
Dalston-native, ElevenLabs-compatible, and OpenAI-compatible APIs.

## Runtime modes

| Mode | Persistence and scheduling | Typical use |
| --- | --- | --- |
| `distributed` | PostgreSQL, Redis Streams, S3/MinIO, container workers | Multi-node and production deployments |
| `lite` | SQLite, in-memory scheduling, local artifacts, in-process/venv engines | Local and zero-config workflows |

`DALSTON_MODE` is selected at startup. The application does not silently fall
back between modes.

## Components

```text
                       HTTP / WebSocket
                              │
                       ┌──────▼──────┐
                       │   Gateway   │
                       │ API + SPA   │
                       └───┬─────┬───┘
                           │     │ realtime proxy
                 job event │     │
                    ┌──────▼─────▼──────────┐
                    │     Orchestrator       │
                    │ DAG + session          │
                    │ coordination + assembly│
                    └───┬───────────────┬────┘
                        │               │
               task streams       worker registry
                        │               │
                 ┌──────▼───────────────▼─────┐
                 │ Unified engine instances   │
                 │ batch and/or realtime      │
                 └────────────────────────────┘

    PostgreSQL/SQLite       Redis/in-memory       S3/local filesystem
    durable metadata       queues + registry     audio + artifacts
```

### Gateway

The FastAPI gateway handles authentication, request validation, uploads,
REST/WebSocket compatibility translation, rate limits, the console API, and
serving the React application.

Primary surfaces:

- native batch: `/v1/audio/transcriptions`
- native realtime: `/v1/audio/transcriptions/stream`
- ElevenLabs: `/v1/speech-to-text`
- ElevenLabs realtime: `/v1/speech-to-text/realtime`
- OpenAI audio: `/v1/audio/transcriptions`, `/v1/audio/translations`
- OpenAI realtime: `/v1/realtime?intent=transcription`

The native and OpenAI batch routes share a path and are distinguished by the
request contract/model handling.

### Orchestrator

The orchestrator:

- consumes durable job events;
- selects engines by capability and readiness;
- constructs and schedules task DAGs;
- retries/reconciles stale work;
- assembles the final transcript;
- coordinates realtime worker allocation and capacity;
- delivers webhooks and applies retention cleanup; and
- runs optional PII/audio-redaction post-processing.

Realtime coordination is implemented by the orchestrator’s session
coordinator/allocator. The former standalone “Session Router” architecture is
legacy.

### Engines

An engine advertises stages, models, timing/speaker/language capabilities,
hardware requirements, and whether it supports batch and/or native streaming.
Unified runners can share a loaded model between batch and realtime adapters.

Execution profiles are:

- `container`: distributed Redis-stream worker;
- `venv`: isolated lite subprocess; and
- `inproc`: lite execution in the application process.

## Batch data flow

```text
submit
  │
  ▼
prepare ──► transcribe ──► optional align
  │
  └─────────────────────► optional diarize
                              │
                 all required tasks terminal
                              │
                              ▼
                 orchestrator transcript assembly
                              │
                              ▼
                  optional pii_detect/audio_redact
```

Diarization depends on prepared audio and runs in parallel with the
transcription/alignment branch. Capability flags can remove align or diarize
when an upstream engine already supplies that output.

There is no distributed merge task. Mono and per-channel transcripts are
assembled by orchestrator code. Legacy merge types and lite-profile logic are
retained only where compatibility requires them.

## Realtime data flow

1. Gateway authenticates the WebSocket and checks tenant limits.
2. The session coordinator chooses a compatible ready worker.
3. Gateway proxies native or translated protocol messages to that worker.
4. Worker emits VAD, interim, final, warning, and session lifecycle messages.
5. Coordinator releases capacity and the gateway persists native session data
   when retention permits.

Compatibility model names are validated but do not imply a fixed model-to-
engine mapping. Native `resume_session_id` records lineage without restoring
transcript or decoder context.

Realtime completion does not automatically launch batch enhancement.

## Storage

| Data | Distributed | Lite |
| --- | --- | --- |
| Jobs, tasks, keys, sessions, webhooks, audit | PostgreSQL | SQLite |
| Events, queues, registry, rate limits | Redis | In-memory services |
| Audio, task outputs, transcripts, exports | S3/MinIO | Local filesystem |
| Model cache | Shared/worker volume or configured model storage | Local cache |

Engine business logic receives materialized local files and returns typed
responses/artifact descriptors. Runners own storage I/O.

## Reliability and observability

- Redis consumer groups provide durable distributed delivery.
- Heartbeats and stale-task scanners recover abandoned work.
- PostgreSQL/SQLite remain the durable lifecycle source.
- OpenTelemetry propagates request/job/task trace context.
- Structured logs and Prometheus metrics expose queue, worker, engine, and
  latency state.
- Webhooks use Standard Webhooks signatures and retry delivery.

## Further reference

- [Batch API](batch/API.md)
- [Orchestrator](batch/ORCHESTRATOR.md)
- [Engine contracts](PIPELINE_INTERFACES.md)
- [Realtime guide](realtime/REALTIME.md)
- [WebSocket API](realtime/WEBSOCKET_API.md)
- [Data retention](DATA_RETENTION.md)
- [Observability](OBSERVABILITY.md)
- [Glossary](../GLOSSARY.md)
