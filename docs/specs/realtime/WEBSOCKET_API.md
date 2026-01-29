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

## Connection

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
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
| `scribe_v1` | WhisperX base (~16x realtime) |
| `scribe_v2` | WhisperX large-v3 (~1x realtime) |

### Example Connection

```javascript
const ws = new WebSocket(
  'wss://api.dalston.example.com/v1/speech-to-text/realtime?' +
  'model_id=scribe_v1&language_code=en&commit_strategy=vad&include_timestamps=true'
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
  "language_probability": 0.98
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

## Complete Example

### JavaScript Client

```javascript
class ElevenLabsCompatibleTranscriber {
  constructor(options = {}) {
    this.options = {
      url: 'wss://api.dalston.example.com/v1/speech-to-text/realtime',
      modelId: 'scribe_v1',
      languageCode: 'en',
      commitStrategy: 'vad',
      includeTimestamps: true,
      ...options
    };
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        model_id: this.options.modelId,
        language_code: this.options.languageCode,
        commit_strategy: this.options.commitStrategy,
        include_timestamps: this.options.includeTimestamps,
      });

      this.ws = new WebSocket(`${this.options.url}?${params}`);

      this.ws.onopen = () => resolve();
      this.ws.onerror = (error) => reject(error);
      
      this.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      };

      this.ws.onclose = (event) => {
        this.onClose?.(event);
      };
    });
  }

  handleMessage(message) {
    switch (message.message_type) {
      case 'partial_transcript':
        this.onPartialTranscript?.(message.text);
        break;
      case 'committed_transcript':
      case 'committed_transcript_with_timestamps':
        this.onFinalTranscript?.(message);
        break;
      case 'language_detection':
        this.onLanguageDetection?.(message);
        break;
      case 'error':
        this.onError?.(message);
        break;
    }
  }

  sendAudio(audioData) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      // Convert ArrayBuffer to base64
      const base64 = btoa(String.fromCharCode(...new Uint8Array(audioData)));
      
      this.ws.send(JSON.stringify({
        message_type: 'input_audio_chunk',
        audio_base_64: base64,
        commit: false
      }));
    }
  }

  commit() {
    this.ws?.send(JSON.stringify({
      message_type: 'input_audio_chunk',
      audio_base_64: '',
      commit: true
    }));
  }

  close() {
    this.ws?.send(JSON.stringify({
      message_type: 'close_connection'
    }));
  }
}

// Usage
const transcriber = new ElevenLabsCompatibleTranscriber({
  modelId: 'scribe_v2',
  languageCode: 'en'
});

transcriber.onPartialTranscript = (text) => {
  console.log('Partial:', text);
};

transcriber.onFinalTranscript = (result) => {
  console.log('Final:', result.text);
  if (result.words) {
    console.log('Words:', result.words);
  }
};

await transcriber.connect();

// From microphone (using Web Audio API)
const audioContext = new AudioContext({ sampleRate: 16000 });
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const source = audioContext.createMediaStreamSource(stream);
const processor = audioContext.createScriptProcessor(4096, 1, 1);

processor.onaudioprocess = (event) => {
  const float32Data = event.inputBuffer.getChannelData(0);
  // Convert to 16-bit PCM
  const int16Data = new Int16Array(float32Data.length);
  for (let i = 0; i < float32Data.length; i++) {
    int16Data[i] = Math.max(-32768, Math.min(32767, float32Data[i] * 32768));
  }
  transcriber.sendAudio(int16Data.buffer);
};

source.connect(processor);
processor.connect(audioContext.destination);
```

### Python Client

```python
import asyncio
import websockets
import json
import base64

async def transcribe_realtime(audio_source):
    uri = (
        "wss://api.dalston.example.com/v1/speech-to-text/realtime?"
        "model_id=scribe_v1&language_code=en&commit_strategy=vad&include_timestamps=true"
    )
    
    async with websockets.connect(uri) as ws:
        async def receive():
            async for msg in ws:
                data = json.loads(msg)
                msg_type = data.get('message_type')
                
                if msg_type == 'partial_transcript':
                    print(f"Partial: {data['text']}", end='\r')
                elif msg_type in ('committed_transcript', 'committed_transcript_with_timestamps'):
                    print(f"\nFinal: {data['text']}")
                    if 'words' in data:
                        for word in data['words']:
                            print(f"  [{word['start']:.2f}-{word['end']:.2f}] {word['text']}")
                elif msg_type == 'error':
                    print(f"Error: {data['message']}")
                    break
        
        async def send():
            async for chunk in audio_source:
                audio_b64 = base64.b64encode(chunk).decode('utf-8')
                await ws.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": audio_b64,
                    "commit": False
                }))
            
            # End session
            await ws.send(json.dumps({
                "message_type": "close_connection"
            }))
        
        await asyncio.gather(receive(), send())

# Simulated audio source
async def audio_file_source(path):
    with open(path, 'rb') as f:
        while chunk := f.read(3200):  # 100ms at 16kHz, 16-bit
            yield chunk
            await asyncio.sleep(0.1)

asyncio.run(transcribe_realtime(audio_file_source('audio.pcm')))
```

---

# Dalston Native WebSocket

## Endpoint

```
WS /v1/audio/transcriptions/stream
```

This endpoint uses Dalston's native message format with efficient binary audio frames.

---

## Connection

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
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
  'language=en&model=fast&interim_results=true&word_timestamps=true'
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

## Complete Example

### JavaScript Client (Binary Mode)

```javascript
class DalstonTranscriber {
  constructor(options = {}) {
    this.options = {
      url: 'ws://localhost:8000/v1/audio/transcriptions/stream',
      language: 'en',
      model: 'fast',
      interimResults: true,
      wordTimestamps: true,
      ...options
    };
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        language: this.options.language,
        model: this.options.model,
        interim_results: this.options.interimResults,
        word_timestamps: this.options.wordTimestamps,
      });

      this.ws = new WebSocket(`${this.options.url}?${params}`);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
        
        if (message.type === 'session.begin') {
          resolve(message);
        }
      };

      this.ws.onerror = reject;
    });
  }

  handleMessage(message) {
    switch (message.type) {
      case 'session.begin':
        this.onSessionBegin?.(message);
        break;
      case 'transcript.partial':
        this.onPartialTranscript?.(message);
        break;
      case 'transcript.final':
        this.onFinalTranscript?.(message);
        break;
      case 'vad.speech_start':
        this.onSpeechStart?.(message);
        break;
      case 'vad.speech_end':
        this.onSpeechEnd?.(message);
        break;
      case 'session.end':
        this.onSessionEnd?.(message);
        break;
      case 'error':
        this.onError?.(message);
        break;
    }
  }

  // Send raw binary audio - more efficient than base64
  sendAudio(audioData) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(audioData);
    }
  }

  flush() {
    this.ws?.send(JSON.stringify({ type: 'flush' }));
  }

  end() {
    this.ws?.send(JSON.stringify({ type: 'end' }));
  }
}

// Usage
const transcriber = new DalstonTranscriber({
  language: 'en',
  model: 'accurate'
});

transcriber.onPartialTranscript = (msg) => console.log('Partial:', msg.text);
transcriber.onFinalTranscript = (msg) => console.log('Final:', msg.text);

await transcriber.connect();

// Send raw PCM bytes directly (no base64 overhead)
transcriber.sendAudio(pcmBuffer);
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
