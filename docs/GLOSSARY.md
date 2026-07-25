# Glossary

## Core concepts

| Term | Definition |
| --- | --- |
| **Job** | A batch transcription request for one audio input. The orchestrator expands it into a task DAG. |
| **Task** | One scheduled unit of pipeline work assigned to an engine. |
| **DAG** | Directed acyclic graph describing task dependencies within a job. |
| **Stage** | A processing category: `prepare`, `transcribe`, `align`, `diarize`, `pii_detect`, or `audio_redact`. |
| **Engine** | A registered runtime that implements one or more batch/realtime capabilities. Unified engines may serve both interfaces with shared model state. |
| **Artifact** | Stored or materialized input/output such as audio, a task response, transcript, redacted audio, or export. |
| **Tenant** | Isolation boundary for jobs, API keys, sessions, webhooks, and artifacts. |

## Batch processing

| Term | Definition |
| --- | --- |
| **Orchestrator** | Consumes job events, selects engines, constructs/schedules task DAGs, assembles transcripts, and manages lifecycle work. |
| **Engine stream** | Redis Stream used to dispatch distributed tasks to a compatible engine instance. |
| **Transcript assembly** | Orchestrator logic that combines transcription/alignment results with speaker turns. It is not a distributed merge task. |
| **Per-channel** | Speaker mode where each input channel is transcribed independently and treated as a speaker. |
| **Post-processing** | Optional `pii_detect` and `audio_redact` work that runs after core transcript assembly. |

## Realtime processing

| Term | Definition |
| --- | --- |
| **Session** | One realtime transcription connection and its lifecycle record where the protocol supports persistence. |
| **Realtime worker** | An engine instance with available realtime capacity. |
| **Session coordinator** | Orchestrator component that selects workers, allocates/releases capacity, and reconciles realtime sessions. Older documents called this the Session Router. |
| **VAD** | Voice activity detection, used to identify speech boundaries. |
| **Partial transcript** | Interim text that may change. |
| **Final transcript** | Committed text for an utterance. |
| **Resume linkage** | Native `resume_session_id` lineage between sessions. It does not currently restore transcript/decoder context. |

Realtime completion does not automatically create a batch enhancement job.
Applications that want post-session diarization must retain the recording and
submit a separate batch request.

## APIs

| Term | Definition |
| --- | --- |
| **Dalston native API** | Native REST and WebSocket endpoints under `/v1/audio/transcriptions`. |
| **ElevenLabs-compatible API** | Compatibility endpoints under `/v1/speech-to-text`. |
| **OpenAI-compatible API** | OpenAI audio and realtime compatibility endpoints. |
| **Webhook** | Standard Webhooks-formatted callback for transcription completion, failure, or cancellation. |

## Observability

| Term | Definition |
| --- | --- |
| **Correlation ID** | Request identifier propagated across service boundaries. |
| **Span** | One timed unit in an OpenTelemetry trace. |
| **Trace** | Related spans representing a request/job lifecycle. |
| **Metric** | Counter, histogram, or gauge collected for operational analysis. |

## Pipeline stages

| Stage | Purpose |
| --- | --- |
| `prepare` | Probe and normalize audio; optionally split channels. |
| `transcribe` | Produce text and timed segments, optionally words. |
| `align` | Refine segment timing to word timing when the transcriber lacks it. |
| `diarize` | Produce speaker turns. Runs from prepared audio in parallel with transcription/alignment. |
| `pii_detect` | Detect configured PII entities in the assembled transcript. |
| `audio_redact` | Produce silenced or beeped audio from timed PII entities. |

Legacy merge/refine contracts may remain in compatibility code, but they are
not stages in the current distributed DAG.
