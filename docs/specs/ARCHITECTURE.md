# Dalston Architecture

## Executive Summary

**Dalston** is a modular, self-hosted audio transcription server that provides an ElevenLabs-compatible API for both batch and real-time transcription. It deconstructs monolithic transcription pipelines into isolated, containerized engines that communicate via Redis queues and S3 storage.

### Core Value Proposition

- **Dual Mode**: Both batch (file upload) and real-time (streaming) transcription
- **Engine Isolation**: Each processing engine runs in its own container, eliminating dependency conflicts
- **Pluggable Pipeline**: Swap transcription, diarization, or alignment engines without changing the system
- **Two-Level Queue**: Jobs contain task DAGs enabling parallel processing and granular failure handling
- **Multi-Stage Engines**: Support for integrated pipelines (like WhisperX) that handle multiple stages in one pass
- **Hybrid Mode**: Get immediate real-time results, then enhance with batch processing
- **Simple API, Complex Internals**: ElevenLabs-compatible API abstracts internal complexity

---

## Batch vs Real-Time: When to Use

| Aspect | Batch | Real-Time |
|--------|-------|-----------|
| **Use Case** | Recorded audio, podcasts, meetings | Live calls, voice assistants, live captioning |
| **Latency** | Seconds to minutes | < 500ms target |
| **Features** | Full pipeline (diarize, emotions, LLM cleanup) | Transcription only (+ VAD) |
| **Input** | Complete audio file | Streaming audio chunks |
| **Scaling** | Queue-based, async | Direct connection, capacity-limited |
| **Quality** | Highest (multiple passes) | Good (single pass, optimized for speed) |

**Hybrid Mode**: Start with real-time for immediate feedback, then run batch enhancement for speaker identification, error correction, and analysis.

---

## System Architecture

### Unified Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   DALSTON                                        │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │                              GATEWAY                                        │ │
│  │                          FastAPI + React                                    │ │
│  │                                                                             │ │
│  │   REST API (/v1/audio/transcriptions)         ─── BATCH PATH               │ │
│  │   WebSocket (/v1/audio/transcriptions/stream) ─── REALTIME PATH            │ │
│  │   Management Console (/console)                                             │ │
│  │                                                                             │ │
│  └──────────────────────┬─────────────────────────┬────────────────────────────┘ │
│                         │                         │                              │
│            BATCH        │                         │   REALTIME                  │
│                         ▼                         ▼                              │
│  ┌──────────────────────────────┐   ┌──────────────────────────────────────────┐│
│  │        ORCHESTRATOR          │   │         SESSION ROUTER                    ││
│  │                              │   │                                           ││
│  │   • DAG expansion            │   │   • Worker pool management               ││
│  │   • Task scheduling          │   │   • Session allocation                   ││
│  │   • Dependency management    │   │   • Load balancing                       ││
│  └──────────────┬───────────────┘   └─────────────────┬────────────────────────┘│
│                 │                                     │                          │
│                 ▼                                     │                          │
│  ┌──────────────────────────────────────────────────────────────────────────────┐│
│  │                              REDIS                                           ││
│  │                                                                              ││
│  │   Batch: Job/Task state, engine work queues, events                         ││
│  │   Realtime: Worker registry, session state, metrics                         ││
│  │                                                                              ││
│  └──────────────┬───────────────────────────────────┬───────────────────────────┘│
│                 │                                   │                            │
│    ┌────────────┴────────────┐                     │                            │
│    │                         │                     │                            │
│    ▼                         ▼                     ▼                            │
│  ┌──────────────────┐      ┌──────────────────────────────────────────────────┐ │
│  │  BATCH ENGINE    │      │              REALTIME WORKER POOL                │ │
│  │  CONTAINERS      │      │                                                  │ │
│  │                  │      │   ┌─────────────────┐  ┌─────────────────┐       │ │
│  │  faster-whisper  │      │   │ realtime-       │  │ realtime-       │       │ │
│  │  pyannote        │      │   │ whisper-1       │  │ whisper-2       │       │ │
│  │  phoneme-align   │      │   │                 │  │                 │       │ │
│  │  llm-cleanup     │      │   │ • WebSocket srv │  │ • WebSocket srv │       │ │
│  │  merger          │      │   │ • Streaming ASR │  │ • Streaming ASR │       │ │
│  │  whisperx-full   │      │   │ • VAD           │  │ • VAD           │       │ │
│  │                  │      │   │                 │  │                 │       │ │
│  │  [Poll Redis]    │      │   │ [Direct WS]     │  │ [Direct WS]     │       │ │
│  └──────────────────┘      │   └─────────────────┘  └─────────────────┘       │ │
│                            └──────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                           STORAGE LAYER                                      │ │
│  │                                                                              │ │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │ │
│  │   │   PostgreSQL    │  │      S3         │  │      Local Temp             │ │ │
│  │   │                 │  │                 │  │                             │ │ │
│  │   │  • Jobs         │  │  • Audio files  │  │  • In-flight processing    │ │ │
│  │   │  • Tasks        │  │  • Transcripts  │  │  • Audio buffering         │ │ │
│  │   │  • API Keys     │  │  • Exports      │  │  • Model cache             │ │ │
│  │   │  • Tenants      │  │  • Models       │  │                             │ │ │
│  │   │                 │  │                 │  │                             │ │ │
│  │   └─────────────────┘  └─────────────────┘  └─────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### Gateway

