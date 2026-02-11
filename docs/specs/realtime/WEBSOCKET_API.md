# WebSocket API Reference

## Overview

Dalston provides real-time streaming transcription via WebSocket with two endpoint variants:

- **ElevenLabs Compatible** (`/v1/speech-to-text/realtime`) — Drop-in replacement for ElevenLabs WebSocket API
- **Dalston Native** (`/v1/audio/transcriptions/stream`) — Dalston's own conventions with binary audio

Both provide sub-500ms latency for real-time transcription.

---

# ElevenLabs Compatible WebSocket

## Endpoint

```
WS /v1/speech-to-text/realtime
```

This endpoint uses ElevenLabs message formats and parameter names by default.

---

## Authentication

WebSocket connections require an API key passed as a query parameter:

```
wss://api.dalston.example.com/v1/speech-to-text/realtime?api_key=dk_your_key_here&...
```

The API key must have the `realtime` scope. If authentication fails, the connection is closed immediately with code `4001` (invalid key) or `4003` (missing scope).

---

## Connection

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | string | **required** | API key with `realtime` scope |
| `model_id` | string | `"scribe_v1"` | Model: `"scribe_v1"` or `"scribe_v2"` |
| `audio_format` | string | `"pcm_16000"` | Audio encoding (see table below) |
| `language_code` | string | `"auto"` | ISO 639-1 code or `"auto"` |
| `commit_strategy` | string | `"vad"` | `"vad"` (auto-commit) or `"manual"` |
| `include_timestamps` | boolean | `false` | Include word-level timing |
| `include_language_detection` | boolean | `false` | Include detected language in responses |
| `vad_silence_threshold_secs` | float | `1.5` | Silence duration to trigger commit (VAD mode) |
| `vad_threshold` | float | `0.4` | VAD sensitivity 0.0-1.0 (lower = more sensitive) |

### Audio Format Values

| Value | Description |
|-------|-------------|
| `pcm_16000` | 16-bit PCM, 16kHz, mono (recommended) |
| `pcm_8000` | 16-bit PCM, 8kHz, mono |
| `pcm_22050` | 16-bit PCM, 22.05kHz, mono |
| `pcm_24000` | 16-bit PCM, 24kHz, mono |
| `pcm_44100` | 16-bit PCM, 44.1kHz, mono |
| `ulaw_8000` | μ-law, 8kHz, mono (telephony) |

### Model ID Mapping

| ElevenLabs Model | Dalston Backend |
|------------------|-----------------|
| `scribe_v1` | Parakeet 0.6B (native streaming) |
| `scribe_v2` | Parakeet 1.1B (native streaming) |

Note: Parakeet models provide native streaming support (not VAD-chunked like Whisper), enabling true partial results during speech.

### Example Connection

```javascript
const ws = new WebSocket(
  'wss://api.dalston.example.com/v1/speech-to-text/realtime?' +
  'api_key=dk_your_key_here&model_id=scribe_v1&language_code=en&commit_strategy=vad&include_timestamps=true'
);
```

---

## Protocol Messages

### Client → Server

#### Audio Chunk

Send audio data as base64-encoded JSON:

```json
{
  "message_type": "input_audio_chunk",
  "audio_base_64": "UklGRiQAAABXQVZFZm10IBA...",
  "commit": false,
  "sample_rate": 16000
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_type` | string | Yes | Must be `"input_audio_chunk"` |
| `audio_base_64` | string | Yes | Base64-encoded audio data |
| `commit` | boolean | No | Force commit (manual mode), default `false` |
| `sample_rate` | integer | No | Override sample rate if different from connection |

**Chunk size recommendation**: 100-250ms of audio per message.

#### Manual Commit

When using `commit_strategy=manual`, send a commit message to finalize a segment:

```json
{
  "message_type": "input_audio_chunk",
  "audio_base_64": "",
  "commit": true
}
```

Or send an empty audio chunk with `commit: true`.

