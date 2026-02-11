# M24: Real-Time Session Persistence

| | |
|---|---|
| **Goal** | Persist realtime sessions for visibility, recovery, and hybrid mode |
| **Duration** | 3-4 days |
| **Dependencies** | M6 in progress |
| **Deliverable** | Sessions stored in DB, audio/transcript saved to S3, console visibility |
| **Status** | In Progress |

## User Story

> *"I can see all my past and current realtime transcriptions, and if my connection drops, I don't lose everything."*

---

## Overview

Currently, realtime sessions exist only in Redis with a 60-second TTL after ending. Audio is buffered in memory and discarded. This milestone adds durable persistence to enable:

- **Visibility**: List past/running sessions in web console
- **Audit**: Full session metadata and stats retained
- **Recovery**: Audio saved during session, not just on end
- **Hybrid Mode**: Prerequisite for M07's batch enhancement

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     REALTIME SESSION PERSISTENCE                                 │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                         DURING SESSION                                   │   │
│   │                                                                          │   │
│   │   Audio chunks ──┬──▶ [VAD → Transcribe] ──▶ WebSocket to client        │   │
│   │                  │                                                       │   │
│   │                  ├──▶ [AudioRecorder] ──▶ S3 (buffered writes)          │   │
│   │                  │                                                       │   │
│   │                  └──▶ [Stats Tracker] ──▶ PostgreSQL (periodic update)  │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                            │
│                                     ▼ on session end                            │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                          ON SESSION END                                  │   │
│   │                                                                          │   │
│   │   1. Finalize audio file in S3                                          │   │
│   │   2. Save final transcript to S3                                        │   │
│   │   3. Update session status in PostgreSQL                                │   │
│   │   4. (M07) Create enhancement job if enhance_on_end=true                │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 24.1: RealtimeSession Database Model

**Deliverables:**

- `RealtimeSessionModel` in PostgreSQL with full session metadata
- Alembic migration for new table
- Session created in DB on session start (not just Redis)

**Schema:**

```python
class RealtimeSessionModel(Base):
    __tablename__ = "realtime_sessions"

    id: UUID                          # Session ID
    tenant_id: UUID                   # FK to tenants

    # Status
    status: str                       # active | completed | error | interrupted

    # Parameters (immutable after creation)
    language: str
    model: str                        # fast | accurate
    encoding: str                     # pcm_s16le, mulaw, etc.
    sample_rate: int

    # Feature flags
    store_audio: bool                 # Whether audio is being recorded
    store_transcript: bool            # Whether to persist transcript
    enhance_on_end: bool              # Trigger batch enhancement

    # Results (populated during/after session)
    audio_uri: str | None             # S3 path to audio file
    transcript_uri: str | None        # S3 path to transcript JSON
    enhancement_job_id: UUID | None   # FK to jobs table

    # Stats (updated periodically during session)
    audio_duration_seconds: float
    utterance_count: int
    word_count: int

    # Tracking
    worker_id: str
    client_ip: str
    started_at: datetime
    ended_at: datetime | None
    error: str | None
```

---

### 24.2: Session Lifecycle Integration

**Deliverables:**

- Create DB record on session start (Gateway or Session Router)
- Update stats periodically during session (every 10s or on utterance)
- Finalize record on session end with final stats and status

**Integration points:**

| Event | Action |
|-------|--------|
| WebSocket connect | Create RealtimeSessionModel with status=active |
| Utterance complete | Increment utterance_count, word_count, audio_duration |
| Session end | Set status=completed, ended_at, final stats |
| Error/disconnect | Set status=error or interrupted, capture error message |

---

### 24.3: Audio Recording to S3

**Deliverables:**

- `AudioRecorder` class that buffers audio chunks
- S3 multipart upload: start on session begin, upload parts every 30s
- Complete upload on session end
- Storage path: `s3://{bucket}/sessions/{session_id}/audio.wav`
- WAV header written at finalization (16kHz, 16-bit PCM)

**Opt-in via parameter:**

```text
WS /v1/audio/transcriptions/stream?store_audio=true
```

**Implementation notes:**

- Buffer audio in memory ring buffer (30-60 seconds max)
- Flush to S3 when buffer reaches threshold or on timer
- On session end, complete multipart upload and write WAV header
- On error, attempt to finalize partial recording

---

