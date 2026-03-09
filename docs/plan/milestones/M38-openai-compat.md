# M38: OpenAI Compatibility Layer

| | |
|---|---|
| **Goal** | Drop-in replacement for OpenAI Audio Transcription API |
| **Duration** | 3-4 days |
| **Dependencies** | M6 complete (real-time working), M8 complete (ElevenLabs pattern established) |
| **Deliverable** | OpenAI clients work unchanged by pointing to Dalston |
| **Status** | Complete (Batch + Real-time implemented) |

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

Five response formats are supported: `json` (default, returns `{"text": "..."}`) , `text` (plain text), `verbose_json` (includes segments, words, timing, and metadata), `srt`, and `vtt` (subtitle formats). See `dalston/gateway/api/v1/compat/openai_translator.py` for the full transformation logic.

**Deliverables:**

- Detect OpenAI-style requests by parameter inspection
- Map all OpenAI parameters to Dalston equivalents
- Transform Dalston response to OpenAI format based on `response_format`
- Enforce 25MB file size limit for OpenAI compat mode
- Return OpenAI-style errors

---

### 38.2: OpenAI Error Responses ✅

OpenAI uses a specific error envelope with `message`, `type`, `param`, and `code` fields. Implementation in `dalston/gateway/api/v1/compat/openai_errors.py`.

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

### 38.3: Real-time WebSocket Endpoint ✅

**Endpoint:** `WS /v1/realtime?intent=transcription`

Accepts `intent=transcription` query param and optional `model` (default `gpt-4o-transcribe`). Supports both `Authorization: Bearer` header and query param auth, plus `OpenAI-Beta: realtime=v1` header. Model mapping follows the same pattern as batch (see Model Compatibility Matrix below).

---

### 38.4: Real-time Protocol Translation ✅

The protocol translator maps between OpenAI's event types and Dalston's native WebSocket events. Translates audio format names (e.g. `pcm16` → `pcm_s16le`), session config fields, and VAD settings. Client events (`input_audio_buffer.append/commit/clear`, `transcription_session.update`) map to Dalston audio/session primitives. Server events map OpenAI's `conversation.item.input_audio_transcription.*` to Dalston's `transcript.partial/final` events, with generated sequential event IDs (`evt_xxx`, `item_xxx`).

Full field and event mapping is implemented in `dalston/gateway/api/v1/openai_realtime.py`.

---

### 38.5: Translation Endpoint ✅

**Endpoint:** `POST /v1/audio/translations`

Transcribes audio into English regardless of source language. Accepts same parameters as transcription but forces `language=en` and enables translation mode in Whisper engine.

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

Implementation lives in `dalston/gateway/api/v1/`: `openai_audio.py` (batch routes), `openai_realtime.py` (WebSocket), and the `compat/` subpackage (`openai_types.py`, `openai_translator.py`, `openai_errors.py`).

---

## Verification

- [ ] OpenAI Python SDK works unchanged with `base_url="http://localhost:8000/v1"`
- [ ] All five `response_format` values produce correct output
- [ ] `timestamp_granularities` populates word/segment timestamps in `verbose_json`
- [ ] Model mapping works for `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`
- [ ] Error responses match OpenAI format
- [ ] WebSocket at `/v1/realtime?intent=transcription` accepts and translates events bidirectionally

---

## Checkpoint

- [x] **POST /v1/audio/transcriptions** detects and handles OpenAI-style requests
- [x] **response_format** outputs correct format (json, text, srt, verbose_json, vtt)
- [x] **timestamp_granularities** populates word/segment timestamps
- [x] **Model mapping** works for whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe
- [x] **Error responses** match OpenAI format
- [x] **WS /v1/realtime** accepts transcription sessions
- [x] **Real-time protocol** translates OpenAI events bidirectionally
- [x] **OpenAI Python SDK** works unchanged
- [x] **POST /v1/audio/translations** endpoint for audio-to-English translation

**Next**: [M39: Translation Endpoint](M39-translation.md) (optional) or other priorities

---

## Design Decisions

### Coexistence with Dalston Native API

Since both Dalston native and OpenAI-compatible APIs use `/v1/audio/transcriptions`, request disambiguation uses parameter inspection: OpenAI-specific parameters (`response_format` with OpenAI values, OpenAI model IDs, `timestamp_granularities[]`) trigger OpenAI mode. OpenAI mode returns synchronous responses; Dalston mode returns async job IDs.

### WebSocket Endpoint Separation

OpenAI uses `/v1/realtime` while Dalston native uses `/v1/audio/transcriptions/stream` and ElevenLabs uses `/v1/speech-to-text/realtime`. This natural separation avoids conflicts.

### Authentication Compatibility

Dalston accepts `Authorization: Bearer dk_xxx` (Dalston keys), `Authorization: Bearer sk-xxx` (treated as Dalston key, `sk-` prefix ignored), and query param `?api_key=dk_xxx` for WebSocket compatibility.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| OpenAI API evolves | Pin to documented v1 API, add version detection |
| File size mismatch (OpenAI 25MB vs Dalston 3GB) | Enforce 25MB in OpenAI compat mode |
| Token auth differences | Accept both header formats |
| Missing features (GPT-4o-audio) | Document scope as transcription-only |
| Real-time event ordering | Generate sequential event IDs |