#### End Session

Close the session gracefully and receive final transcript:

```json
{
  "message_type": "close_connection"
}
```

---

### Server → Client

#### Partial Transcript

Interim results as speech is being recognized. Text may change as more audio arrives.

```json
{
  "message_type": "partial_transcript",
  "text": "hello how are"
}
```

#### Committed Transcript

Final transcript for a completed utterance (after VAD endpoint or manual commit).

**Without timestamps** (`include_timestamps=false`):

```json
{
  "message_type": "committed_transcript",
  "text": "Hello, how are you?"
}
```

**With timestamps** (`include_timestamps=true`):

```json
{
  "message_type": "committed_transcript_with_timestamps",
  "text": "Hello, how are you?",
  "language_code": "en",
  "words": [
    {
      "text": "Hello",
      "start": 0.0,
      "end": 0.4,
      "type": "word",
      "speaker_id": "speaker_1"
    },
    {
      "text": ",",
      "start": 0.4,
      "end": 0.4,
      "type": "spacing"
    },
    {
      "text": "how",
      "start": 0.5,
      "end": 0.7,
      "type": "word",
      "speaker_id": "speaker_1"
    },
    {
      "text": "are",
      "start": 0.8,
      "end": 1.0,
      "type": "word",
      "speaker_id": "speaker_1"
    },
    {
      "text": "you",
      "start": 1.1,
      "end": 1.4,
      "type": "word",
      "speaker_id": "speaker_1"
    }
  ]
}
```

#### Language Detection

When `include_language_detection=true`, language info is included:

```json
{
  "message_type": "language_detection",
  "language_code": "en",
  "language_confidence": 0.98
}
```

#### Error

```json
{
  "message_type": "error",
  "code": "rate_limit",
  "message": "Audio arriving too fast"
}
```

| Code | Description |
|------|-------------|
| `rate_limit` | Audio arriving faster than real-time |
| `invalid_audio` | Audio format doesn't match configuration |
| `invalid_message` | Malformed JSON or unknown message type |
| `language_unsupported` | Requested language not available |
| `no_capacity` | No workers available |
| `session_timeout` | Session exceeded maximum duration |

---

## Client Examples

For complete client implementations in JavaScript and Python, see [WebSocket Client Examples](../examples/websocket-clients.md).

---

# Dalston Native WebSocket

## Endpoint

```
WS /v1/audio/transcriptions/stream
```

This endpoint uses Dalston's native message format with efficient binary audio frames.

---

## Connection

