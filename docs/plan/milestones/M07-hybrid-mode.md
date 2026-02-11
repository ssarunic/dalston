# M7: Hybrid Mode

| | |
|---|---|
| **Goal** | Real-time results + batch enhancement |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete, M24 complete |
| **Deliverable** | Sessions can trigger batch enhancement on end |
| **Status** | Complete |

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

- [x] **Enhancement job** created automatically on session end (when `enhance_on_end=true`)
- [x] **Job uses recorded audio** from M24's S3 storage
- [x] **Job linked** to session via `enhancement_job_id`
- [x] **Full batch pipeline** runs (diarization, alignment, optional LLM cleanup)
- [x] **Enhancement status endpoint** returns job status and result
- [x] **Session end message** includes `enhancement_job_id`

**Next**: [M8: ElevenLabs Compatibility](M08-elevenlabs-compat.md) — Drop-in API replacement

---

## Implementation Summary (February 2026)

### What Was Built

**Enhancement Service (7.1):**

- `EnhancementService` in `dalston/gateway/services/enhancement.py`
- Creates batch jobs from realtime session recordings
- Validates: requires `store_audio=true` for audio to exist
- Validates: session must be in terminal state (not active)
- Validates: session cannot already have an enhancement job
- Maps realtime models (fast, parakeet) to batch model (large-v3)

**Gateway Integration (7.2):**

- Enhancement job created automatically in session finalization flow
- Validation added at WebSocket connect: `enhance_on_end=true` requires `store_audio=true`
- Error returned early with clear message if validation fails
- Enhancement job ID passed to session finalization and stored in DB

**Enhancement Status Endpoint (7.3):**

```
GET /v1/realtime/sessions/{session_id}/enhancement
```

Returns enhancement status:

- `not_requested` - session didn't have `enhance_on_end=true`
- `pending` - session still active, job not created yet
- `processing` - enhancement job running
- `completed` - job done, includes enhanced transcript
- `failed` - job failed, includes error message

**Manual Enhancement Trigger:**

```
POST /v1/realtime/sessions/{session_id}/enhance
```

Allows triggering enhancement for sessions that:

- Had `store_audio=true` but `enhance_on_end=false`
- Need re-enhancement with different options

Query parameters:

- `enable_diarization` (bool, default: true)
- `enable_word_timestamps` (bool, default: true)
- `enable_llm_cleanup` (bool, default: false)
- `enable_emotions` (bool, default: false)

**Session End Message (7.4):**

The `session.end` message already included `enhancement_job_id` field from M24 preparation.

### Enhancement Job Parameters

When creating enhancement jobs from realtime sessions:

```python
parameters = {
    "language": session.language or "auto",
    "model": "large-v3",  # Use full model for accuracy
    "speaker_detection": "diarize",
    "timestamps_granularity": "word",
    "llm_cleanup": enhance_llm_cleanup,  # Optional
    "emotion_detection": enhance_emotions,  # Optional
    "_enhancement": {
        "source_session_id": session.id,
        "original_model": session.model,
        "original_engine": session.engine,
    }
}
```

### Files Changed

| Component | Key Files |
|-----------|-----------|
| Enhancement Service | `dalston/gateway/services/enhancement.py` (new) |
| Gateway Endpoints | `dalston/gateway/api/v1/realtime.py` |
| Unit Tests | `tests/unit/test_enhancement_service.py` (new) |
| Integration Tests | `tests/integration/test_hybrid_mode_api.py` (new) |
| E2E Tests | `tests/e2e/test_hybrid_mode_e2e.py` (new) |

### Usage Example

```bash
# 1. Connect with enhancement enabled
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?\
api_key=dk_xxx&store_audio=true&enhance_on_end=true"

# 2. Stream audio, get realtime transcripts
→ (audio frames)
← {"type": "transcript.partial", "text": "Hello"}
← {"type": "transcript.final", "text": "Hello world"}

# 3. End session
→ {"type": "end"}
← {"type": "session.end",
    "transcript": "Hello world...",
    "enhancement_job_id": "job_abc123"}

# 4. Poll enhancement status
curl http://localhost:8000/v1/realtime/sessions/sess_xxx/enhancement
→ {"status": "processing", "enhancement_job_id": "job_abc123"}

# 5. Get enhanced result when complete
curl http://localhost:8000/v1/realtime/sessions/sess_xxx/enhancement
→ {"status": "completed",
    "transcript": {"text": "Hello world", "segments": [...], "speakers": [...]}}

# Or poll the batch job directly
curl http://localhost:8000/v1/audio/transcriptions/job_abc123
```

### Test Coverage

- **19 unit tests** for EnhancementService
- **10 integration tests** for API endpoints and response models
- **4 e2e tests** for full hybrid workflow (require infrastructure)

All 688 existing tests continue to pass.
