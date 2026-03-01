# M38: OpenAI Compatibility Layer

| | |
|---|---|
| **Goal** | Drop-in replacement for OpenAI Audio Transcription API |
| **Duration** | 3-4 days |
| **Dependencies** | M6 complete (real-time working), M8 complete (ElevenLabs pattern established) |
| **Deliverable** | OpenAI clients work unchanged by pointing to Dalston |
| **Status** | In Progress (Batch complete, Real-time pending) |

## User Story

> *"As a developer using OpenAI's transcription API, I can switch to Dalston by just changing the base URL."*

---

## OpenAI API Reference

We implement compatibility with:

- `POST /v1/audio/transcriptions` — Batch transcription (OpenAI format)
- `WS /v1/realtime?intent=transcription` — Real-time streaming transcription

---

## Steps

### 38.1: Batch Transcription Endpoint ✅

**Endpoint:** `POST /v1/audio/transcriptions` (OpenAI-compatible route)

Since Dalston already uses `/v1/audio/transcriptions` for its native API, we implement OpenAI compatibility by detecting the request format and responding accordingly.

**Detection strategy:**

- If `response_format` is present with OpenAI values (`json`, `text`, `srt`, `verbose_json`, `vtt`) → OpenAI mode
- If `model` is an OpenAI model ID (`whisper-1`, `gpt-4o-transcribe`, etc.) → OpenAI mode
- Otherwise → Dalston native mode

**OpenAI Request Parameters:**

| OpenAI Param | Type | Required | Description |
|--------------|------|----------|-------------|
| `file` | file | Yes | Audio file (max 25MB for OpenAI compat) |
| `model` | string | Yes | `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` |
| `language` | string | No | ISO-639-1 language code |
| `prompt` | string | No | Vocabulary hints (max 224 tokens) |
| `response_format` | string | No | `json`, `text`, `srt`, `verbose_json`, `vtt` |
| `temperature` | float | No | Randomness 0.0-1.0 |
| `timestamp_granularities[]` | array | No | `word`, `segment`, or both (requires `verbose_json`) |

**OpenAI → Dalston Parameter Mapping:**

| OpenAI Param | Dalston Param | Notes |
|--------------|---------------|-------|
| `model` = `whisper-1` | `model_id` = `whisper-large-v2` | OpenAI's Whisper is V2 |
| `model` = `gpt-4o-transcribe` | `model_id` = `whisper-large-v3` | Best accuracy model |
| `model` = `gpt-4o-mini-transcribe` | `model_id` = `distil-whisper` | Fast model |
| `language` | `language` | Direct mapping |
| `prompt` | `initial_prompt` | Direct mapping |
| `temperature` | `temperature` | Direct mapping |
| `timestamp_granularities[]` = `["word"]` | `timestamps_granularity` = `word` | Word timestamps |
| `timestamp_granularities[]` = `["segment"]` | `timestamps_granularity` = `segment` | Segment timestamps |
| `response_format` | *(handled in response transformation)* | |

**OpenAI Response Formats:**

#### `response_format=json` (default)

```json
{
  "text": "Hello, how are you today?"
}
```

#### `response_format=text`

```
Hello, how are you today?
```

#### `response_format=verbose_json`

```json
{
  "task": "transcribe",
  "language": "english",
  "duration": 2.5,
  "text": "Hello, how are you today?",
  "segments": [
    {
      "id": 0,
      "seek": 0,
      "start": 0.0,
      "end": 2.5,
      "text": " Hello, how are you today?",
      "tokens": [50364, 2425, 11, 577, 366, 291, 965, 30, 50489],
      "temperature": 0.0,
      "avg_logprob": -0.45,
      "compression_ratio": 0.95,
      "no_speech_prob": 0.02
    }
  ],
  "words": [
    { "word": "Hello", "start": 0.0, "end": 0.4 },
    { "word": ",", "start": 0.4, "end": 0.4 },
    { "word": "how", "start": 0.5, "end": 0.7 },
    { "word": "are", "start": 0.8, "end": 1.0 },
    { "word": "you", "start": 1.1, "end": 1.3 },
    { "word": "today", "start": 1.4, "end": 1.8 },
    { "word": "?", "start": 1.8, "end": 1.8 }
  ]
}
```

