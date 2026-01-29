# Glossary

Terminology used throughout Dalston documentation.

## Core Concepts

| Term | Definition |
| --- | --- |
| **Job** | A batch request to transcribe one audio file. Jobs are expanded into task DAGs by the Orchestrator. |
| **Task** | An atomic unit of work in the batch pipeline. Each task runs on a specific engine and may depend on other tasks. |
| **DAG** | Directed Acyclic Graph — the dependency structure of tasks within a job. Tasks execute in topological order. |
| **Engine** | A containerized processor that performs a specific pipeline stage. Can be batch (queue-based) or real-time (WebSocket-based). |
| **Stage** | A processing category in the pipeline: `prepare`, `transcribe`, `align`, `diarize`, `detect`, `refine`, `merge`. |

## Batch Processing

| Term | Definition |
| --- | --- |
| **Orchestrator** | The service that expands jobs into task DAGs, schedules tasks to engine queues, and manages job lifecycle. |
| **Work Queue** | A Redis list where tasks wait for engine workers. Each engine type has its own queue (`dalston:queue:{engine_id}`). |
| **Multi-stage Engine** | An engine that handles multiple pipeline stages in one pass (e.g., WhisperX doing transcribe + align + diarize). |

## Real-time Processing

| Term | Definition |
| --- | --- |
| **Session** | A real-time transcription connection. Each WebSocket connection creates one session. |
| **Worker** | A real-time engine instance that handles streaming transcription sessions. Workers register with the Session Router. |
| **Session Router** | The service that manages the real-time worker pool, allocates sessions to workers, and monitors worker health. |
| **VAD** | Voice Activity Detection — identifies speech vs. silence in audio streams. Used to trigger utterance endpoints. |
| **Partial Transcript** | Interim transcription results that may change as more audio arrives. |
| **Final Transcript** | Committed transcription for a completed utterance (after VAD endpoint or manual commit). |

## Hybrid Mode

| Term | Definition |
| --- | --- |
| **Hybrid Mode** | A processing mode that combines real-time transcription with batch enhancement. Provides immediate results, then improves quality with speaker diarization and LLM cleanup. |
| **Enhancement Job** | A batch job created from a real-time session's recorded audio. Runs additional processing stages not available in real-time. |

## Storage

| Term | Definition |
| --- | --- |
| **Artifact** | Any file produced during processing: audio files, intermediate outputs, final transcripts, exports. |
| **Tenant** | An isolated namespace for multi-tenancy. Jobs, API keys, and artifacts are scoped to tenants. |

## API

| Term | Definition |
| --- | --- |
| **Dalston Native API** | Dalston's own REST and WebSocket endpoints (`/v1/audio/transcriptions/*`). |
| **ElevenLabs Compatible API** | Drop-in replacement endpoints matching ElevenLabs conventions (`/v1/speech-to-text/*`). |
| **Webhook** | An HTTP callback triggered on job completion or failure. |

## Pipeline Stages

| Stage | Purpose |
| --- | --- |
| `prepare` | Convert audio to standard format (16kHz, 16-bit WAV). Split channels if needed. |
| `transcribe` | Convert speech to text. Produces segments with timestamps. |
| `align` | Refine word-level timestamps using forced alignment. |
| `diarize` | Identify and label speakers in the audio. |
| `detect` | Detect emotions, audio events (laughter, applause), or other metadata. |
| `refine` | LLM-based cleanup: error correction, formatting, speaker name inference. |
| `merge` | Combine outputs from all stages into the final transcript. |
