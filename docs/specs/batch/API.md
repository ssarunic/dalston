# Dalston API Reference

## Overview

Dalston provides a dual-mode REST API for audio transcription:

- **Dalston Native** (`/v1/audio/transcriptions/*`) — Dalston's own conventions
- **ElevenLabs Compatible** (`/v1/speech-to-text/*`) — Drop-in replacement for ElevenLabs API

**Base URL**: `http://localhost:8000` (or your deployment URL)

---

## Authentication

All API endpoints require authentication via API key. Include the key in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_your_api_key_here" \
  -F "file=@audio.mp3"
```

### Getting an API Key

```bash
# Create your first admin key
python -m dalston.gateway.cli create-admin-key --name "My Key"
```

### API Key Scopes

| Scope | Permissions |
|-------|-------------|
| `jobs:read` | GET transcription jobs |
| `jobs:write` | POST/DELETE transcription jobs |
| `realtime` | WebSocket streaming access |
| `webhooks` | Manage webhook configurations |
| `admin` | All permissions + key management |

### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid API key |
| `403 Forbidden` | API key lacks required scope |
| `429 Too Many Requests` | Rate limit exceeded |

### Key Management Endpoints

| Method | Endpoint | Scope | Description |
|--------|----------|-------|-------------|
| POST | `/auth/keys` | `admin` | Create new API key |
| GET | `/auth/keys` | `admin` | List API keys |
| GET | `/auth/keys/{id}` | `admin` | Get key details |
| DELETE | `/auth/keys/{id}` | `admin` | Revoke key |
| GET | `/auth/me` | any | Get current key info |

---

## Rate Limits

Rate limits are applied per tenant (API key) to ensure fair usage.

### Default Limits

| Limit | Default | Environment Variable |
|-------|---------|---------------------|
| Requests per minute | 600 | `RATE_LIMIT_REQUESTS_PER_MINUTE` |
| Concurrent batch jobs | 10 | `RATE_LIMIT_CONCURRENT_JOBS` |
| Concurrent realtime sessions | 5 | `RATE_LIMIT_CONCURRENT_SESSIONS` |
| Upload size | 500 MB | — |
| Audio duration per job | 4 hours | — |

### Rate Limit Response

When a rate limit is exceeded, the API returns `429 Too Many Requests`:

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Rate limit exceeded"
  }
}
```

Or for concurrent limits:

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Concurrent job limit exceeded (10 max)"
  }
}
```

### Rate Limit Headers

Rate limit responses include headers:

```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 42
Retry-After: 32              # Only on 429 responses (requests/minute)
```

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum allowed in window |
| `X-RateLimit-Remaining` | Remaining in current window |
| `Retry-After` | Seconds to wait before retrying (429 only) |

### Limit Types

| Limit | Meaning | Resolution |
|-------|---------|------------|
| Requests per minute | Too many API calls | Wait `Retry-After` seconds |
| Concurrent jobs | Too many jobs processing | Wait for jobs to complete |
| Concurrent sessions | Too many realtime sessions | Close sessions or wait |
| Upload size | File too large | Reduce file size or split audio |
| Audio duration | Audio too long | Split into shorter segments |

---

## Endpoints Summary

### Dalston Native API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/audio/transcriptions` | Submit audio for transcription |
| GET | `/v1/audio/transcriptions/{id}` | Get job status and results |
| GET | `/v1/audio/transcriptions` | List recent jobs |
| DELETE | `/v1/audio/transcriptions/{id}` | Cancel job |

### ElevenLabs Compatible API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/speech-to-text` | Submit audio (ElevenLabs format) |
| GET | `/v1/speech-to-text/transcripts/{transcription_id}` | Get transcript (ElevenLabs format) |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| WS `/v1/audio/transcriptions/stream` | Realtime streaming (Dalston format) |
| WS `/v1/speech-to-text/realtime` | Realtime streaming (ElevenLabs format) |

