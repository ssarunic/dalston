# OpenAI-compatible API

Dalston implements the OpenAI speech-to-text compatibility surface at the
gateway and, for leaf/composite transcribe engines that expose the HTTP server,
at the engine endpoint. The gateway adds authentication, model registry
routing, persistence, async-native APIs, exports, webhooks, and multi-stage
processing.

Compatibility is tested with `openai==2.37.0`. Pin the version your application
tests; OpenAI's SDK and public contract can evolve independently of Dalston.

## Authentication

Point the SDK at Dalston and use a Dalston API key:

```python
from openai import OpenAI

client = OpenAI(
    api_key="dk_...",
    base_url="http://localhost:8000/v1",
)
```

The gateway accepts `Authorization: Bearer`. A string with an OpenAI-looking
prefix is not exchanged with OpenAI; it is validated as a Dalston credential.

## Transcription

```python
with open("audio.wav", "rb") as audio:
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio,
        response_format="verbose_json",
        timestamp_granularities=["word"],
    )
```

Endpoint:

```http
POST /v1/audio/transcriptions
```

An OpenAI model label switches this shared route into synchronous compatibility
mode. Supported request fields include `file`, `model`, `language`, `prompt`,
`response_format`, `temperature`, timestamp granularities, `include`,
`known_speaker_names`, and `chunking_strategy`, subject to model/format
validation.

Supported response formats are `json`, `text`, `srt`, `verbose_json`, `vtt`,
and `diarized_json` where the selected model/engine supports the required data.
Timestamp granularities require `verbose_json`. Optional fields are omitted or
null when the engine cannot supply them.

OpenAI-compatible requests have a 25 MB upload ceiling. Dalston's native route
uses the same path but native model selection/parameters and different
asynchronous response behavior.

## Translation

```http
POST /v1/audio/translations
```

Translation uses the OpenAI-compatible multipart contract and returns a
synchronous response. Availability depends on a compatible engine/model.

## Realtime transcription

Create an ephemeral session:

```http
POST /v1/realtime/transcription_sessions
```

Then connect:

```text
wss://HOST/v1/realtime?intent=transcription&model=gpt-4o-transcribe
```

The WebSocket requires `intent=transcription`. An API key or issued ephemeral
client secret can authenticate the connection. `OpenAI-Beta: realtime=v1` is
accepted but optional.

Dalston accepts current nested transcription-session updates and the older flat
shape supported by its compatibility tests. It handles audio buffer append,
commit, and clear events and translates worker output to OpenAI delta/completed,
speech boundary, session, and error events.

See [OpenAI realtime guide](../../guides/42-realtime-openai-compatible.md) and
[WebSocket reference](../realtime/WEBSOCKET_API.md) for tested examples.

## Routing

Compatibility model names are adapter labels, not a guarantee that one hard
coded internal model runs. Dalston resolves an eligible registry model and live
engine. If no compatible capacity exists, the request fails rather than
silently contacting OpenAI.

Direct engine HTTP mounts execute only that engine and do not provide gateway
control-plane features. Unsupported optional fields return `400`.

## Errors and limits

HTTP compatibility errors use an OpenAI-style `error` object. Realtime errors
use OpenAI event envelopes when a message can be sent, followed by the
appropriate WebSocket close code.

Gateway defaults are 600 requests/minute, 10 concurrent batch jobs, and 5
concurrent realtime sessions. OpenAI-style rate-limit headers are added by the
compatibility path where applicable. Configure Dalston limits with the
`DALSTON_RATE_LIMIT_*` settings.

## Deliberate differences

- Dalston is self-hosted and executes registered local/private engines.
- The native API provides async jobs, explicit retention, stage selection,
  tasks/artifacts, exports, and webhooks beyond the compatibility contract.
- Realtime persistence is a Dalston management feature, not part of the OpenAI
  wire protocol.
- Supported model/format combinations are limited to capabilities implemented
  and tested by the selected engine.

For known parity work and evidence requirements, see
[the internal parity analysis](./PARITY_GAPS.md). That analysis is engineering
planning material, not a promise that every listed provider feature is
implemented.