#### `response_format=srt`

```
1
00:00:00,000 --> 00:00:02,500
Hello, how are you today?
```

#### `response_format=vtt`

```
WEBVTT

00:00:00.000 --> 00:00:02.500
Hello, how are you today?
```

**Deliverables:**

- Detect OpenAI-style requests by parameter inspection
- Map all OpenAI parameters to Dalston equivalents
- Transform Dalston response to OpenAI format based on `response_format`
- Enforce 25MB file size limit for OpenAI compat mode
- Return OpenAI-style errors

---

### 38.2: OpenAI Error Responses ✅

OpenAI uses a specific error format:

```json
{
  "error": {
    "message": "Invalid file format. Supported formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm",
    "type": "invalid_request_error",
    "param": "file",
    "code": "invalid_file_format"
  }
}
```

**Error Type Mapping:**

| Dalston Error | OpenAI Type | OpenAI Code |
|---------------|-------------|-------------|
| `invalid_request` | `invalid_request_error` | `invalid_request` |
| `unsupported_format` | `invalid_request_error` | `invalid_file_format` |
| `file_too_large` | `invalid_request_error` | `file_too_large` |
| `model_unavailable` | `invalid_request_error` | `model_not_found` |
| `processing_error` | `server_error` | `processing_failed` |
| `internal_error` | `server_error` | `internal_error` |
| `rate_limit_exceeded` | `rate_limit_error` | `rate_limit_exceeded` |

---

### 38.3: Real-time WebSocket Endpoint

**Endpoint:** `WS /v1/realtime?intent=transcription`

OpenAI's Realtime API uses a different WebSocket URL and event protocol than ElevenLabs.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `intent` | string | required | Must be `transcription` |
| `model` | string | `gpt-4o-transcribe` | Transcription model |

**Headers:**

```
Authorization: Bearer <api_key>
OpenAI-Beta: realtime=v1
```

Note: Dalston will accept both header auth and query param auth for compatibility.

**OpenAI → Dalston Model Mapping:**

| OpenAI Model | Dalston Backend |
|--------------|-----------------|
| `gpt-4o-transcribe` | Parakeet 1.1B |
| `gpt-4o-mini-transcribe` | Parakeet 0.6B |
| `whisper-1` | Whisper streaming (VAD-chunked) |

---

### 38.4: Real-time Protocol Translation

**Session Configuration (Client → Server):**

```json
{
  "type": "transcription_session.update",
  "session": {
    "input_audio_format": "pcm16",
    "input_audio_transcription": {
      "model": "gpt-4o-transcribe",
      "language": "en",
      "prompt": "Technical vocabulary: Kubernetes, FastAPI"
    },
    "turn_detection": {
      "type": "server_vad",
      "threshold": 0.5,
      "prefix_padding_ms": 300,
      "silence_duration_ms": 500
    },
    "input_audio_noise_reduction": {
      "type": "near_field"
    }
  }
}
```

**Dalston mapping:**

| OpenAI Field | Dalston Field |
|--------------|---------------|
| `input_audio_format` = `pcm16` | `encoding` = `pcm_s16le` |
| `input_audio_format` = `g711_ulaw` | `encoding` = `mulaw` |
| `input_audio_format` = `g711_alaw` | `encoding` = `alaw` |
| `input_audio_transcription.model` | `model` |
| `input_audio_transcription.language` | `language` |
| `input_audio_transcription.prompt` | `initial_prompt` |
| `turn_detection.type` = `server_vad` | `enable_vad` = `true` |
| `turn_detection.threshold` | `vad_threshold` |
| `turn_detection.silence_duration_ms` | `min_silence_duration_ms` |
| `turn_detection` = `null` | `enable_vad` = `false` |

**Audio Input (Client → Server):**

