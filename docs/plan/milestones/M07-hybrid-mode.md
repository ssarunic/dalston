# M7: Hybrid Mode

| | |
|---|---|
| **Goal** | Real-time results + batch enhancement |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete |
| **Deliverable** | Sessions can trigger batch enhancement on end |
| **Status** | In Progress |

## User Story

> *"I see text live, then get improved results with speaker names afterward."*

---

## Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                                                                       │
│   REALTIME SESSION                        BATCH ENHANCEMENT          │
│                                                                       │
│   Audio ───▶ Realtime ───▶ Immediate     Session ───▶ Batch ───▶    │
│   stream     Worker       transcript     recording   Pipeline        │
│                                │                        │            │
│                                ▼                        ▼            │
│                           User sees               Enhanced result    │
│                           text NOW                + diarization      │
│                           (< 500ms)               + speaker names    │
│                                                   + LLM cleanup      │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 7.1: Session Recording

**Deliverables:**

- `SessionRecorder` class that accumulates audio chunks during session
- Write WAV file on session end (16kHz, 16-bit PCM)
- Storage location: `/data/sessions/{session_id}/audio.wav`
- Optional: checkpoint writes every N seconds for fault tolerance

---

### 7.2: Enhancement Job Creation

**Deliverables:**

- When `enhance_on_end=true`, create batch job from recorded audio
- Job parameters: `speaker_detection=diarize`, `word_timestamps=true`
- Optional LLM cleanup and emotion detection via additional params
- Link session to enhancement job in Redis

---

### 7.3: Gateway Enhancement Parameters

**New query parameters:**

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `enhance_on_end` | bool | false | Create batch job on session end |
| `enhance_llm_cleanup` | bool | false | Include LLM cleanup in enhancement |
| `enhance_emotions` | bool | false | Include emotion detection |

---

### 7.4: Enhancement Status Endpoint

**New endpoint:**

```text
GET /v1/audio/transcriptions/stream/{session_id}/enhancement
```

**Response:**

```json
{
  "session_id": "sess_abc123",
  "enhancement_job_id": "job_xyz789",
  "status": "completed",
  "transcript": { ... }
}
```

---

### 7.5: Session End Message

**Updated `session.end` message:**

```json
{
  "type": "session.end",
  "session_id": "sess_abc123",
  "total_duration": 45.6,
  "transcript": "Full real-time transcript...",
  "enhancement_job_id": "job_xyz789"
}
```

---

## Usage Flow

```text
1. Client connects with enhance_on_end=true

   ws://localhost:8000/v1/audio/transcriptions/stream?enhance_on_end=true

2. Client streams audio, receives real-time transcripts

   ← {"type": "transcript.partial", "text": "Hello"}
   ← {"type": "transcript.final", "text": "Hello world", ...}

3. Client ends session

   → {"type": "end"}
   ← {"type": "session.end",
       "transcript": "Hello world...",
       "enhancement_job_id": "job_abc123"}

4. Client polls for enhanced result

   GET /v1/audio/transcriptions/job_abc123

   → {"status": "completed", "segments": [...], "speakers": [...]}
```

---

## Verification

```bash
# Connect with enhancement enabled
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?enhance_on_end=true"

# Stream audio, end session
# Note enhancement_job_id in session.end message

# Poll for enhanced result
curl http://localhost:8000/v1/audio/transcriptions/{job_id}
```

---

## Checkpoint

- [ ] **Session recording** saves all streamed audio
- [ ] **Enhancement job** created automatically on session end
- [ ] **Job linked** to session for status tracking
- [ ] **Full batch pipeline** runs on recorded audio
- [ ] **Client receives** both real-time and enhanced results

**Next**: [M8: ElevenLabs Compatibility](M08-elevenlabs-compat.md) — Drop-in API replacement