### Management API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/models` | List available models |
| GET | `/v1/models/{id}` | Get model details |
| POST | `/v1/models/{id}/load` | Load model into memory |
| POST | `/v1/models/{id}/unload` | Unload model from memory |
| GET | `/v1/status` | System health and capacity |

### Webhook Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/webhooks` | List configured webhooks |
| POST | `/v1/webhooks` | Create webhook configuration |
| GET | `/v1/webhooks/{id}` | Get webhook details |
| PATCH | `/v1/webhooks/{id}` | Update webhook |
| DELETE | `/v1/webhooks/{id}` | Delete webhook |

---

# ElevenLabs Compatible API

These endpoints match ElevenLabs Speech-to-Text API conventions for drop-in compatibility.

---

## POST /v1/speech-to-text

Submit audio for transcription using ElevenLabs-compatible parameters.

### Request

**Content-Type**: `multipart/form-data`

#### Audio Input (one required)

| Field | Type | Description |
|-------|------|-------------|
| `file` | binary | Audio file upload (max 3GB) |
| `cloud_storage_url` | string | HTTPS URL to audio file (S3/GCS presigned URL) |

#### Core Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model_id` | string | `"scribe_v1"` | Model: `"scribe_v1"` or `"scribe_v2"` |
| `language_code` | string | `null` | ISO 639-1/3 code, auto-detect if null |

#### Speaker Detection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `diarize` | boolean | `false` | Enable speaker diarization |
| `num_speakers` | integer | `null` | Expected speaker count (1-32) |
| `diarization_threshold` | float | `0.5` | Sensitivity 0.0-2.0 (lower = more speakers) |
| `use_multi_channel` | boolean | `false` | Treat each audio channel as separate speaker |

#### Timestamps & Output

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timestamps_granularity` | string | `"word"` | `"none"`, `"word"`, `"character"` |
| `additional_formats` | array | `[]` | Export: `"srt"`, `"webvtt"`, `"txt"`, `"docx"`, `"pdf"` |

#### Audio Analysis

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tag_audio_events` | boolean | `false` | Detect laughter, applause, music, etc. |
| `entity_detection` | string/array | `null` | Detect PII/PHI: `"pii"`, `"phi"`, `["pii", "phi"]` |

#### Transcription Hints

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `keyterms` | array | `[]` | Bias terms (max 100 terms, 50 chars each) |

#### Processing Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `temperature` | float | `0.0` | Output randomness 0.0-2.0 |
| `seed` | integer | `null` | Random seed for reproducibility |

#### Async / Webhook

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `webhook` | boolean | `false` | Enable async mode with webhook callback |
| `webhook_id` | string | `null` | Pre-configured webhook ID |
| `webhook_metadata` | object | `null` | Custom data echoed in callback |

### Model ID Mapping

| ElevenLabs Model | Dalston Backend | Description |
|------------------|-----------------|-------------|
| `scribe_v1` | WhisperX base | Fast, good accuracy |
| `scribe_v2` | WhisperX large-v3 | Best accuracy |

### Response (Synchronous)

For audio < 5 minutes without `webhook=true`:

```json
{
  "transcription_id": "trans_abc123def456",
  "language_code": "en",
  "language_confidence": 0.98,
  "text": "Welcome to the show. Thanks for having me.",
  "words": [
    {
      "text": "Welcome",
      "start": 0.0,
      "end": 0.4,
      "type": "word",
      "speaker_id": "speaker_1",
      "logprob": -0.12
    },
    {
      "text": " ",
      "start": 0.4,
      "end": 0.45,
      "type": "spacing"
    },
    {
      "text": "to",
      "start": 0.45,
      "end": 0.55,
      "type": "word",
      "speaker_id": "speaker_1",
      "logprob": -0.08
    },
    {
      "text": "(laughter)",
      "start": 5.2,
      "end": 6.8,
      "type": "audio_event",
      "speaker_id": null,
      "logprob": -0.15
    }
  ],
  "entities": [
    {
      "text": "John Smith",
      "entity_type": "pii",
      "start_char": 45,
      "end_char": 55
    }
  ],
  "additional_formats": {
    "srt": "https://dalston.example.com/v1/speech-to-text/transcripts/trans_abc123def456/export/srt",
    "webvtt": "https://dalston.example.com/v1/speech-to-text/transcripts/trans_abc123def456/export/webvtt"
  }
}
```

