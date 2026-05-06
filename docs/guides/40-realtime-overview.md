# Real-time transcription — three protocols, one backend

> Stream audio in over a WebSocket, get transcripts back as you speak.
> Dalston speaks **three** WebSocket protocols, all hitting the same engine
> pool: its native binary protocol, the ElevenLabs JSON protocol, and the
> OpenAI Realtime protocol. Pick whichever your existing client speaks.

---

## The three endpoints

| Protocol | URL | Audio framing | Picked because |
|---|---|---|---|
| **Dalston native** | `ws://host/v1/audio/transcriptions/stream` | binary PCM | Lowest overhead. Building from scratch. |
| **ElevenLabs** | `ws://host/v1/speech-to-text/realtime` | base64 in JSON | You already have ElevenLabs SDK code |
| **OpenAI Realtime** | `ws://host/v1/realtime?intent=transcription` | base64 in JSON | You already have OpenAI Realtime client |

All three sit in front of the same real-time engines (NeMo, ONNX,
faster-whisper, vllm-asr, riva, hf-asr). The gateway translates the
incoming JSON / binary protocol into the engines' internal format. From
your audio model's perspective, it doesn't matter which front door you
came through.

Source files:

- Dalston native: [`dalston/gateway/api/v1/realtime.py:145`](../../dalston/gateway/api/v1/realtime.py#L145)
- ElevenLabs: [`dalston/gateway/api/v1/realtime.py:409`](../../dalston/gateway/api/v1/realtime.py#L409)
- OpenAI: [`dalston/gateway/api/v1/openai_realtime.py:612`](../../dalston/gateway/api/v1/openai_realtime.py#L612)

---

## Authentication

| Protocol | Where the key goes |
|---|---|
| Dalston native | `?api_key=dk_...` query param **or** `Authorization: Bearer dk_...` header |
| ElevenLabs | `?api_key=dk_...` query param **or** `Authorization: Bearer dk_...` |
| OpenAI | `Authorization: Bearer dk_...` header (+ `OpenAI-Beta: realtime=v1` header) |

The query param approach is the right choice for browser-based clients
(Web `WebSocket` API doesn't reliably support custom headers). For
server-side, the header is cleaner.

The API key needs the `realtime` scope. Auth happens **before** WebSocket
upgrade — failed auth gets a `4001` close code, not an HTTP 401 (the
upgrade never happens).

---

## Models and latency

The model parameter resolves to a real-time engine via the `engine_selector`
([dalston/orchestrator/engine_selector.py](../../dalston/orchestrator/)):

| Endpoint | Model param | Maps to |
|---|---|---|
| Dalston native | `model=faster-whisper-large-v3` (or `parakeet-rnnt-0.6b`, …) | direct engine ID |
| ElevenLabs | `model_id=scribe_v1` | `parakeet-0.6b` |
| ElevenLabs | `model_id=scribe_v2` | `parakeet-1.1b` |
| OpenAI | `model=gpt-4o-transcribe` | largest available Parakeet |
| OpenAI | `model=gpt-4o-mini-transcribe` | smaller Parakeet |
| OpenAI | `model=whisper-1` | Whisper streaming |

End-to-end latency budgets (verified from each engine's `engine.yaml`
`performance.warm_start_latency_ms`, plus typical chunk-to-result delay):

| Engine | Warm start | Per-chunk delivery | Best for |
|---|---|---|---|
| `nemo` | 100 ms | ~100 ms | live captions, dictation |
| `onnx` | 50 ms | ~150 ms | low-VRAM live |
| `faster-whisper` | 30 ms | ~300 ms (VAD-chunked) | multilingual live |
| `vllm-asr` | 5000 ms | ~500 ms | audio LLM apps |

For sub-200ms experiences, **NeMo on a warm GPU** is the answer.

---

## A request, end to end

Whichever endpoint you pick, the lifecycle looks the same:

1. **Client opens WebSocket** with auth + config in URL/headers.
2. **Gateway authenticates** before upgrading. On failure: 4001/4003/4029
   close codes.
3. **Gateway accepts** and picks a worker via the session router (least-loaded
   policy + capability filter).
4. **Gateway sends a session-start frame** specific to the protocol
   (`session.begin`, `transcription_session.created`, etc.).
5. **Client streams audio** as binary frames (Dalston native) or base64 in
   JSON (ElevenLabs/OpenAI).
6. **Server emits partial transcripts** as they become available. With
   `enable_vad=true` (Dalston/ElevenLabs) or auto-VAD (OpenAI), the server
   also emits speech-start/speech-end events.
7. **On commit / silence / explicit end**, server emits **final** transcript
   for the committed segment.
8. **Client closes** with a JSON close message or just disconnects.
9. **Server emits `session.end`** with summary stats and total transcript.
10. If `store_transcript=true`, the gateway persists the transcript to S3
    and creates a Job record (queryable via the batch API afterwards).

---

## Two big "soft" features

### Hybrid mode (real-time + batch enrichment)

Real-time engines optimize for latency, not necessarily diarization.
Common pattern: stream live captions for immediate UX, **then** kick off a
batch diarize job on the recorded audio after the session ends.

Enable with `store_audio=true` and `store_transcript=true` on the
WebSocket query params. The session ends with a Job record in the standard
batch surface; you can post-process for speaker labels.

### Resume / continuation

The Dalston native protocol accepts `resume_session_id` to link a new
session to a previous one — useful for clients that drop and reconnect.
The transcript context is preserved across reconnect.

ElevenLabs and OpenAI compat layers do not implement resume — they expect
clients to manage session state themselves.

---

## Picking the right protocol

| You're … | Pick |
|---|---|
| Migrating an ElevenLabs Scribe integration | [41-realtime-elevenlabs-compatible.md](41-realtime-elevenlabs-compatible.md) |
| Migrating an OpenAI Realtime integration | [42-realtime-openai-compatible.md](42-realtime-openai-compatible.md) |
| Building a new app from scratch | [43-realtime-dalston-native.md](43-realtime-dalston-native.md) |
| Building a browser mic widget | Dalston native (binary frames are smaller) |
| Building a server-side bridge | Whichever your upstream library prefers |

Each protocol page has copy-pasteable code in JS/Python.

---

## Limits

The gateway enforces:

- Per-API-key WebSocket session count limits (configurable, default 10
  concurrent)
- Per-key rate limits per second (configurable)
- Vocabulary: max 100 terms, 50 chars each
- Audio sample rate: must match `sample_rate` query param (16 kHz is the
  default and the most common engine input)

Violations produce `error` frames with codes (`rate_limit`,
`session_limit`, etc.) and a typed `recoverable` flag so the client can
decide whether to retry.

---

## See also

- [41-realtime-elevenlabs-compatible.md](41-realtime-elevenlabs-compatible.md) — ElevenLabs JSON protocol
- [42-realtime-openai-compatible.md](42-realtime-openai-compatible.md) — OpenAI Realtime protocol
- [43-realtime-dalston-native.md](43-realtime-dalston-native.md) — Dalston binary protocol
- [`docs/specs/realtime/REALTIME.md`](../specs/realtime/REALTIME.md) — engineering reference
- [`docs/specs/realtime/WEBSOCKET_API.md`](../specs/realtime/WEBSOCKET_API.md) — full wire protocol
- [`docs/specs/examples/websocket-clients.md`](../specs/examples/websocket-clients.md) — more client samples
