# Real-Time Transcription Architecture

## Overview

Real-time transcription provides streaming speech-to-text with sub-500ms latency. Unlike batch processing which handles complete files through a multi-stage pipeline, real-time operates on continuous audio streams with immediate feedback.

---

## Batch vs Real-Time: Key Differences

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         BATCH vs REAL-TIME                                       │
│                                                                                  │
│   BATCH                                    REAL-TIME                            │
│   ─────                                    ─────────                            │
│                                                                                  │
│   • Complete audio file                    • Streaming audio chunks              │
│   • Queue-based, async                     • Direct connection, sync             │
│   • Latency: seconds to minutes            • Latency: <500ms target              │
│   • Full pipeline (diarize, etc.)          • Transcription only (+ VAD)         │
│   • Redis queue for distribution           • Direct worker connection            │
│   • Worker pulls when ready                • Worker must be available NOW        │
│   • Retry on failure                       • Reconnect on failure                │
│   • Result stored on disk                  • Result streamed to client           │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         REAL-TIME ARCHITECTURE                                   │
│                                                                                  │
│                                                                                  │
│   CLIENT                                                                        │
│     │                                                                           │
│     │ WebSocket                                                                 │
│     │                                                                           │
│     ▼                                                                           │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                           GATEWAY                                        │   │
│   │                                                                          │   │
│   │   WS /v1/audio/transcriptions/stream                                    │   │
│   │                                                                          │   │
│   │   1. Accept connection                                                  │   │
│   │   2. Request worker from Session Router                                 │   │
│   │   3. Proxy bidirectional WebSocket                                      │   │
│   │   4. Release worker on disconnect                                       │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────┬───────────────────────────────┘   │
│                                             │                                   │
│                                             │                                   │
│   ┌─────────────────────────────────────────▼───────────────────────────────┐   │
│   │                        SESSION ROUTER                                    │   │
│   │                                                                          │   │
│   │   • Track available workers                                             │   │
│   │   • Allocate sessions                                                   │   │
│   │   • Load balancing                                                      │   │
│   │   • Health monitoring                                                   │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────┬───────────────────────────────┘   │
│                                             │                                   │
│                  ┌──────────────────────────┼──────────────────────────┐       │
│                  │                          │                          │       │
│                  ▼                          ▼                          ▼       │
│   ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐ │
│   │  REALTIME WORKER 1  │   │  REALTIME WORKER 2  │   │  REALTIME WORKER N  │ │
│   │                     │   │                     │   │                     │ │
│   │  • WebSocket server │   │  • WebSocket server │   │  • WebSocket server │ │
│   │  • VAD (Silero)     │   │  • VAD (Silero)     │   │  • VAD (Silero)     │ │
│   │  • Streaming ASR    │   │  • Streaming ASR    │   │  • Streaming ASR    │ │
│   │  • 4 sessions/GPU   │   │  • 4 sessions/GPU   │   │  • 4 sessions/GPU   │ │
│   │                     │   │                     │   │                     │ │
│   │  [GPU]              │   │  [GPU]              │   │  [GPU]              │ │
│   └─────────────────────┘   └─────────────────────┘   └─────────────────────┘ │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      REAL-TIME PROCESSING PIPELINE                               │
│                                                                                  │
│                                                                                  │
│   ┌───────────────┐                                                             │
│   │ Audio Receiver│  ← Binary WebSocket frames (100-250ms chunks)              │
│   │               │                                                             │
│   │ • Validate    │                                                             │
│   │ • Buffer      │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │  VAD Engine   │  Silero VAD                                                │
│   │               │                                                             │
│   │ • Detect      │  → vad.speech_start event                                  │
│   │   speech      │  → vad.speech_end event                                    │
│   │ • Endpoint    │                                                             │
│   │   detection   │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           ▼ (on speech chunk or endpoint)                                       │
│   ┌───────────────┐                                                             │
│   │  ASR Engine   │  Whisper / faster-whisper / distil-whisper                 │
│   │               │                                                             │
│   │ • Transcribe  │  Runs on GPU                                               │
│   │   chunk       │  Returns text + timestamps                                 │
│   │               │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │  Transcript   │                                                             │
│   │  Assembler    │                                                             │
│   │               │                                                             │
│   │ • Accumulate  │  Maintains full session transcript                         │
│   │ • Align       │  Handles overlapping chunks                                │
│   │ • Finalize    │  Produces final on endpoint                                │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │ Result Sender │  → JSON WebSocket frames                                   │
│   │               │                                                             │
│   │ • Partial     │  transcript.partial (interim results)                      │
│   │ • Final       │  transcript.final (utterance complete)                     │
│   │ • Events      │  vad.speech_start, vad.speech_end                          │
│   └───────────────┘                                                             │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Model Variants

| Variant | Model | Latency | Quality | Use Case |
|---------|-------|---------|---------|----------|
| `fast` | Parakeet 0.6B | ~200ms | Good | Voice assistants, live captioning |
| `accurate` | Parakeet 1.1B | ~300ms | Excellent | Important meetings, English-only |

Workers can load multiple models and switch based on session requirements.

### Streaming vs Non-Streaming Models