```json
{
  "type": "input_audio_buffer.append",
  "audio": "<base64-encoded-audio>"
}
```

Maps to Dalston's `input_audio_chunk` or binary frame.

**Commit Audio (Client → Server):**

```json
{
  "type": "input_audio_buffer.commit"
}
```

Maps to Dalston's `{ "type": "flush" }`.

**Clear Buffer (Client → Server):**

```json
{
  "type": "input_audio_buffer.clear"
}
```

New message type - clears pending audio buffer.

**Server → Client Events:**

| OpenAI Event | Dalston Event | Description |
|--------------|---------------|-------------|
| `transcription_session.created` | `session.begin` | Session started |
| `transcription_session.updated` | *(new)* | Config acknowledged |
| `input_audio_buffer.speech_started` | `vad.speech_start` | VAD detected speech |
| `input_audio_buffer.speech_stopped` | `vad.speech_end` | VAD detected silence |
| `input_audio_buffer.committed` | *(ack of commit)* | Buffer committed |
| `conversation.item.input_audio_transcription.delta` | `transcript.partial` | Incremental transcript |
| `conversation.item.input_audio_transcription.completed` | `transcript.final` | Final transcript |
| `error` | `error` | Error occurred |

**Transcription Delta Event:**

```json
{
  "type": "conversation.item.input_audio_transcription.delta",
  "event_id": "evt_abc123",
  "item_id": "item_xyz",
  "content_index": 0,
  "delta": "Hello, how"
}
```

**Transcription Completed Event:**

```json
{
  "type": "conversation.item.input_audio_transcription.completed",
  "event_id": "evt_abc456",
  "item_id": "item_xyz",
  "content_index": 0,
  "transcript": "Hello, how are you today?",
  "logprobs": [
    { "token": "Hello", "logprob": -0.12 },
    { "token": ",", "logprob": -0.05 }
  ]
}
```

**Deliverables:**

- New WebSocket endpoint at `/v1/realtime`
- Accept `intent=transcription` query param
- Parse and translate OpenAI event format
- Generate OpenAI event IDs (`evt_xxx`, `item_xxx`)
- Support both header (`Authorization: Bearer`) and query param auth

---

### 38.5: Translation Endpoint ✅

**Endpoint:** `POST /v1/audio/translations`

OpenAI provides a translation endpoint that transcribes audio into English regardless of source language.

**Request (same as transcription):**

```
POST /v1/audio/translations
Content-Type: multipart/form-data

file: (audio file)
model: whisper-1
prompt: (optional)
response_format: json
temperature: 0
```

**Response:**

```json
{
  "text": "Hello, how are you today?"
}
```

**Implementation:**

- Accept same parameters as transcription
- Force `language=en` in Dalston backend
- Enable translation mode in Whisper engine

**Note:** This is lower priority as translation is less commonly used.

---

## Model Compatibility Matrix

| OpenAI Model | Dalston Engine | Streaming | Notes |
|--------------|----------------|-----------|-------|
| `whisper-1` | whisper-large-v2 | No | OpenAI's original Whisper |
| `gpt-4o-transcribe` | whisper-large-v3 | Batch only | Best accuracy |
| `gpt-4o-mini-transcribe` | distil-whisper | Batch only | Fast, English-focused |
| `gpt-4o-transcribe` (realtime) | parakeet-1.1b | Yes | Real-time streaming |
| `gpt-4o-mini-transcribe` (realtime) | parakeet-0.6b | Yes | Fast real-time |

---

## File Structure

```
dalston/gateway/api/v1/
├── openai_audio.py           # OpenAI-compatible batch routes
├── openai_realtime.py        # OpenAI-compatible WebSocket
└── compat/
    ├── __init__.py
    ├── openai_types.py       # OpenAI request/response models
    ├── openai_translator.py  # Parameter and response translation
    └── openai_errors.py      # Error format translation
```

---

## Verification

### Test with OpenAI SDK