### Response (Asynchronous)

For audio ≥ 5 minutes or when `webhook=true`:

```json
{
  "message": "Transcription submitted",
  "request_id": "req_xyz789",
  "transcription_id": "trans_abc123def456"
}
```

### Word Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Word text, punctuation, or event label |
| `start` | float | Start time in seconds |
| `end` | float | End time in seconds |
| `type` | string | `"word"`, `"spacing"`, `"audio_event"` |
| `speaker_id` | string | `"speaker_1"`, `"speaker_2"`, etc. (null for events) |
| `logprob` | float | Log probability |
| `characters` | array | Character-level timing (if `timestamps_granularity="character"`) |

### Entity Object Schema

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Detected entity text |
| `entity_type` | string | `"pii"`, `"phi"`, `"pci"`, `"offensive"` |
| `start_char` | integer | Start character position in `text` |
| `end_char` | integer | End character position in `text` |

### Example

```bash
# File upload with diarization
curl -X POST https://api.dalston.example.com/v1/speech-to-text \
  -F "file=@interview.wav" \
  -F "model_id=scribe_v2" \
  -F "language_code=en" \
  -F "diarize=true" \
  -F "num_speakers=2" \
  -F "timestamps_granularity=word"

# URL input with keyterms and async webhook
curl -X POST https://api.dalston.example.com/v1/speech-to-text \
  -F "cloud_storage_url=https://bucket.s3.amazonaws.com/audio.mp3?X-Amz-..." \
  -F "model_id=scribe_v1" \
  -F "language_code=en" \
  -F 'keyterms=["PostgreSQL", "Kubernetes", "FastAPI"]' \
  -F "webhook=true" \
  -F "webhook_id=wh_prod_main"
```

---

## GET /v1/speech-to-text/transcripts/{transcription_id}

Get transcript status and results.

### Response (Processing)

```json
{
  "transcription_id": "trans_abc123def456",
  "status": "processing",
  "progress_percent": 45,
  "stage": "transcribing",
  "estimated_remaining_seconds": 60
}
```

### Response (Completed)

Same schema as synchronous POST response above.

### Response (Failed)

```json
{
  "transcription_id": "trans_abc123def456",
  "status": "failed",
  "error": {
    "code": "processing_error",
    "message": "Audio file is corrupted or unsupported format"
  }
}
```

---

## Webhook Callback (ElevenLabs Format)

When `webhook=true` or `webhook_id` is provided:

### Headers

```
Content-Type: application/json
X-Dalston-Signature: sha256=a1b2c3d4...
X-Dalston-Timestamp: 1706443350
```

### Body (Completed)

```json
{
  "event": "transcription.completed",
  "transcription_id": "trans_abc123def456",
  "status": "completed",
  "timestamp": "2025-01-28T12:02:30Z",
  "language_code": "en",
  "text": "Full transcript...",
  "words": [...],
  "webhook_metadata": {
    "episode_id": "ep_123"
  }
}
```

### Body (Failed)

```json
{
  "event": "transcription.failed",
  "transcription_id": "trans_abc123def456",
  "status": "failed",
  "error": {
    "code": "processing_error",
    "message": "Transcription failed"
  },
  "timestamp": "2025-01-28T12:01:15Z",
  "webhook_metadata": {
    "episode_id": "ep_123"
  }
}
```

---

# Dalston Native API

These endpoints use Dalston's own conventions with additional features.

---

## POST /v1/audio/transcriptions

Submit audio for transcription.

### Request

**Content-Type**: `multipart/form-data`

#### Audio Input (one required)