### 24.4: Transcript Persistence

**Deliverables:**

- Save final transcript JSON to S3 on session end
- Storage path: `s3://{bucket}/sessions/{session_id}/transcript.json`
- Transcript format matches batch job output for consistency

**Opt-in via parameter:**

```text
WS /v1/audio/transcriptions/stream?store_transcript=true
```

**Transcript schema:**

```json
{
  "session_id": "sess_abc123",
  "language": "en",
  "duration": 45.6,
  "text": "Full transcript text...",
  "utterances": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "Hello world",
      "words": [...]
    }
  ]
}
```

---

### 24.5: Session Query API

**New endpoints:**

```text
GET /v1/realtime/sessions
GET /v1/realtime/sessions/{session_id}
DELETE /v1/realtime/sessions/{session_id}
```

**List response:**

```json
{
  "sessions": [
    {
      "id": "sess_abc123",
      "status": "completed",
      "language": "en",
      "model": "fast",
      "audio_duration_seconds": 45.6,
      "utterance_count": 12,
      "started_at": "2025-01-28T12:00:00Z",
      "ended_at": "2025-01-28T12:01:00Z"
    }
  ],
  "pagination": { ... }
}
```

**Detail response includes:**

- All metadata fields
- Links to audio/transcript if stored
- Enhancement job status if applicable

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status (active, completed, error) |
| `since` | datetime | Sessions started after this time |
| `until` | datetime | Sessions started before this time |
| `limit` | int | Max results (default 50) |
| `offset` | int | Pagination offset |

**Delete endpoint:**

Only non-active sessions (completed, error, interrupted) can be deleted. Attempting to delete an active session returns 409 Conflict.

```text
DELETE /v1/realtime/sessions/{session_id}

Response: {"deleted": true, "session_id": "sess_abc123"}
```

---

### 24.6: Web Console Integration

**Deliverables:**

- New "Realtime Sessions" page in web console
- List view with status, duration, timestamps
- Detail view with full metadata and stats
- Real-time status updates for active sessions (via polling or WebSocket)
- Links to download audio/transcript if stored

---

### 24.7: Session Resume (Basic)

**Deliverables:**

- New query parameter: `resume_session_id`
- If provided, create new session linked to previous
- Link stored in `previous_session_id` field
- Final transcript can optionally merge linked sessions

**Behavior:**

```text
WS /v1/audio/transcriptions/stream?resume_session_id=sess_abc123

← {"type": "session.begin", "session_id": "sess_def456", "resumed_from": "sess_abc123"}
```

**Note:** This is "soft" resume - a new session linked to old one, not true state restoration. Audio and transcripts remain separate but can be merged in post-processing.

---

## New WebSocket Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `store_audio` | bool | false | Record audio to S3 |
| `store_transcript` | bool | false | Save final transcript to S3 |
| `enhance_on_end` | bool | false | Create batch job on end (M07) |
| `resume_session_id` | string | null | Link to previous session |

---

## Verification

```bash
# Connect with persistence enabled
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?store_audio=true&store_transcript=true"

# Stream audio, end session
→ (audio frames)
→ {"type": "end"}
← {"type": "session.end", "session_id": "sess_abc123", ...}

# List sessions via API
curl http://localhost:8000/v1/realtime/sessions

# Get session details
curl http://localhost:8000/v1/realtime/sessions/sess_abc123

# Download stored audio
curl http://localhost:8000/v1/realtime/sessions/sess_abc123/audio -o session.wav

# Download stored transcript
curl http://localhost:8000/v1/realtime/sessions/sess_abc123/transcript
```

---

## Checkpoint

- [x] **RealtimeSessionModel** created in PostgreSQL on session start
- [x] **Session stats** updated during session (duration, utterances, words)
- [ ] **Audio recording** streams to S3 during session (opt-in)
- [ ] **Transcript saved** to S3 on session end (opt-in)
- [x] **Session list API** returns past and active sessions
- [x] **Session delete API** removes non-active sessions
- [x] **Web console** shows realtime sessions page with delete functionality
- [x] **SDK support** for list, get, and delete session operations
- [x] **CLI support** via `dalston sessions list|get|delete` commands
- [ ] **Resume parameter** links sessions together

**Next**: [M7: Hybrid Mode](M07-hybrid-mode.md) — Create batch enhancement jobs from recorded sessions