```python
from openai import OpenAI

# Point to Dalston instead of OpenAI
client = OpenAI(
    api_key="dk_your_dalston_key",  # or "not-needed" if auth disabled
    base_url="http://localhost:8000/v1"
)

# Batch transcription
with open("audio.mp3", "rb") as f:
    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=f,
        response_format="verbose_json",
        timestamp_granularities=["word"]
    )

print(transcript.text)
print(transcript.words)
```

### Test Real-time (WebSocket)

```python
import websocket
import json
import base64

ws = websocket.create_connection(
    "ws://localhost:8000/v1/realtime?intent=transcription",
    header=["Authorization: Bearer dk_your_key", "OpenAI-Beta: realtime=v1"]
)

# Configure session
ws.send(json.dumps({
    "type": "transcription_session.update",
    "session": {
        "input_audio_format": "pcm16",
        "input_audio_transcription": {
            "model": "gpt-4o-transcribe",
            "language": "en"
        }
    }
}))

# Send audio
audio_bytes = read_audio_file()
ws.send(json.dumps({
    "type": "input_audio_buffer.append",
    "audio": base64.b64encode(audio_bytes).decode()
}))

# Commit
ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

# Receive transcript
while True:
    msg = json.loads(ws.recv())
    if msg["type"] == "conversation.item.input_audio_transcription.completed":
        print(msg["transcript"])
        break
```

### Test with curl

```bash
# Basic transcription
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_your_key" \
  -F "file=@audio.mp3" \
  -F "model=whisper-1"

# With timestamps
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_your_key" \
  -F "file=@audio.mp3" \
  -F "model=gpt-4o-transcribe" \
  -F "response_format=verbose_json" \
  -F "timestamp_granularities[]=word"
```

---

## Checkpoint

- [x] **POST /v1/audio/transcriptions** detects and handles OpenAI-style requests
- [x] **response_format** outputs correct format (json, text, srt, verbose_json, vtt)
- [x] **timestamp_granularities** populates word/segment timestamps
- [x] **Model mapping** works for whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe
- [x] **Error responses** match OpenAI format
- [ ] **WS /v1/realtime** accepts transcription sessions
- [ ] **Real-time protocol** translates OpenAI events bidirectionally
- [x] **OpenAI Python SDK** works unchanged
- [x] **POST /v1/audio/translations** endpoint for audio-to-English translation

**Next**: [M39: Translation Endpoint](M39-translation.md) (optional) or other priorities

---

## Implementation Notes

### Coexistence with Dalston Native API

Since both Dalston native and OpenAI-compatible APIs use `/v1/audio/transcriptions`, we need request disambiguation:

1. **Check for OpenAI-specific parameters:**
   - `response_format` with OpenAI values
   - `model` with OpenAI model IDs
   - `timestamp_granularities[]` array parameter

2. **Check request structure:**
   - OpenAI: `file` (required), `model` (required)
   - Dalston: `file` OR `audio_url`, `model_id` (optional)

3. **Response routing:**
   - OpenAI mode: Synchronous response in requested format
   - Dalston mode: Job ID response (async by default)

### WebSocket Endpoint Separation

OpenAI uses `/v1/realtime` while:

- Dalston native uses `/v1/audio/transcriptions/stream`
- ElevenLabs uses `/v1/speech-to-text/realtime`

This natural separation avoids conflicts.

### Authentication Compatibility

OpenAI expects `Authorization: Bearer sk-xxx` header. Dalston should accept:

- `Authorization: Bearer dk_xxx` (Dalston keys)
- `Authorization: Bearer sk-xxx` (treat as Dalston key, ignore `sk-` prefix)
- Query param: `?api_key=dk_xxx` (for WebSocket compatibility)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| OpenAI API evolves | Pin to documented v1 API, add version detection |
| File size mismatch (OpenAI 25MB vs Dalston 3GB) | Enforce 25MB in OpenAI compat mode |
| Token auth differences | Accept both header formats |
| Missing features (GPT-4o-audio) | Document scope as transcription-only |
| Real-time event ordering | Generate sequential event IDs |
