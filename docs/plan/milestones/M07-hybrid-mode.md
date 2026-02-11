# M7: Hybrid Mode

| | |
|---|---|
| **Goal** | Real-time results + batch enhancement |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete, M24 complete |
| **Deliverable** | Sessions can trigger batch enhancement on end |
| **Status** | Not Started |

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

## Prerequisites from M24

This milestone builds on M24's session persistence:

- **Audio recording**: Sessions with `store_audio=true` have audio in S3
- **Session model**: `RealtimeSessionModel` tracks session metadata
- **S3 storage**: Audio at `s3://{bucket}/sessions/{session_id}/audio.wav`

---

## Steps

### 7.1: Enhancement Job Creation

**Deliverables:**

- When `enhance_on_end=true`, create batch job from recorded audio
- Requires `store_audio=true` (error if audio not recorded)
- Job parameters: `speaker_detection=diarize`, `word_timestamps=true`
- Optional LLM cleanup and emotion detection via additional params
- Link `enhancement_job_id` back to `RealtimeSessionModel`

---

### 7.2: Gateway Enhancement Parameters

**New query parameters:**

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `enhance_on_end` | bool | false | Create batch job on session end |
| `enhance_llm_cleanup` | bool | false | Include LLM cleanup in enhancement |
| `enhance_emotions` | bool | false | Include emotion detection |

---

### 7.3: Enhancement Status Endpoint

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

### 7.4: Session End Message

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
# Connect with enhancement enabled (requires store_audio=true)
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?store_audio=true&enhance_on_end=true"

# Stream audio, end session
# Note enhancement_job_id in session.end message

# Poll for enhanced result
curl http://localhost:8000/v1/audio/transcriptions/{job_id}

# Check enhancement status via session
curl http://localhost:8000/v1/audio/transcriptions/stream/{session_id}/enhancement
```

---

## Checkpoint

- [ ] **Enhancement job** created automatically on session end (when `enhance_on_end=true`)
- [ ] **Job uses recorded audio** from M24's S3 storage
- [ ] **Job linked** to session via `enhancement_job_id`
- [ ] **Full batch pipeline** runs (diarization, alignment, optional LLM cleanup)
- [ ] **Enhancement status endpoint** returns job status and result
- [ ] **Session end message** includes `enhancement_job_id`

**Next**: [M8: ElevenLabs Compatibility](M08-elevenlabs-compat.md) — Drop-in API replacement
