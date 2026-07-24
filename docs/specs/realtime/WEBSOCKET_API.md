# WebSocket API reference

Dalston supports native, OpenAI-compatible, and ElevenLabs-compatible realtime
transcription. All adapters share worker allocation and capacity, but their
wire messages are intentionally different.

## Authentication

Server-side clients should send:

```http
Authorization: Bearer <DALSTON_API_KEY>
```

Browser clients that cannot set a WebSocket authorization header can use the
supported protocol-specific single-use token flow. Do not put a long-lived API
key in a browser URL.

Authentication failures close with `4001`; a key without the required scope
closes with `4003`.

## Dalston-native protocol

Connect to:

```text
wss://HOST/v1/audio/transcriptions/stream
```

Query parameters:

| Name | Default | Notes |
| --- | --- | --- |
| `language` | `auto` | Language code or automatic detection |
| `model` | empty | Registry model ID; empty enables automatic selection |
| `encoding` | `pcm_s16le` | Raw audio encoding |
| `sample_rate` | `16000` | Sample rate in Hz |
| `enable_vad` | `true` | Emit VAD events |
| `interim_results` | `true` | Emit provisional transcript events |
| `word_timestamps` | `false` | Request word timing when supported |
| `vocabulary` | omitted | JSON array, at most 100 terms of 50 characters |
| `retention` | server default | `0`, `-1`, or 1–3650 days |
| `resume_session_id` | omitted | Link the new session to a prior session |
| `pii_detection` | `false` | Detect PII in a retained transcript |
| `pii_entity_types` | omitted | Comma-separated entity types |
| `redact_pii_audio` | `false` | Create retained redacted audio |
| `pii_redaction_mode` | `silence` | `silence` or `beep` |

Send audio as binary frames. JSON control messages include `end` to finish
input. Server events use the internal public names:

- `session.begin`
- `speech.start` / `speech.end`
- `transcript.partial`
- `transcript.final`
- `warning`
- `error`
- `session.end`

Partial text is replaceable. Append only `transcript.final`. A normal client
should send `end`, continue reading through `session.end`, then close.

See [the native client guide](../../guides/43-realtime-dalston-native.md) and
[client examples](../examples/websocket-clients.md).

## ElevenLabs-compatible protocol

Connect to:

```text
wss://HOST/v1/speech-to-text/realtime
```

Supported query parameters are `language_code`, `model_id`, `audio_format`,
`commit_strategy`, `include_timestamps`, `include_language_detection`,
`keyterms`, `previous_text`, `vad_threshold`, `min_speech_duration_ms`,
`min_silence_duration_ms`, and `prefix_padding_ms`.

`commit_strategy` defaults to `manual`. Audio formats are validated through the
ElevenLabs adapter, including PCM and µ-law variants supported by the current
code. The compatibility model IDs are labels used by the adapter; actual
routing resolves through Dalston's model registry and live workers.

Clients send JSON `input_audio_chunk` messages containing `audio_base_64`.
Manual mode uses the message's commit signal to finalize an utterance. Server
messages include `session_started`, `partial_transcript`,
`committed_transcript`, `committed_transcript_with_timestamps`,
`input_error`, and session termination.

Words in timestamped responses include `text`, `start`, `end`, and `type`.
`logprob` and `characters` are included when the engine supplies them.

See [the ElevenLabs guide](../../guides/41-realtime-elevenlabs-compatible.md).
The compatibility suite is tested against `elevenlabs==2.47.0`.

## OpenAI-compatible protocol

Connect to:

```text
wss://HOST/v1/realtime?intent=transcription&model=gpt-4o-transcribe
```

`intent` must be `transcription`. `OpenAI-Beta: realtime=v1` is accepted but
optional.

Dalston accepts both current transcription-session nesting and the older flat
session-update shape, normalizing them internally. Relevant configuration
includes input audio format, transcription model/language/prompt, server VAD,
and input noise reduction.

Client events include:

- `transcription_session.update` or compatible `session.update`
- `input_audio_buffer.append`
- `input_audio_buffer.commit`
- `input_audio_buffer.clear`

Server events include session created/updated events, speech started/stopped,
transcription delta/completed, and `error`. Exact payload examples are in
[the OpenAI realtime guide](../../guides/42-realtime-openai-compatible.md).

`POST /v1/realtime/transcription_sessions` creates an ephemeral
OpenAI-compatible client secret and returns the normalized session
configuration.

## Limits and close codes

| Code | Meaning |
| --- | --- |
| `1000` | Normal completion |
| `4001` | Missing, invalid, or expired credentials |
| `4003` | Required scope missing |
| `4010` | Audio lag budget exceeded |
| `4029` | API-key request-rate limit |
| `4400` | Invalid realtime parameters or unsupported intent |
| `4429` | Concurrent-session limit |
| `4503` | No compatible worker capacity / service unavailable |

The default concurrent limit is 5 sessions per tenant
(`DALSTON_RATE_LIMIT_CONCURRENT_SESSIONS`). Message size, audio rate, queue lag,
and utterance duration are also bounded by gateway/worker implementation.

Clients should retry `4429` and `4503` with jittered backoff. Do not retry
authentication or parameter failures unchanged. A `4010` disconnect requires
slower sending, lower load, or more capacity.

## Persistence and reconnection

Retention is opt-in/configurable and does not change the live transcript
protocol. `resume_session_id` creates lineage only; a reconnect is a new
session with fresh decoder state. There is no sequence-number replay protocol
and no automatic batch enhancement.

Use `/v1/realtime/sessions` to discover persisted sessions and the transcript,
audio, and export endpoints to retrieve retained artifacts. See
[Session persistence](./SESSION_PERSISTENCE.md).
