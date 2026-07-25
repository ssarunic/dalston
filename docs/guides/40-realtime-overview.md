# Realtime transcription: three protocols, one worker pool

Dalston exposes three WebSocket protocols in front of the registered realtime
workers:

| Protocol | Endpoint | Audio framing |
| --- | --- | --- |
| Dalston native | `/v1/audio/transcriptions/stream` | Binary PCM |
| ElevenLabs compatible | `/v1/speech-to-text/realtime` | Base64 in JSON |
| OpenAI compatible | `/v1/realtime?intent=transcription` | Base64 in JSON |

Use the compatibility endpoint that matches an existing integration. For a new
client, the native endpoint avoids base64 expansion and has the smallest wire
format.

## Authentication

Dalston native and ElevenLabs clients can send a Dalston API key through
`?api_key=dk_...` or `Authorization: Bearer dk_...`. OpenAI-compatible
server-side clients normally use the Authorization header; browser clients can
use the query parameter because the browser `WebSocket` API cannot set arbitrary
headers.

The key needs realtime scope. Common connection close codes are:

| Code | Meaning |
| --- | --- |
| `4400` | Invalid request parameters |
| `4001` | Missing or invalid authentication |
| `4003` | Missing required scope |
| `4029` | API-key request-rate limit |
| `4429` | Concurrent-session limit |
| `4503` | No compatible worker capacity |
| `4010` | Client audio lag exceeded the recoverable threshold |

## Routing

The orchestrator’s session coordinator selects a ready worker by capability,
language/model preference, and available capacity.

- Native `model` is a routing preference for a registered model or engine.
- ElevenLabs `scribe_v1` and `scribe_v2` are accepted compatibility labels;
  they currently use automatic ready-worker routing.
- OpenAI `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, and `whisper-1` are
  accepted compatibility labels; they also route automatically.

Do not infer the concrete model weights from a compatibility name. Inspect
session metadata or the engine registry when the exact runtime matters.

Latency depends on the selected worker, loaded model, VAD/commit settings,
network, and audio chunk size. Values in `engine.yaml` are benchmark metadata,
not an end-to-end latency guarantee.

## Common lifecycle

1. The client opens a WebSocket with authentication and configuration.
2. The gateway validates the request and asks the session coordinator for a
   compatible worker.
3. The gateway accepts the connection and sends the protocol’s session-start
   event.
4. The client streams binary or base64 audio.
5. The worker emits interim transcript and optional VAD events.
6. Silence, a commit message, or a flush produces a final segment.
7. The client sends the protocol’s graceful-end message.
8. The worker returns a session summary and the coordinator releases capacity.

The wire lifecycle is similar, but persistence is not identical:

- Native sessions are recorded in the realtime session ledger.
- Native audio/transcript persistence is derived from `retention`: zero is
  transient; a non-zero value permits storage.
- ElevenLabs-compatible sessions currently do not create the same persisted
  realtime session record.
- Realtime completion does not automatically create a batch enhancement job.

To add speaker diarization after a live session, retain the recording and
submit it separately to the batch API with `speaker_detection=diarize`.

## Resume linkage

The native endpoint accepts `resume_session_id`. This records the prior session
as lineage for the new session. It does not currently replay transcript state
or restore decoding context. ElevenLabs and OpenAI compatibility endpoints do
not expose this linkage.

## Limits

Defaults are configurable, but the current application defaults include:

- five concurrent realtime sessions per tenant;
- 600 API requests per minute per tenant;
- vocabulary limited to 100 terms of at most 50 characters; and
- a 16 kHz default sample rate.

The realtime worker also monitors audio lag. Recoverable lag produces warning
messages; sustained excessive lag terminates the session so clients do not
receive indefinitely delayed transcripts.

## Choose a protocol

| Situation | Guide |
| --- | --- |
| Existing ElevenLabs Scribe integration | [ElevenLabs-compatible](41-realtime-elevenlabs-compatible.md) |
| Existing OpenAI Realtime integration | [OpenAI-compatible](42-realtime-openai-compatible.md) |
| New application or browser microphone UI | [Dalston native](43-realtime-dalston-native.md) |

## Sources

- Native and ElevenLabs gateway:
  [`dalston/gateway/api/v1/realtime.py`](../../dalston/gateway/api/v1/realtime.py)
- OpenAI gateway:
  [`dalston/gateway/api/v1/openai_realtime.py`](../../dalston/gateway/api/v1/openai_realtime.py)
- Session coordination:
  [`dalston/orchestrator/session_coordinator.py`](../../dalston/orchestrator/session_coordinator.py)
- Wire reference:
  [WEBSOCKET_API.md](../specs/realtime/WEBSOCKET_API.md)