| Field | Type | Description |
|-------|------|-------------|
| `file` | binary | Audio file upload |
| `audio_url` | string | URL to audio file (S3/GCS presigned URL, HTTPS) |

#### Core Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `language` | string | `"auto"` | ISO 639-1 language code or `"auto"` for detection |
| `model_id` | string | `null` | Model/engine preference (see below) |

#### Speaker Detection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `speaker_detection` | string | `"none"` | `"none"`, `"diarize"`, `"per_channel"` |
| `num_speakers` | integer | `null` | Exact speaker count hint (1-32) |
| `min_speakers` | integer | `null` | Minimum speakers for auto-detection (1-32) |
| `max_speakers` | integer | `null` | Maximum speakers for auto-detection (1-32) |
| `diarization_threshold` | float | `0.5` | Sensitivity 0.0-2.0 |

#### Timestamps & Output

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timestamps_granularity` | string | `"word"` | `"none"`, `"segment"`, `"word"` |
| `additional_formats` | array | `[]` | Export: `"srt"`, `"vtt"`, `"txt"`, `"json"` |

#### Enrichment

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `detect_emotions` | boolean | `false` | Add emotion labels to segments |
| `detect_events` | boolean | `false` | Detect laughter, applause, music, etc. |
| `llm_cleanup` | boolean | `false` | LLM-based error correction and formatting |

#### Transcription Hints

| Field            | Type   | Default | Description                                                  |
|------------------|--------|---------|--------------------------------------------------------------|
| `initial_prompt` | string | `null`  | Domain vocabulary hints to improve accuracy (max 1000 chars) |
| `keyterms`       | array  | `[]`    | Bias terms (max 100 terms, 50 chars each)                    |

#### Processing Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `temperature` | float | `0.0` | Output randomness 0.0-2.0 |
| `seed` | integer | `null` | Random seed for reproducibility |

#### Webhook / Async

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `webhook_url` | string | `null` | URL for completion callback |
| `webhook_id` | string | `null` | Pre-configured webhook ID |
| `webhook_metadata` | object | `null` | Custom data echoed in callback |

### Model ID Options

| Value | Description |
|-------|-------------|
| `null` (default) | Orchestrator chooses optimal engine |
| `"whisper-large-v3"` | Most accurate multilingual (99 languages) |
| `"whisper-large-v2"` | Previous generation large model |
| `"whisper-medium"` | Balanced accuracy and speed |
| `"whisper-small"` | Fast multilingual transcription |
| `"whisper-base"` | Very fast, lower accuracy |
| `"whisper-tiny"` | Fastest, minimal accuracy |
| `"distil-whisper"` | Fast English-only, near large-v3 accuracy |
| `"parakeet-110m"` | Lightweight English-only, low memory |
| `"parakeet-0.6b"` | Fast English-only with native streaming |
| `"parakeet-1.1b"` | Balanced English-only with native streaming |
| `"fast"` | Alias for distil-whisper |
| `"accurate"` | Alias for whisper-large-v3 |
| `"parakeet"` | Alias for parakeet-110m |

### Response (Immediate)

```json
{
  "id": "job_abc123",
  "status": "pending",
  "created_at": "2025-01-28T12:00:00Z"
}
```

### Example

```bash
# Basic transcription with diarization
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@interview.wav" \
  -F "language=en" \
  -F "speaker_detection=diarize" \
  -F "num_speakers=2" \
  -F "timestamps_granularity=word"

# With domain vocabulary hints and speaker range
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@medical-consultation.wav" \
  -F "initial_prompt=cardiology, ECG, arrhythmia, myocardial infarction" \
  -F "speaker_detection=diarize" \
  -F "min_speakers=2" \
  -F "max_speakers=4"
