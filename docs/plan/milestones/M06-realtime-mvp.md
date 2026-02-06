# M6: Real-Time MVP

| | |
|---|---|
| **Goal** | Stream audio, get live transcripts |
| **Duration** | 5-6 days |
| **Dependencies** | M2 complete (can start parallel with M3-M5) |
| **Deliverable** | WebSocket streaming transcription |
| **Status** | In Progress |

## User Story

> *"As a user on a live call, I see text appearing as people speak."*

---

## Architecture Overview

```text
┌─────────┐     ┌─────────┐     ┌────────────────┐     ┌──────────────┐
│ Client  │────▶│ Gateway │────▶│ Session Router │────▶│ RT Worker 1  │
│         │◀────│         │◀────│                │     │ (4 sessions) │
└─────────┘     └─────────┘     │                │     └──────────────┘
   WebSocket       Proxy        │                │     ┌──────────────┐
                                │                │────▶│ RT Worker 2  │
                                └────────────────┘     │ (4 sessions) │
                                                       └──────────────┘
```

---

## Steps

### 6.1: Realtime SDK

```text
dalston/realtime_sdk/
├── __init__.py
├── engine.py          # Base RealtimeEngine class
├── session.py         # Session handler
├── vad.py             # Silero VAD wrapper
├── assembler.py       # Transcript assembly
├── registry.py        # Worker registry client
└── protocol.py        # Message types
```

**Deliverables:**

- `RealtimeEngine` base class with WebSocket server and model loading
- `SessionHandler` managing audio processing and transcription for one session
- `VADProcessor` using Silero VAD for speech detection
- Worker registration with Session Router via Redis

---

### 6.2: Session Router

```text
dalston/session_router/
├── __init__.py
├── router.py
├── registry.py
├── allocator.py
└── health.py
```

**Deliverables:**

- Track worker pool in Redis: endpoint, status, capacity, active sessions, models
- `acquire_worker()`: Find worker with capacity, reserve slot atomically
- `release_worker()`: Release slot when session ends
- Least-loaded allocation strategy
- Health check loop: mark workers offline if heartbeat stale >30s

---

### 6.3: Gateway WebSocket Endpoint

**New endpoint:**

```text
WS /v1/audio/transcriptions/stream
```

**Query parameters:**

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `language` | string | auto | Language code or "auto" |
| `model` | string | fast | "fast" or "accurate" |
| `word_timestamps` | bool | false | Include word-level timing |
| `interim_results` | bool | true | Send partial transcripts |

**Deliverables:**

- Accept WebSocket connection
- Acquire worker from Session Router
- Return error if no capacity
- Bidirectional proxy between client and worker
- Release worker on disconnect

---

### 6.4: Realtime Worker Engine

```text
engines/realtime/whisper-streaming/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Load fast (distil-whisper) and accurate (large-v3) models
- Load Silero VAD model
- WebSocket server accepting session connections
- Handle multiple concurrent sessions (default: 4)
- Register with Session Router on startup
- Send heartbeats every 10 seconds

---

### 6.5: Session Handler

**Deliverables:**

- Receive audio chunks (16-bit PCM @ 16kHz)
- Run VAD to detect speech boundaries
- Transcribe on speech end
- Send partial results every 500ms during speech
- Track full transcript with assembler
- Send session end message with full transcript

---

### 6.6: VAD Processor

**Deliverables:**

- Silero VAD integration with configurable threshold (default: 0.5)
- Detect speech start, continue, end events
- Configurable min speech duration (250ms) and min silence duration (500ms)
- Lookback buffer (~300ms) to capture speech start
- Return accumulated speech audio on speech end

---

## WebSocket Protocol

### Client → Server

| Type | Format | Description |
| --- | --- | --- |
| Audio | Binary | Raw PCM int16 @ 16kHz mono |
| End | `{"type": "end"}` | End session gracefully |
| Flush | `{"type": "flush"}` | Force transcription of buffered audio |

### Server → Client

| Type | Description |
| --- | --- |
| `session.begin` | Session started, includes session_id |
| `vad.speech_start` | Speech detected at timestamp |
| `vad.speech_end` | Speech ended at timestamp |
| `transcript.partial` | Interim result (may change) |
| `transcript.final` | Confirmed transcript with start/end times |
| `session.end` | Session complete with full transcript |
| `error` | Error with code and message |

---

## Verification

```bash
# Start realtime worker
docker compose up -d realtime-whisper-1

# Connect via WebSocket
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?model=fast"

# Send audio and observe transcripts
# (Use test client to stream audio file)
```

---

## Checkpoint

- [ ] **Session Router** manages worker pool
- [ ] **Gateway** proxies WebSocket to workers
- [ ] **Worker** handles multiple concurrent sessions
- [ ] **VAD** detects speech boundaries
- [ ] **Streaming ASR** produces partial + final results

**Next**: [M7: Hybrid Mode](M07-hybrid-mode.md) — Real-time + batch enhancement
