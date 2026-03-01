# OpenAI-Compatible API Reference

## Overview

Dalston provides OpenAI-compatible endpoints for audio transcription, allowing existing OpenAI SDK users to switch to Dalston by simply changing the base URL.

**Compatibility Target**: OpenAI Audio API (March 2025)

---

## Authentication

OpenAI uses Bearer token authentication:

```bash
curl -X POST https://api.dalston.example.com/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_your_api_key_here" \
  -F "file=@audio.mp3" \
  -F "model=whisper-1"
```

Dalston accepts both:

- `Authorization: Bearer dk_xxx` (native Dalston keys)
- `Authorization: Bearer sk-xxx` (OpenAI-style prefix, treated as Dalston key)

---

## Batch Transcription

### POST /v1/audio/transcriptions

Transcribes audio into the input language.

**Content-Type**: `multipart/form-data`

### Request Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | file | Yes | - | Audio file (see supported formats below) |
| `model` | string | Yes | - | Model ID (see table below) |
| `language` | string | No | auto | ISO-639-1 language code |
| `prompt` | string | No | - | Vocabulary hints (max 224 tokens) |
| `response_format` | string | No | `json` | Output format |
| `temperature` | float | No | `0` | Randomness 0.0-1.0 |
| `timestamp_granularities[]` | array | No | - | `word`, `segment`, or both |

### Supported Audio Formats

| Format | Extensions | Max Size |
|--------|------------|----------|
| FLAC | .flac | 25 MB |
| MP3 | .mp3 | 25 MB |
| MP4 | .mp4 | 25 MB |
| MPEG | .mpeg, .mpga | 25 MB |
| M4A | .m4a | 25 MB |
| OGG | .ogg | 25 MB |
| WAV | .wav | 25 MB |
| WebM | .webm | 25 MB |

**Note**: OpenAI compatibility enforces 25 MB limit. Use Dalston native API for larger files (up to 3 GB).

### Available Models

| Model | Description | Dalston Backend |
|-------|-------------|-----------------|
| `whisper-1` | OpenAI's Whisper V2 | whisper-large-v2 |
| `gpt-4o-transcribe` | Most accurate | whisper-large-v3 |
| `gpt-4o-mini-transcribe` | Fast, efficient | distil-whisper |
| `gpt-4o-transcribe-diarize` | With speaker labels | whisper-large-v3 + pyannote |

### Response Formats

#### `json` (default)

```json
{
  "text": "Hello, how are you today?"
}
```

#### `text`

Plain text output:

```
Hello, how are you today?
```

#### `verbose_json`

Detailed output with timing information:

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
  ]
}
```

When `timestamp_granularities[]=word` is specified:

```json
{
  "task": "transcribe",
  "language": "english",
  "duration": 2.5,
  "text": "Hello, how are you today?",
  "words": [
    { "word": "Hello", "start": 0.0, "end": 0.4 },
    { "word": ",", "start": 0.4, "end": 0.4 },
    { "word": "how", "start": 0.5, "end": 0.7 },
    { "word": "are", "start": 0.8, "end": 1.0 },
    { "word": "you", "start": 1.1, "end": 1.3 },
    { "word": "today", "start": 1.4, "end": 1.8 },
    { "word": "?", "start": 1.8, "end": 1.8 }
  ],
  "segments": [...]
}
```

#### `srt`

SubRip subtitle format:

```
1
00:00:00,000 --> 00:00:02,500
Hello, how are you today?

2
00:00:03,000 --> 00:00:05,500
I'm doing great, thanks for asking.
```

#### `vtt`

WebVTT subtitle format:

```
WEBVTT

00:00:00.000 --> 00:00:02.500
Hello, how are you today?

00:00:03.000 --> 00:00:05.500
I'm doing great, thanks for asking.
```

#### `diarized_json` (for gpt-4o-transcribe-diarize only)

Speaker-aware transcription:

```json
{
  "text": "Hello, how are you today? I'm doing great, thanks.",
  "segments": [
    {
      "speaker": "speaker_0",
      "start": 0.0,
      "end": 2.5,
      "text": "Hello, how are you today?"
    },
    {
      "speaker": "speaker_1",
      "start": 2.8,
      "end": 4.5,
      "text": "I'm doing great, thanks."
    }
  ]
}
```

### Examples

```bash
# Basic transcription
curl -X POST https://api.dalston.example.com/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@audio.mp3" \
  -F "model=whisper-1"

# With word timestamps
curl -X POST https://api.dalston.example.com/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@audio.mp3" \
  -F "model=gpt-4o-transcribe" \
  -F "response_format=verbose_json" \
  -F "timestamp_granularities[]=word"

