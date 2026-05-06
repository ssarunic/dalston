# Real-time: OpenAI-compatible WebSocket

> Already integrated with the OpenAI Realtime API for transcription? Point
> your client at Dalston instead. The wire protocol matches. Same
> `transcription_session.update`, same `input_audio_buffer.append`, same
> `conversation.item.input_audio_transcription.completed` events.

This is the right doc when:

- You're moving off the OpenAI Realtime transcription API
- You want to keep using OpenAI's official client libraries
- You want OpenAI semantics with self-hosted compute economics

For protocol comparison, see [40-realtime-overview.md](40-realtime-overview.md).

---

## The endpoint

```
wss://<your-dalston>/v1/realtime?intent=transcription&model=gpt-4o-transcribe

Headers:
  Authorization: Bearer dk_...           # your Dalston API key
  OpenAI-Beta: realtime=v1               # optional but conventional
```

Source: [`dalston/gateway/api/v1/openai_realtime.py:612`](../../dalston/gateway/api/v1/openai_realtime.py#L612).

> **Note on the URL.** OpenAI's hosted endpoint is `wss://api.openai.com/...`.
> Dalston's path is `wss://<your-host>/v1/realtime?intent=transcription`.
> The `intent=transcription` query param is required — Dalston only
> implements the transcription intent, not full conversation.

---

## Model mapping

OpenAI model IDs resolve to Dalston engines:

| OpenAI ID | Dalston engine |
|---|---|
| `gpt-4o-transcribe` | largest available Parakeet (typically 1.1B) |
| `gpt-4o-mini-transcribe` | smaller Parakeet (0.6B) |
| `whisper-1` | Whisper streaming (faster-whisper) |

---

## Wire protocol — the relevant slice

This is OpenAI's Realtime protocol with the conversation/agent surface
omitted. Both directions use JSON.

**Client → server**:

```json
// 1. Configure the session (sent right after connecting)
{
  "type": "transcription_session.update",
  "session": {
    "input_audio_format": "pcm16",
    "input_audio_transcription": { "model": "gpt-4o-transcribe", "language": "en" },
    "turn_detection": { "type": "server_vad" }
  }
}

// 2. Send audio
{ "type": "input_audio_buffer.append", "audio": "<base64 PCM 16kHz mono>" }

// 3. Force a commit (manual mode)
{ "type": "input_audio_buffer.commit" }

// 4. Clear unprocessed audio
{ "type": "input_audio_buffer.clear" }
```

**Server → client**:

| `type` | What |
|---|---|
| `transcription_session.created` | Session opened, default config echoed |
| `transcription_session.updated` | Your update was applied |
| `input_audio_buffer.speech_started` | VAD detected speech (timestamps in ms) |
| `input_audio_buffer.speech_stopped` | VAD detected silence |
| `input_audio_buffer.committed` | Buffer committed for processing |
| `conversation.item.input_audio_transcription.delta` | Partial transcript (`delta` field) |
| `conversation.item.input_audio_transcription.completed` | Final transcript |
| `error` | Error with `event_id`, `code`, `message` |

---

## JavaScript example

> **Browser auth note.** The Dalston gateway accepts the API key via
> `Authorization` header (server-side) or via `?api_key=` query param
> (browser-friendly). It does **not** parse the OpenAI-style subprotocol
> auth string used by `api.openai.com`. So in the browser, append your key
> to the URL.

```javascript
const apiKey = 'dk_...';
const url = `wss://dalston.example.com/v1/realtime` +
            `?intent=transcription&model=gpt-4o-transcribe&api_key=${apiKey}`;

const ws = new WebSocket(url);

ws.onopen = () => {
    ws.send(JSON.stringify({
        type: 'transcription_session.update',
        session: {
            input_audio_format: 'pcm16',
            input_audio_transcription: { model: 'gpt-4o-transcribe', language: 'en' },
            turn_detection: { type: 'server_vad' },
        },
    }));
};

ws.onmessage = (event) => {
    const m = JSON.parse(event.data);
    switch (m.type) {
        case 'conversation.item.input_audio_transcription.delta':
            console.log('partial:', m.delta);
            break;
        case 'conversation.item.input_audio_transcription.completed':
            console.log('final:', m.transcript);
            break;
        case 'input_audio_buffer.speech_started':
            console.log('speech start at', m.audio_start_ms, 'ms');
            break;
        case 'error':
            console.error(m.error);
            break;
    }
};

// To stream PCM chunks:
function sendPCM(int16Buffer) {
    const b64 = btoa(String.fromCharCode(...new Uint8Array(int16Buffer.buffer)));
    ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: b64 }));
}
```

---

## Python — using the OpenAI SDK directly

You can use OpenAI's official Python client by overriding the base URL and
key:

```python
# Approach 1: their AsyncOpenAI realtime client (when stable)
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://dalston.example.com/v1",
    api_key="dk_...",
)
# client.beta.realtime.transcription.connect(...)
```

Or just speak the protocol with `websockets`:

```python
import asyncio
import base64
import json
import websockets

API_KEY = "dk_..."
URL = "wss://dalston.example.com/v1/realtime?intent=transcription&model=gpt-4o-transcribe"

async def main(audio_chunks):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }
    async with websockets.connect(URL, additional_headers=headers) as ws:
        # Configure session
        await ws.send(json.dumps({
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-transcribe",
                    "language": "en",
                },
                "turn_detection": {"type": "server_vad"},
            },
        }))

        async def send():
            async for chunk in audio_chunks:
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))

        async def recv():
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("type")
                if t == "conversation.item.input_audio_transcription.delta":
                    print(f"\r{m.get('delta', '')}", end="", flush=True)
                elif t == "conversation.item.input_audio_transcription.completed":
                    print(f"\n[final] {m.get('transcript', '')}")
                elif t == "error":
                    print(f"\nerror: {m['error']}")
                    break

        await asyncio.gather(send(), recv())

# ... pass an async iterator of 16-bit 16 kHz mono PCM bytes ...
```

---

## What's the same vs OpenAI's hosted API

**Same:**

- Event types and JSON shapes (`transcription_session.update`,
  `input_audio_buffer.*`, `conversation.item.input_audio_transcription.*`,
  `error`)
- Model name strings (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`,
  `whisper-1`)
- `Authorization: Bearer ...` header convention
- VAD events surfaced as `speech_started` / `speech_stopped`

**Different:**

- Different host. `wss://<your-dalston>/v1/realtime` instead of OpenAI.
- Models behind the scenes are NeMo Parakeet / faster-whisper, not GPT-4o.
  Audio quality is excellent but the underlying weights are different.
- No conversation / response generation — this is **transcription only**.
  `intent` must be `"transcription"`.
- API keys are Dalston keys (`dk_...`), not OpenAI keys.

---

## See also

- [40-realtime-overview.md](40-realtime-overview.md)
- [41-realtime-elevenlabs-compatible.md](41-realtime-elevenlabs-compatible.md)
- [43-realtime-dalston-native.md](43-realtime-dalston-native.md)
- [`docs/specs/realtime/WEBSOCKET_API.md`](../specs/realtime/WEBSOCKET_API.md)
- [`docs/specs/examples/websocket-clients.md`](../specs/examples/websocket-clients.md)
