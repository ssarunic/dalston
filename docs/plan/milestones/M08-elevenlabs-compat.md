# M8: ElevenLabs Compatibility Layer

| | |
|---|---|
| **Goal** | Drop-in replacement for ElevenLabs Speech-to-Text API |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete (real-time working) |
| **Deliverable** | ElevenLabs clients work unchanged |

## User Story

> *"As a developer using ElevenLabs, I can switch to Dalston by just changing the base URL."*

---

## ElevenLabs API Reference

We implement compatibility with:

- `POST /v1/speech-to-text` — Batch transcription
- `GET /v1/speech-to-text/transcripts/{id}` — Get result
- `WS /v1/speech-to-text/realtime` — Streaming

---

## Steps

### 8.1: Batch Endpoint

**Endpoint:** `POST /v1/speech-to-text`

**ElevenLabs parameters mapped to Dalston:**

| ElevenLabs Param | Dalston Param | Notes |
| --- | --- | --- |
| `model_id` | `model` | scribe_v1 → fast, scribe_v2 → accurate |
| `language_code` | `language` | Direct mapping |
| `diarize` | `speaker_detection` | true → "diarize" |
| `num_speakers` | `num_speakers` | Direct mapping |
| `timestamps_granularity` | `word_timestamps` | "word" → true |
| `tag_audio_events` | `detect_events` | Direct mapping |

**Deliverables:**

- Accept both `file` upload and `cloud_storage_url`
- Map all parameters to Dalston equivalents
- Sync mode (wait for result) and async mode (return job ID)
- Format response in ElevenLabs structure

---

### 8.2: Get Transcript Endpoint

**Endpoint:** `GET /v1/speech-to-text/transcripts/{transcription_id}`

**Response format (ElevenLabs):**

```json
{
  "transcription_id": "job_abc123",
  "status": "completed",
  "language_code": "en",
  "audio_duration": 45.2,
  "text": "Hello world...",
  "words": [
    {"text": "Hello", "start": 0.0, "end": 0.5, "type": "word", "speaker_id": "speaker_0"}
  ]
}
```

---

### 8.3: WebSocket Endpoint

**Endpoint:** `WS /v1/speech-to-text/realtime`

**Query parameters:**

| Parameter | Default | Description |
| --- | --- | --- |
| `model_id` | scribe_v1 | Model selection |
| `language_code` | auto | Language |
| `commit_strategy` | vad | "vad" or "manual" |
| `include_timestamps` | false | Word timestamps |

---

### 8.4: Protocol Translation

**Client → Server (ElevenLabs format):**

| Message Type | Description |
| --- | --- |
| `input_audio_chunk` | Base64 audio + optional commit flag |
| `close_connection` | End session |

**Server → Client (ElevenLabs format):**

| Message Type | Description |
| --- | --- |
| `partial_transcript` | Interim result |
| `committed_transcript` | Final result (no timestamps) |
| `committed_transcript_with_timestamps` | Final result with words |
| `begin_utterance` | VAD speech start |
| `end_utterance` | VAD speech end |
| `error` | Error with code and message |

**Deliverables:**

- Decode base64 audio from ElevenLabs format
- Translate Dalston messages to ElevenLabs format
- Map error codes appropriately

---

## Verification

### Test with ElevenLabs SDK

```python
from elevenlabs import ElevenLabs

# Point to Dalston instead of ElevenLabs
client = ElevenLabs(
    api_key="not-needed-for-dalston",
    base_url="http://localhost:8000"
)

# Batch transcription
result = client.speech_to_text.convert(
    file=open("audio.mp3", "rb"),
    model_id="scribe_v1",
    diarize=True
)

print(result.text)
```

### Test WebSocket

```python
async with client.speech_to_text.realtime(
    model_id="scribe_v1",
    language_code="en"
) as session:
    for chunk in audio_chunks:
        await session.send(chunk)

    async for message in session:
        print(message)
```

---

## Checkpoint

- [ ] **POST /v1/speech-to-text** matches ElevenLabs API
- [ ] **GET /v1/speech-to-text/transcripts/{id}** returns ElevenLabs format
- [ ] **WS /v1/speech-to-text/realtime** uses ElevenLabs protocol
- [ ] **Protocol translation** is bidirectional
- [ ] **ElevenLabs SDK** works unchanged

**Next**: [M9: Enrichment](M09-enrichment.md) — Emotions, events, LLM cleanup