# With language and prompt
curl -X POST https://api.dalston.example.com/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@meeting.wav" \
  -F "model=gpt-4o-transcribe" \
  -F "language=en" \
  -F "prompt=Kubernetes, FastAPI, PostgreSQL"

# With diarization
curl -X POST https://api.dalston.example.com/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@interview.mp3" \
  -F "model=gpt-4o-transcribe-diarize" \
  -F "response_format=diarized_json"
```

---

## Translation (Optional)

### POST /v1/audio/translations

Translates audio into English.

**Request**: Same as transcription, but always outputs English.

```bash
curl -X POST https://api.dalston.example.com/v1/audio/translations \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@french_audio.mp3" \
  -F "model=whisper-1"
```

**Response**:

```json
{
  "text": "Hello, how are you today?"
}
```

---

## Real-time Transcription

### WebSocket /v1/realtime

Real-time streaming transcription using OpenAI's Realtime API protocol.

**Connection URL**:

```
wss://api.dalston.example.com/v1/realtime?intent=transcription
```

**Headers**:

```
Authorization: Bearer dk_your_api_key
OpenAI-Beta: realtime=v1
```

### Session Configuration

After connecting, configure the transcription session:

```json
{
  "type": "transcription_session.update",
  "session": {
    "input_audio_format": "pcm16",
    "input_audio_transcription": {
      "model": "gpt-4o-transcribe",
      "language": "en",
      "prompt": "Technical terms: Kubernetes, FastAPI"
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

### Audio Formats

| Format | Description |
|--------|-------------|
| `pcm16` | 16-bit PCM, 24kHz, mono |
| `g711_ulaw` | G.711 μ-law |
| `g711_alaw` | G.711 A-law |

### Turn Detection Options

| Type | Description |
|------|-------------|
| `server_vad` | Server-side voice activity detection |
| `semantic_vad` | Semantic-aware turn detection |
| `null` | Manual commit only |

### Client → Server Events

#### Append Audio

```json
{
  "type": "input_audio_buffer.append",
  "audio": "<base64-encoded-audio>"
}
```

#### Commit Buffer

Force processing of buffered audio:

```json
{
  "type": "input_audio_buffer.commit"
}
```

#### Clear Buffer

Discard buffered audio:

```json
{
  "type": "input_audio_buffer.clear"
}
```

### Server → Client Events

#### Session Created

```json
{
  "type": "transcription_session.created",
  "event_id": "evt_abc123",
  "session": {
    "id": "sess_xyz789",
    "input_audio_format": "pcm16",
    "input_audio_transcription": {
      "model": "gpt-4o-transcribe",
      "language": "en"
    }
  }
}
```

#### Session Updated

```json
{
  "type": "transcription_session.updated",
  "event_id": "evt_abc124",
  "session": {...}
}
```

#### Speech Started

VAD detected speech:

```json
{
  "type": "input_audio_buffer.speech_started",
  "event_id": "evt_abc125",
  "audio_start_ms": 1500
}
```

#### Speech Stopped

VAD detected silence:

```json
{
  "type": "input_audio_buffer.speech_stopped",
  "event_id": "evt_abc126",
  "audio_end_ms": 3200
}
```

#### Buffer Committed

Acknowledgment of commit:

```json
{
  "type": "input_audio_buffer.committed",
  "event_id": "evt_abc127",
  "item_id": "item_xyz"
}
```

#### Transcription Delta

Incremental transcript (streaming):

```json
{
  "type": "conversation.item.input_audio_transcription.delta",
  "event_id": "evt_abc128",
  "item_id": "item_xyz",
  "content_index": 0,
  "delta": "Hello, how"
}
```

#### Transcription Completed

Final transcript for an utterance:

```json
{
  "type": "conversation.item.input_audio_transcription.completed",
  "event_id": "evt_abc129",
  "item_id": "item_xyz",
  "content_index": 0,
  "transcript": "Hello, how are you today?"
}
```

With logprobs (if requested):

```json
{
  "type": "conversation.item.input_audio_transcription.completed",
  "event_id": "evt_abc129",
  "item_id": "item_xyz",
  "content_index": 0,
  "transcript": "Hello, how are you today?",
  "logprobs": [
    { "token": "Hello", "logprob": -0.12 },
    { "token": ",", "logprob": -0.05 },
    { "token": "how", "logprob": -0.08 }
  ]
}
```

#### Error

```json
{
  "type": "error",
  "event_id": "evt_abc130",
  "error": {
    "type": "invalid_request_error",
    "code": "invalid_audio_format",
    "message": "Audio format does not match session configuration"
  }
}
```

### Real-time Models

| Model | Dalston Backend | Description |
|-------|-----------------|-------------|
| `gpt-4o-transcribe` | parakeet-1.1b | Best quality streaming |
| `gpt-4o-mini-transcribe` | parakeet-0.6b | Fast streaming |
| `whisper-1` | whisper-streaming | VAD-chunked Whisper |

### Example Client (Python)

```python
import websocket
import json
import base64

def on_message(ws, message):
    event = json.loads(message)

    if event["type"] == "transcription_session.created":
        print(f"Session started: {event['session']['id']}")

    elif event["type"] == "conversation.item.input_audio_transcription.delta":
        print(f"Partial: {event['delta']}", end="", flush=True)

    elif event["type"] == "conversation.item.input_audio_transcription.completed":
        print(f"\nFinal: {event['transcript']}")

def on_open(ws):
    # Configure session
    ws.send(json.dumps({
        "type": "transcription_session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "gpt-4o-transcribe",
                "language": "en"
            },
            "turn_detection": {
                "type": "server_vad"
            }
        }
    }))

    # Send audio (example: read from file)
    with open("audio.raw", "rb") as f:
        while chunk := f.read(4800):  # 100ms at 24kHz
            ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode()
            }))

    # Commit final buffer
    ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

ws = websocket.WebSocketApp(
    "wss://api.dalston.example.com/v1/realtime?intent=transcription",
    header=[
        "Authorization: Bearer dk_your_key",
        "OpenAI-Beta: realtime=v1"
    ],
    on_open=on_open,
    on_message=on_message
)

ws.run_forever()
```

---

## Error Responses

All errors follow OpenAI's error format:

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

### Error Types

| Type | HTTP Status | Description |
|------|-------------|-------------|
| `invalid_request_error` | 400 | Invalid parameters or request |
| `authentication_error` | 401 | Invalid or missing API key |
| `rate_limit_error` | 429 | Rate limit exceeded |
| `server_error` | 500 | Internal processing error |

### Error Codes

| Code | Description |
|------|-------------|
| `invalid_file_format` | Unsupported audio format |
| `file_too_large` | File exceeds 25 MB limit |
| `invalid_model` | Model not found |
| `invalid_language` | Unsupported language code |
| `invalid_response_format` | Invalid response_format value |
| `invalid_audio_format` | Real-time audio format mismatch |
| `processing_failed` | Transcription failed |
| `rate_limit_exceeded` | Too many requests |

---

## Rate Limits

| Limit | Default | Description |
|-------|---------|-------------|
| Requests per minute | 60 | API calls per minute |
| Concurrent requests | 10 | Simultaneous batch jobs |
| Concurrent sessions | 5 | Simultaneous real-time sessions |
| File size | 25 MB | Per-request file size |

### Rate Limit Headers

```
X-RateLimit-Limit-Requests: 60
X-RateLimit-Remaining-Requests: 45
X-RateLimit-Reset-Requests: 32
```

---

## Migration Guide

### From OpenAI to Dalston

```python
from openai import OpenAI

# Before (OpenAI)
client = OpenAI(api_key="sk-xxx")

# After (Dalston)
client = OpenAI(
    api_key="dk_your_dalston_key",
    base_url="https://api.dalston.example.com/v1"
)

# Code remains the same
transcript = client.audio.transcriptions.create(
    model="whisper-1",
    file=open("audio.mp3", "rb"),
    response_format="verbose_json",
    timestamp_granularities=["word"]
)
```

### Feature Comparison

| Feature | OpenAI | Dalston |
|---------|--------|---------|
| Max file size | 25 MB | 25 MB (compat) / 3 GB (native) |
| whisper-1 | Yes | Yes |
| gpt-4o-transcribe | Yes | Yes |
| gpt-4o-mini-transcribe | Yes | Yes |
| gpt-4o-transcribe-diarize | Yes | Yes |
| Real-time API | Yes | Yes |
| Translation | Yes | Yes (optional) |
| SRT/VTT export | Yes | Yes |
| Word timestamps | Yes | Yes |
| Self-hosted | No | Yes |

---

## OpenAI vs Dalston API Comparison

For users who want Dalston's extended features:

| Feature | OpenAI Compat | Dalston Native |
|---------|---------------|----------------|
| Endpoint | `/v1/audio/transcriptions` | `/v1/audio/transcriptions` |
| File size | 25 MB | 3 GB |
| Response | Synchronous | Async (job-based) |
| Speaker count hints | No | Yes (`min_speakers`, `max_speakers`) |
| Enrichment | No | Yes (emotions, events, LLM cleanup) |
| PII detection | No | Yes |
| Webhooks | No | Yes |
| Session persistence | No | Yes (hybrid mode) |

Use the Dalston native API for extended features. See [Dalston API Reference](../batch/API.md).