**Purpose**: Public API, web console, request routing

**Technology**: FastAPI (Python) + React (TypeScript)

**Endpoints**:

- `POST /v1/audio/transcriptions` — Submit file for batch transcription
- `GET /v1/audio/transcriptions/{id}` — Get batch job status/results
- `WS /v1/audio/transcriptions/stream` — Real-time streaming transcription
- `GET /v1/realtime/status` — Real-time capacity and status
- `GET /console/*` — Management UI

**Documentation**: [Batch API](./batch/API.md) | [WebSocket API](./realtime/WEBSOCKET_API.md)

---

### Orchestrator (Batch)

**Purpose**: Job expansion, task scheduling, dependency management

**Responsibilities**:

- Expand jobs into task DAGs based on parameters
- Select optimal engines (single-stage or multi-stage)
- Push ready tasks to engine queues
- Handle failures and retries
- Trigger webhooks on completion

**Documentation**: [Orchestrator Details](./batch/ORCHESTRATOR.md)

---

### Session Router (Real-Time)

**Purpose**: Real-time worker pool management and session allocation

**Responsibilities**:

- Track available real-time workers
- Allocate sessions to workers with capacity
- Monitor worker health via heartbeat
- Handle failover and reconnection

**Documentation**: [Session Router](./realtime/SESSION_ROUTER.md)

---

### Storage Architecture

| Layer | Technology | Purpose |
|-------|------------|---------|
| **PostgreSQL** | Primary database | Persistent business data (jobs, tasks, API keys, tenants) |
| **Redis** | In-memory store | Ephemeral data (streams, session state, rate limits, pub/sub) |
| **S3** | Object storage | All artifacts (audio files, transcripts, exports, models) |
| **Local** | Temp filesystem | In-flight processing files only |

### Redis (Ephemeral)

**Purpose**: Streams, pub/sub, rate limiting, session state

**Batch Structures**:

- `dalston:stream:{engine_id}` — Engine work streams
- `dalston:ratelimit:{key_id}` — Rate limit counters
- `dalston:events` — Event pub/sub

**Real-Time Structures**:

- `dalston:realtime:workers` — Worker registry
- `dalston:realtime:worker:{id}` — Worker state
- `dalston:realtime:session:{id}` — Session state (TTL-based)

**Documentation**: [Data Model](./batch/DATA_MODEL.md)

---

### Batch Engine Containers

**Purpose**: Execute processing tasks in isolated environments

**Execution Model**: Poll Redis stream (consumer group), process task, acknowledge completion

**Engine Categories**:

| Category | Engines |
|----------|---------|
| TRANSCRIBE | faster-whisper, parakeet, whisper-openai |
| ALIGN | phoneme-align |
| DIARIZE | pyannote-4.0 |
| DETECT | emotion2vec, panns-events, pii-presidio |
| REDACT | audio-redactor |
| REFINE | llm-cleanup |
| MERGE | final-merger |
| MULTI-STAGE | whisperx-full |

**Documentation**: [Engines Reference](./batch/ENGINES.md)

---

### Real-Time Worker Pool

**Purpose**: Handle streaming transcription with low latency

**Execution Model**: WebSocket server accepting direct connections

**Capabilities**:

- Voice Activity Detection (VAD)
- Streaming ASR with partial results
- Multiple concurrent sessions per worker
- Model variants (fast/accurate)

**Documentation**: [Real-Time Engines](./realtime/REALTIME_ENGINES.md)

---

## Batch Pipeline

```
Ingest → Prepare → Transcribe → Align → Diarize → Enrich → Refine (LLM) → Merge → Output
```

### Speaker Detection Modes

| Mode | Description | Pipeline |
|------|-------------|----------|
| `none` | No speaker identification | transcribe → merge |
| `diarize` | AI-based detection | transcribe → align → diarize → merge |
| `per_channel` | Channel = speaker | split → [transcribe×N] → merge |

### Single-Stage vs Multi-Stage

**Modular** (maximum flexibility):

```
faster-whisper → phoneme-align → pyannote → merger
```