```

---

## GET /v1/audio/transcriptions/{id}

Get job status and results.

### Response (Pending/Running)

```json
{
  "id": "job_abc123",
  "status": "running",
  "progress": 45,
  "current_stage": "transcribe",
  "created_at": "2025-01-28T12:00:00Z",
  "started_at": "2025-01-28T12:00:01Z"
}
```

### Response (Completed)

```json
{
  "id": "job_abc123",
  "status": "completed",
  "created_at": "2025-01-28T12:00:00Z",
  "completed_at": "2025-01-28T12:02:30Z",
  "processing_time_seconds": 150.0,

  "language_code": "en",
  "language_confidence": 0.98,
  "text": "Welcome to the show. Thanks for having me...",

  "segments": [
    {
      "id": "seg_001",
      "start": 0.0,
      "end": 3.5,
      "text": "Welcome to the show.",
      "speaker": "SPEAKER_00",
      "words": [
        { "text": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98 },
        { "text": "to", "start": 0.45, "end": 0.55, "confidence": 0.99 },
        { "text": "the", "start": 0.6, "end": 0.7, "confidence": 0.99 },
        { "text": "show.", "start": 0.75, "end": 1.1, "confidence": 0.97 }
      ],
      "emotion": "positive",
      "emotion_confidence": 0.85
    }
  ],

  "speakers": [
    { "id": "SPEAKER_00", "label": "Host", "duration_seconds": 45.2 },
    { "id": "SPEAKER_01", "label": "Guest", "duration_seconds": 102.8 }
  ],

  "additional_formats": {
    "srt": "http://localhost:8000/v1/audio/transcriptions/job_abc123/export/srt",
    "vtt": "http://localhost:8000/v1/audio/transcriptions/job_abc123/export/vtt"
  },

  "model_used": "whisperx-large-v3"
}
```

### Response (Failed)

```json
{
  "id": "job_abc123",
  "status": "failed",
  "error": {
    "code": "engine_error",
    "message": "Transcription engine failed: CUDA out of memory"
  },
  "created_at": "2025-01-28T12:00:00Z",
  "failed_at": "2025-01-28T12:00:15Z"
}
```

---

## GET /v1/audio/transcriptions

List recent transcription jobs.

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `20` | Max results (1-100) |
| `offset` | integer | `0` | Pagination offset |
| `status` | string | `null` | Filter: `pending`, `running`, `completed`, `failed` |

### Response

```json
{
  "jobs": [
    {
      "id": "job_abc123",
      "status": "completed",
      "created_at": "2025-01-28T12:00:00Z",
      "completed_at": "2025-01-28T12:02:30Z"
    },
    {
      "id": "job_def456",
      "status": "running",
      "progress": 67,
      "created_at": "2025-01-28T12:05:00Z"
    }
  ],
  "total": 47,
  "limit": 20,
  "offset": 0
}
```

---

## DELETE /v1/audio/transcriptions/{id}

Cancel a pending or running job.

### Response

```json
{
  "id": "job_abc123",
  "status": "cancelled"
}
```

---

# Management API

---

## GET /v1/models

List available transcription models.

### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "whisper-large-v3",
      "object": "model",
      "name": "Whisper Large v3",
      "description": "Most accurate multilingual model, 99 languages",
      "capabilities": {
        "languages": 99,
        "streaming": false,
        "word_timestamps": true
      },
      "tier": "accurate"
    },
    {
      "id": "parakeet-0.6b",
      "object": "model",
      "name": "Parakeet 0.6B",
      "description": "Fast English-only with native streaming, low latency",
      "capabilities": {
        "languages": 1,
        "streaming": true,
        "word_timestamps": true
      },
      "tier": "fast"
    }
  ],
  "aliases": {
    "fast": "distil-whisper",
    "accurate": "whisper-large-v3",
    "parakeet": "parakeet-110m"
  }
}
```

---

## GET /v1/status

System health and capacity.

### Response

```json
{
  "status": "healthy",
  "uptime_seconds": 86400,
  "gpu_available": true,
  "gpu_memory_used_mb": 2048,
  "gpu_memory_total_mb": 8192,
  "models_loaded": ["whisperx-base"],
  "active_jobs": 2,
  "queue_depth": 5,
  "backend": "faster-whisper"
}
```