| Model Type | VAD | Partial Results | Behavior |
|------------|-----|-----------------|----------|
| **Streaming** (Parakeet) | Optional | Native support | Incremental transcription as audio arrives |
| **Non-streaming** (Whisper) | Required | Not supported | VAD detects utterance end → transcribe → final |

The default realtime engine uses Parakeet for native streaming. The `interim_results` parameter is ignored for non-streaming models like Whisper.

---

## Capacity Planning

### Per-Worker Capacity

```
GPU Memory: 24GB (e.g., RTX 4090)
Model size: ~4GB (distil-whisper)
Per-session overhead: ~1-2GB

→ Approximately 4 concurrent sessions per GPU
```

### Scaling Strategy

| Sessions Needed | Workers Required |
|-----------------|------------------|
| 1-4 | 1 |
| 5-8 | 2 |
| 9-16 | 4 |
| 17-32 | 8 |

### Overflow Handling

When no capacity is available:

| Strategy | Description |
|----------|-------------|
| **Reject** | Return "no_capacity" error, client retries later |
| **Queue** | Hold connection, wait for capacity with timeout |
| **Degrade** | Fall back to smaller model, accept more sessions |

---

## Hybrid Mode

Real-time provides immediate results; batch provides enhanced quality afterward.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   1. REALTIME SESSION                                                           │
│      ─────────────────                                                          │
│                                                                                  │
│      Audio ───▶ Realtime Worker ───▶ Immediate transcript                       │
│      stream          │                    │                                     │
│                      │                    ▼                                     │
│                      │               User sees text NOW                         │
│                      │                                                          │
│                      ▼                                                          │
│                 Save to /data/sessions/{id}/audio.wav                          │
│                                                                                  │
│                                                                                  │
│   2. ON SESSION END (if enhance_on_end=true)                                   │
│      ────────────────────────────────────────                                   │
│                                                                                  │
│      Session recording ───▶ Create batch job ───▶ Return job_id                │
│                                                                                  │
│                                                                                  │
│   3. BATCH ENHANCEMENT                                                          │
│      ─────────────────                                                          │
│                                                                                  │
│      Batch job ───▶ Full pipeline ───▶ Enhanced transcript                     │
│                         │                    │                                  │
│                         │                    ▼                                  │
│                         │              + Diarization                           │
│                         │              + Speaker names                         │
│                         │              + LLM cleanup                           │
│                         │              + Emotions                              │
│                         │                                                       │
│                         ▼                                                       │
│                    Client polls job_id for enhanced result                     │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### API Usage

```
# Connect with enhancement option
WS /v1/audio/transcriptions/stream?enhance_on_end=true

# Session end response includes job_id
{
  "type": "session.end",
  "transcript": "Immediate realtime transcript...",
  "enhancement_job_id": "job_xyz789"
}

# Poll batch API for enhanced result
GET /v1/audio/transcriptions/job_xyz789
```

---

## Components

| Component | Purpose | Documentation |
|-----------|---------|---------------|
| **Gateway** | WebSocket endpoint, request routing | [WebSocket API](./WEBSOCKET_API.md) |
| **Session Router** | Worker pool management, allocation | [Session Router](./SESSION_ROUTER.md) |
| **Realtime Workers** | Streaming transcription | [Realtime Engines](./REALTIME_ENGINES.md) |
| **Session Persistence** | Audio/transcript storage, session history | [Session Persistence](./SESSION_PERSISTENCE.md) |

---

## Redis Data Structures

### Worker Registry

```
dalston:realtime:workers                     (Set)
Members: [worker_id, worker_id, ...]

dalston:realtime:worker:{worker_id}          (Hash)
{
  "endpoint": "ws://stt-rt-transcribe-whisper-1:9000",
  "status": "ready",                          // ready | busy | draining
  "capacity": 4,
  "active_sessions": 2,
  "gpu_memory_used": "4.2GB",
  "gpu_memory_total": "24GB",
  "models_loaded": "[\"distil-whisper\", \"faster-whisper-large\"]",
  "last_heartbeat": "2025-01-28T12:00:00Z"
}

dalston:realtime:worker:{worker_id}:sessions (Set)
Members: [session_id, session_id, ...]
```

### Session State

```
dalston:realtime:session:{session_id}        (Hash)
{
  "worker_id": "stt-rt-transcribe-whisper-1",
  "status": "active",                         // active | ended | error
  "language": "en",
  "model": "fast",
  "started_at": "2025-01-28T12:00:00Z",
  "audio_duration": 45.6,
  "enhance_on_end": true
}

dalston:realtime:sessions:active             (Set)
Members: [session_id, ...]
```

---

## Failure Handling

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crash | Heartbeat timeout | Session Router marks worker offline, notifies Gateway |
| Network disconnect | WebSocket close | Client reconnects, gets new worker |
| GPU OOM | Worker health check | Reduce max sessions, restart worker |
| Transcription error | Exception in ASR | Send error message, continue session |

---

## Latency Budget

Target end-to-end latency: **< 500ms**

| Stage | Budget |
|-------|--------|
| Network (client → gateway) | ~50ms |
| Gateway → Worker routing | ~10ms |
| Audio buffering | ~100ms |
| VAD processing | ~20ms |
| ASR inference | ~250ms |
| Result serialization | ~10ms |
| Network (worker → client) | ~50ms |
| **Total** | **~490ms** |

Actual latency varies with model size and audio characteristics.