**Integrated** (optimized pipeline):

```
whisperx-full [transcribe + align + diarize] → merger
```

---

## Real-Time Pipeline

```
Audio Stream → VAD → Streaming ASR → Transcript Assembly → WebSocket Output
```

### Features

- **Partial Results**: See text as it's being spoken
- **Final Results**: Confirmed transcript on utterance end
- **VAD Events**: Speech start/end notifications
- **Word Timestamps**: Optional word-level timing

### Model Variants

| Variant | Model | Latency | Quality |
|---------|-------|---------|---------|
| `fast` | Parakeet 0.6B | ~200ms | Good |
| `accurate` | Parakeet 1.1B | ~300ms | Excellent |

Parakeet models provide native streaming with true partial results during speech. Non-streaming models (Whisper) use VAD-chunked transcription.

---

## Hybrid Mode

Get immediate results with real-time, then enhance with batch processing:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   REALTIME SESSION                        BATCH ENHANCEMENT                     │
│                                                                                  │
│   Audio ───▶ Realtime ───▶ Immediate     Session ───▶ Batch ───▶ Enhanced      │
│   stream     Worker       transcript     recording   Pipeline   result          │
│                                │                                   │            │
│                                ▼                                   ▼            │
│                           User sees                           User gets         │
│                           text NOW                            + diarization     │
│                           (< 500ms)                           + speaker names   │
│                                                               + LLM cleanup     │
│                                                               + emotions        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Usage**:

```
WS /v1/audio/transcriptions/stream?enhance_on_end=true
```

On session end, returns `enhancement_job_id` to poll for enhanced transcript.

---

## Key Technical Decisions

| Decision | Choice | Details |
|----------|--------|---------|
| Storage architecture | PostgreSQL + Redis + S3 | [ADR-001](../decisions/ADR-001-storage-architecture.md) |
| Engine isolation | Docker containers | [ADR-002](../decisions/ADR-002-engine-isolation.md) |
| Job/task model | Two-level queues (Jobs → Tasks) | [ADR-003](../decisions/ADR-003-two-level-queues.md) |
| Realtime communication | Direct WebSocket | Low latency, bidirectional |
| API compatibility | ElevenLabs | Easy migration for users |
| Realtime scaling | Worker pool + router | Capacity management, load balancing |

For detailed rationale on architectural decisions, see [Architecture Decision Records](../decisions/README.md).

---

## Implementation Phases

### Phase 1: Batch MVP

- Gateway (REST API)
- Orchestrator
- Core engines: faster-whisper, merger

### Phase 2: Batch Speaker Detection

- Diarization and per-channel modes
- Engines: phoneme-align, pyannote

### Phase 3: Batch Enrichment

- Emotion, events, LLM cleanup

### Phase 4: Real-Time MVP

- Session Router
- Realtime workers
- WebSocket API

### Phase 5: Hybrid Mode

- Session recording
- Batch enhancement from realtime

### Phase 6: Management Console

- React web application
- Unified batch + realtime monitoring

---

## Documentation Index

### Batch Transcription

- [API Reference](./batch/API.md) — REST endpoints, parameters, responses
- [Orchestrator](./batch/ORCHESTRATOR.md) — DAG building, task scheduling
- [Data Model](./batch/DATA_MODEL.md) — Redis structures, transcript format
- [Engines](./batch/ENGINES.md) — Engine reference, SDK, creating engines
- [Docker](./batch/DOCKER.md) — Container composition, operations

### Real-Time Transcription

- [Real-Time Overview](./realtime/REALTIME.md) — Architecture and concepts
- [WebSocket API](./realtime/WEBSOCKET_API.md) — Protocol reference
- [Session Router](./realtime/SESSION_ROUTER.md) — Worker pool management
- [Real-Time Engines](./realtime/REALTIME_ENGINES.md) — Streaming worker implementation

### General

- [Project Structure](./PROJECT_STRUCTURE.md) — Directory layout, packages
- [Glossary](../GLOSSARY.md) — Terminology definitions

### Architecture Decisions

- [ADR Index](../decisions/README.md) — Why we made key technical choices

### Examples

- [WebSocket Clients](./examples/websocket-clients.md) — JS/Python client implementations
- [Webhook Verification](./examples/webhook-verification.md) — Signature verification code

### Implementation Reference

- [Auth Patterns](./implementations/auth-patterns.md) — API key auth, middleware, scopes, rate limiting
- [DAG Builder](./implementations/dag-builder.md) — Task DAG construction with optional/parallel tasks
- [Enrichment Engines](./implementations/enrichment-engines.md) — Emotion, events, LLM cleanup patterns
- [Console API](./implementations/console-api.md) — Dashboard aggregation, React Query integration