---

# Export Formats

## GET /v1/audio/transcriptions/{id}/export/{format}

## GET /v1/speech-to-text/transcripts/{transcription_id}/export/{format}

Export transcript in specified format.

### Formats

| Format | Content-Type | Description |
|--------|--------------|-------------|
| `srt` | `text/plain` | SubRip subtitle format |
| `vtt` / `webvtt` | `text/vtt` | WebVTT subtitle format |
| `txt` | `text/plain` | Plain text (no timestamps) |
| `json` | `application/json` | Full transcript JSON |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document |
| `pdf` | `application/pdf` | PDF document |

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `include_speakers` | boolean | `true` | Include speaker labels |
| `max_line_length` | integer | `42` | Max characters per subtitle line |
| `max_lines` | integer | `2` | Max lines per subtitle block |

---

# Webhook Management

## POST /v1/webhooks

Create a pre-configured webhook.

### Request

```json
{
  "name": "Production Callback",
  "url": "https://myapp.com/callbacks/transcription",
  "secret": "whsec_xxxxxxxxxxxxx",
  "events": ["transcription.completed", "transcription.failed"],
  "enabled": true
}
```

### Response

```json
{
  "id": "wh_prod_main",
  "name": "Production Callback",
  "url": "https://myapp.com/callbacks/transcription",
  "events": ["transcription.completed", "transcription.failed"],
  "enabled": true,
  "created_at": "2025-01-28T12:00:00Z"
}
```

---

## Signature Verification

Verify webhook authenticity using HMAC-SHA256:

```python
import hmac
import hashlib

def verify_webhook(payload: bytes, signature: str, timestamp: str, secret: str) -> bool:
    signed_payload = f"{timestamp}.{payload.decode()}"
    expected = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

---

# Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Audio file is required",
    "details": {}
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `invalid_request` | 400 | Invalid request parameters |
| `unsupported_format` | 400 | Audio format not supported |
| `file_too_large` | 400 | File exceeds size limit |
| `job_not_found` | 404 | Job/transcript ID does not exist |
| `model_unavailable` | 503 | Requested model not available |
| `processing_error` | 500 | Transcription processing failed |
| `internal_error` | 500 | Internal server error |

---

# Supported Audio Formats

| Format | Extensions | Notes |
|--------|------------|-------|
| WAV | .wav | Recommended |
| MP3 | .mp3 | Converted internally |
| MP4/M4A | .mp4, .m4a | Audio extracted |
| FLAC | .flac | Lossless |
| OGG | .ogg, .opus | Supported |
| WebM | .webm | Audio extracted |

**Maximum file size**: 3GB

**Maximum duration**: 4 hours

---

# Parameter Mapping Reference

For clients migrating between APIs:

| ElevenLabs (`/v1/speech-to-text`) | Dalston (`/v1/audio/transcriptions`) |
|-----------------------------------|--------------------------------------|
| `model_id` = `scribe_v1` | `model_id` = `whisperx-base` |
| `model_id` = `scribe_v2` | `model_id` = `whisperx-large-v3` |
| `language_code` | `language` |
| `cloud_storage_url` | `audio_url` |
| `keyterms` | `initial_prompt` |
| `diarize` = `true` | `speaker_detection` = `"diarize"` |
| `num_speakers` | `num_speakers`, `min_speakers`, `max_speakers` |
| `use_multi_channel` = `true` | `speaker_detection` = `"per_channel"` |
| `tag_audio_events` | `detect_events` |
| `webhook` = `true` | *(uses webhook_url or webhook_id)* |
| `timestamps_granularity` = `"character"` | *(not supported, falls back to word)* |
| Response: `transcription_id` | Response: `id` |
| Response: `speaker_1` | Response: `SPEAKER_00` |
| Response: `word[].text` | Response: `word.text` |
| Response: `segment.speaker_id` | Response: `segment.speaker` |