Authentication is required via the `api_key` query parameter. See the [Authentication section](#authentication) above.

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | string | **required** | API key with `realtime` scope |
| `language` | string | `"auto"` | Language code (ISO 639-1) or `"auto"` |
| `model` | string | `"fast"` | Model variant: `"fast"` or `"accurate"` |
| `encoding` | string | `"pcm_s16le"` | Audio encoding |
| `sample_rate` | integer | `16000` | Audio sample rate in Hz |
| `channels` | integer | `1` | Number of audio channels |
| `enable_vad` | boolean | `true` | Enable voice activity detection |
| `interim_results` | boolean | `true` | Return partial transcripts |
| `word_timestamps` | boolean | `false` | Include word-level timing |
| `enhance_on_end` | boolean | `false` | Trigger batch enhancement on session end |

### Supported Encodings

| Encoding | Description |
|----------|-------------|
| `pcm_s16le` | 16-bit signed PCM, little-endian (recommended) |
| `pcm_f32le` | 32-bit float PCM, little-endian |
| `mulaw` | μ-law encoded (8-bit) |
| `alaw` | A-law encoded (8-bit) |

### Example Connection

```javascript
const ws = new WebSocket(
  'ws://localhost:8000/v1/audio/transcriptions/stream?' +
  'api_key=dk_your_key_here&language=en&model=fast&interim_results=true&word_timestamps=true'
);
```

---

## Protocol Messages

### Client → Server

#### Binary: Audio Data

Send raw audio bytes as binary WebSocket frames. More efficient than base64 encoding.

```javascript
// Send raw PCM bytes directly
ws.send(audioBuffer);
```

#### JSON: Configuration Update

```json
{
  "type": "config",
  "language": "es"
}
```

#### JSON: Flush

Force processing of buffered audio:

```json
{
  "type": "flush"
}
```

#### JSON: End Session

```json
{
  "type": "end"
}
```

---

### Server → Client

#### Session Begin

```json
{
  "type": "session.begin",
  "session_id": "sess_abc123def456",
  "config": {
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "channels": 1,
    "language": "en",
    "model": "fast"
  }
}
```

#### Partial Transcript

```json
{
  "type": "transcript.partial",
  "text": "hello how are",
  "start": 0.0,
  "end": 1.2
}
```

#### Final Transcript

```json
{
  "type": "transcript.final",
  "text": "Hello, how are you?",
  "start": 0.0,
  "end": 1.8,
  "confidence": 0.95,
  "words": [
    { "word": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.98 },
    { "word": "how", "start": 0.5, "end": 0.7, "confidence": 0.96 },
    { "word": "are", "start": 0.8, "end": 1.0, "confidence": 0.94 },
    { "word": "you", "start": 1.1, "end": 1.4, "confidence": 0.97 }
  ]
}
```

#### VAD Events

```json
{
  "type": "vad.speech_start",
  "timestamp": 2.5
}
```

```json
{
  "type": "vad.speech_end",
  "timestamp": 4.2
}
```

#### Session End

```json
{
  "type": "session.end",
  "session_id": "sess_abc123def456",
  "total_duration": 45.6,
  "total_speech_duration": 32.1,
  "transcript": "Full transcript of entire session...",
  "segments": [
    { "start": 0.0, "end": 1.8, "text": "Hello, how are you?" },
    { "start": 2.5, "end": 5.2, "text": "I'm doing great, thanks for asking." }
  ]
}
```

#### Error

```json
{
  "type": "error",
  "code": "rate_limit",
  "message": "Audio arriving too fast",
  "recoverable": true
}
```

---

# Management Endpoints

## GET /v1/realtime/status

System capacity for realtime transcription.

```json
{
  "status": "ready",
  "total_capacity": 16,
  "active_sessions": 7,
  "available_capacity": 9,
  "workers": [
    {
      "id": "realtime-whisper-1",
      "status": "ready",
      "capacity": 4,
      "active_sessions": 2
    }
  ]
}
```

## GET /v1/realtime/sessions

List active sessions (admin).

```json
{
  "sessions": [
    {
      "session_id": "sess_abc123",
      "worker_id": "realtime-whisper-1",
      "started_at": "2025-01-28T12:00:00Z",
      "duration": 45.6,
      "language": "en",
      "model": "fast"
    }
  ],
  "total": 7
}
```

## DELETE /v1/realtime/sessions/{session_id}

Force-terminate a session (admin).

---

# Error Handling

## Connection Refused (No Capacity)

```json
{
  "message_type": "error",
  "code": "no_capacity",
  "message": "No realtime workers available. Try again later.",
  "retry_after": 5
}
```

## Graceful Degradation

If preferred model unavailable:

```json
{
  "type": "session.begin",
  "session_id": "sess_abc123",
  "config": {
    "model": "fast"
  },
  "warnings": [
    {
      "code": "model_fallback",
      "message": "Requested model 'accurate' unavailable, using 'fast'"
    }
  ]
}
```

---

# Limits

| Limit | Value | Configurable |
|-------|-------|--------------|
| Maximum session duration | 4 hours | Yes |
| Maximum audio rate | 1.5x real-time | Yes |
| Minimum chunk size | 50ms | No |
| Maximum chunk size | 1000ms | Yes |
| Idle timeout | 30 seconds | Yes |

---

# Protocol Comparison

| Aspect | ElevenLabs (`/v1/speech-to-text/realtime`) | Dalston (`/v1/audio/transcriptions/stream`) |
|--------|-------------------------------------------|---------------------------------------------|
| Audio input | JSON with `audio_base_64` | Binary frames (raw bytes) |
| Message type field | `message_type` | `type` |
| Partial message | `partial_transcript` | `transcript.partial` |
| Final message | `committed_transcript_with_timestamps` | `transcript.final` |
| Commit control | `commit: true` in audio message | `{ "type": "flush" }` |
| Session begin | *(not sent)* | `session.begin` |
| VAD events | *(not sent)* | `vad.speech_start`, `vad.speech_end` |
| Session end | *(connection closes)* | `session.end` with summary |
| Model param | `model_id` = `scribe_v1`/`scribe_v2` | `model` = `fast`/`accurate` |
| Language param | `language_code` | `language` |
| VAD param | `commit_strategy` = `vad`/`manual` | `enable_vad` = `true`/`false` |
| Timestamps param | `include_timestamps` | `word_timestamps` |
| Speaker ID format | `speaker_1`, `speaker_2` | `SPEAKER_00`, `SPEAKER_01` |

---

# Migration Guide

## From ElevenLabs to Dalston

If using the ElevenLabs endpoint (`/v1/speech-to-text/realtime`), no changes needed — it's fully compatible.

If migrating to Dalston native for efficiency:

| Change | Before (ElevenLabs) | After (Dalston) |
|--------|---------------------|-----------------|
| Endpoint | `/v1/speech-to-text/realtime` | `/v1/audio/transcriptions/stream` |
| Audio | Base64 JSON | Raw binary frames |
| Params | `model_id`, `language_code`, `commit_strategy` | `model`, `language`, `enable_vad` |
| Message field | `message_type` | `type` |
| Partials | `partial_transcript` | `transcript.partial` |
| Finals | `committed_transcript_with_timestamps` | `transcript.final` |

**Benefits of Dalston native:**

- ~33% bandwidth reduction (no base64 overhead)
- Session metadata (`session.begin`, `session.end`)
- VAD events for UI feedback
- Enhanced transcript option on session end

---

# Error Recovery & Reconnection

## Unclean Session Termination

When a session ends unexpectedly (worker crash, network issue, resource exhaustion), the server sends a `session.terminated` message before closing:

```json
{
  "type": "session.terminated",
  "session_id": "sess_abc123",
  "reason": "worker_crash",
  "last_transcript_offset_ms": 45600,
  "recoverable": true,
  "recovery_hint": {
    "action": "reconnect_with_replay",
    "buffer_window_ms": 10000,
    "retry_after_ms": 500
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `reason` | string | `worker_crash`, `worker_maintenance`, `resource_exhaustion`, `session_timeout` |
| `last_transcript_offset_ms` | integer | Last confirmed transcript position in milliseconds |
| `recoverable` | boolean | Whether recovery is possible |
| `recovery_hint.action` | string | `reconnect_with_replay` or `start_fresh` |
| `recovery_hint.buffer_window_ms` | integer | How much audio the client should replay |
| `recovery_hint.retry_after_ms` | integer | Suggested wait before reconnecting |

## Reconnection Protocol

When `recoverable=true`, clients can reconnect with session recovery:

```
ws://host/v1/audio/transcriptions/stream?api_key=dk_xxx&recovery_session=old_session_id&language=en
```

### Recovery Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `recovery_session` | string | Previous session ID to recover |

### Recovery Flow

1. **Client receives `session.terminated`** (or detects WebSocket close)
2. **If `recoverable=true`:**
   - Wait `retry_after_ms` milliseconds
   - Reconnect with `recovery_session={old_session_id}` query param
   - If client buffered audio since `last_transcript_offset_ms`, send as first chunks
   - Gateway allocates new worker, returns `session.recovered`
3. **If `recoverable=false`:**
   - Start fresh session
   - Audio since last `transcript.final` is lost

### Recovery Response

On successful recovery:

```json
{
  "type": "session.recovered",
  "session_id": "sess_def456",
  "previous_session_id": "sess_abc123",
  "recovered_offset_ms": 45600,
  "message": "Session recovered. Resume sending audio."
}
```

On failed recovery (session expired or unavailable):

```json
{
  "type": "session.begin",
  "session_id": "sess_def456",
  "warnings": [
    {
      "code": "recovery_failed",
      "message": "Previous session not found. Starting fresh session."
    }
  ]
}
```

## Client Implementation Guidance

Clients SHOULD:

- Buffer the last 5-10 seconds of audio locally
- Track the `offset_ms` from each `transcript.final`
- Implement exponential backoff for reconnection attempts (500ms, 1s, 2s, 4s)
- Set a maximum reconnection attempts limit (recommended: 3)
- Clear buffer after successful `transcript.final` acknowledgment

For a complete reconnection-capable client implementation, see [WebSocket Client Examples](../examples/websocket-clients.md#robust-client-with-reconnection-javascript).

---

# Rate Limits

## Connection Limits

| Limit | Default | Configurable | Description |
|-------|---------|--------------|-------------|
| Max concurrent sessions per API key | 3 | Yes | Simultaneous active sessions |
| Max concurrent sessions per tenant | 10 | Yes | Total sessions across all keys |
| Connection attempts per minute | 10 | Yes | Rate of new connection attempts |
| Session creation rate | 5/minute | Yes | Successful session creations |

## In-Session Limits

| Limit | Default | Description |
|-------|---------|-------------|
| Audio data rate (sustained) | 1.5x real-time | Average rate over 10-second window |
| Audio data rate (burst) | 10 seconds | Burst allowance for catch-up/replay |
| Message rate (non-audio) | 30/second | Control messages (config, flush, etc.) |
| Maximum audio chunk size | 1MB | Single message size limit |

### Audio Rate Limiting (Token Bucket)

Audio rate limiting uses a token bucket algorithm that allows bursts while maintaining a sustainable average rate:

```
Bucket capacity: 10 seconds of audio
Refill rate: 1.5x real-time
```

This allows clients to:

- Send up to 10 seconds of buffered audio immediately (reconnection scenario)
- Sustain 1.5x real-time transmission (faster-than-realtime uploads)
- Recover from temporary delays without data loss

When the bucket is empty, excess audio is queued briefly, then dropped with a warning.

## Rate Limit Errors

When a rate limit is exceeded:

```json
{
  "type": "error",
  "code": "rate_limit",
  "message": "Concurrent session limit reached",
  "limit_type": "concurrent_sessions",
  "current": 3,
  "limit": 3,
  "retry_after": null
}
```

| `limit_type` | Meaning | Recovery |
|--------------|---------|----------|
| `concurrent_sessions` | Too many active sessions | Wait for one to end |
| `connection_rate` | Too many connect attempts | Wait `retry_after` seconds |
| `audio_rate` | Sending audio too fast | Slow down; data being dropped |
| `message_rate` | Too many control messages | Reduce message frequency |

### Rate Limit Response Behavior

| Limit Type | Connection Phase | Behavior |
|------------|------------------|----------|
| `concurrent_sessions` | Before accept | Connection rejected with 4029 |
| `connection_rate` | Before accept | Connection rejected with 4029 |
| `audio_rate` | During session | Warning message, audio queued/dropped |
| `message_rate` | During session | Warning, then disconnect |

## WebSocket Close Codes

| Code | Meaning |
|------|---------|
| `4001` | Invalid API key |
| `4003` | Missing required scope |
| `4029` | Rate limit exceeded |
| `4008` | Session timeout |
| `4500` | Internal server error |
| `4503` | Service unavailable (no workers) |
