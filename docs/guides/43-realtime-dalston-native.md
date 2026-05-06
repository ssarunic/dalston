# Real-time: Dalston native WebSocket

> Send raw PCM bytes as binary frames. Receive structured JSON events.
> Lowest overhead, lowest latency, no base64 padding tax. The right pick
> when you're building from scratch and care about throughput.

For protocol comparison, see [40-realtime-overview.md](40-realtime-overview.md).

---

## The endpoint

```
ws://<your-dalston>/v1/audio/transcriptions/stream
    ?api_key=dk_...
    &language=en                       # or "auto"
    &model=parakeet-rnnt-0.6b          # engine ID; empty for any
    &encoding=pcm_s16le                # pcm_s16le, pcm_f32le, mulaw, alaw
    &sample_rate=16000
    &enable_vad=true
    &interim_results=true
    &word_timestamps=true
    &vocabulary=["PostgreSQL","Tailscale"]    # JSON array, max 100 / 50 chars
```

Source: [`dalston/gateway/api/v1/realtime.py:145`](../../dalston/gateway/api/v1/realtime.py#L145).

Auth supports both:

- `?api_key=dk_...` query param (browser-friendly)
- `Authorization: Bearer dk_...` header (server-friendly)

---

## Wire protocol

**Client → server**:

- **Binary frames**: raw PCM bytes (the encoding you specified). No
  framing overhead, no base64.
- **JSON control messages** (text frames):

  ```json
  { "type": "config", "language": "es" }    // change language mid-stream
  { "type": "flush" }                        // force a partial → final
  { "type": "end" }                          // graceful close
  ```

**Server → client** (always JSON, types defined in
[`dalston/realtime_sdk/protocol.py`](../../dalston/realtime_sdk/protocol.py)):

| `type` | Content |
|---|---|
| `session.begin` | `session_id`, `config` |
| `vad.speech_start` | `timestamp` (seconds since session start) |
| `vad.speech_end` | `timestamp` |
| `transcript.partial` | `text`, `start`, `end` (interim, may change) |
| `transcript.final` | `text`, `start`, `end`, `confidence`, `words` (final) |
| `session.end` | `total_duration`, `total_speech_duration`, `transcript`, `segments` |
| `error` | `code`, `message`, `recoverable` |

---

## Python — `AsyncRealtimeSession` from the SDK

The cleanest option. The SDK handles framing, auth, and message parsing:

```python
import asyncio
from dalston_sdk import AsyncRealtimeSession, RealtimeMessageType

async def main():
    async with AsyncRealtimeSession(
        base_url="ws://localhost:8000",
        api_key="dk_...",
        language="en",
        model="parakeet-rnnt-0.6b",     # or leave blank for any
        word_timestamps=True,
        vocabulary=["PostgreSQL", "Tailscale"],
    ) as session:
        await session.connect()
        print(f"session id: {session.session_id}")

        async def feed():
            async for chunk in pcm_chunks_from_mic():     # bytes, 16kHz mono PCM
                await session.send_audio(chunk)

        async def consume():
            async for msg in session:
                if msg.type == RealtimeMessageType.TRANSCRIPT_PARTIAL:
                    print(f"\r{msg.data.text}", end="", flush=True)
                elif msg.type == RealtimeMessageType.TRANSCRIPT_FINAL:
                    print(f"\n[final] {msg.data.text}")
                elif msg.type == RealtimeMessageType.SESSION_END:
                    break

        await asyncio.gather(feed(), consume())

asyncio.run(main())
```

---

## Python — raw `websockets`

If you don't want the SDK:

```python
import asyncio
import json
import websockets

URL = (
    "ws://localhost:8000/v1/audio/transcriptions/stream"
    "?api_key=dk_..."
    "&language=en"
    "&encoding=pcm_s16le"
    "&sample_rate=16000"
    "&enable_vad=true"
    "&interim_results=true"
    "&word_timestamps=true"
)

async def main(audio_chunks):
    async with websockets.connect(URL) as ws:
        async def send():
            async for chunk in audio_chunks:        # bytes
                await ws.send(chunk)                # binary frame
            await ws.send(json.dumps({"type": "end"}))

        async def recv():
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue                        # server only sends JSON
                m = json.loads(raw)
                t = m.get("type")
                if t == "transcript.partial":
                    print(f"\r{m['text']}", end="", flush=True)
                elif t == "transcript.final":
                    print(f"\n[final] {m['text']}")
                elif t == "session.end":
                    break
                elif t == "error":
                    print(f"\nerror: {m['code']} {m['message']}")
                    break

        await asyncio.gather(send(), recv())
```

---

## JavaScript — browser, mic capture

```javascript
const apiKey = 'dk_...';
const url = `ws://localhost:8000/v1/audio/transcriptions/stream` +
            `?api_key=${apiKey}` +
            `&language=en&encoding=pcm_s16le&sample_rate=16000` +
            `&enable_vad=true&interim_results=true&word_timestamps=true`;

const ws = new WebSocket(url);
ws.binaryType = 'arraybuffer';

ws.onopen = async () => {
    const audioCtx = new AudioContext({ sampleRate: 16000 });
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const source = audioCtx.createMediaStreamSource(stream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (event) => {
        const float32 = event.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
        }
        // BINARY frame — raw bytes, no base64
        ws.send(int16.buffer);
    };

    source.connect(processor);
    processor.connect(audioCtx.destination);
};

ws.onmessage = (event) => {
    if (typeof event.data !== 'string') return;     // ignore any binary
    const m = JSON.parse(event.data);
    switch (m.type) {
        case 'session.begin':       console.log('session', m.session_id); break;
        case 'transcript.partial':  console.log('partial:', m.text); break;
        case 'transcript.final':    console.log('final:', m.text, m.words); break;
        case 'vad.speech_start':    console.log('speech start at', m.timestamp); break;
        case 'vad.speech_end':      console.log('speech end at', m.timestamp); break;
        case 'session.end':         console.log('end', m); break;
        case 'error':               console.error(m.code, m.message); break;
    }
};
```

> **Why binary?** Base64 inflates payloads by ~33%. At 16 kHz 16-bit mono
> that's ~32 KB/s of audio; base64 turns it into ~42 KB/s. Over a session
> of any length the savings stack up.

---

## Resume / continuation

Add `resume_session_id=<previous_session_id>` to the URL when reconnecting.
The server links the new session to the old one and preserves transcript
context. Great for clients that drop and reconnect (mobile, flaky networks).

---

## Knobs that matter

| Param | Default | When to change |
|---|---|---|
| `enable_vad` | true | Disable if you have your own VAD upstream |
| `interim_results` | true | Disable if you only care about final transcripts |
| `word_timestamps` | false | Set true for word-by-word display; engine must support it |
| `vocabulary` | none | Boost domain-specific terms (max 100, 50 chars each) |
| `store_audio` | false (env-default in SDK: true) | Save to S3 for post-processing / batch enrichment |
| `store_transcript` | false (env-default in SDK: true) | Auto-create a Job record for the session |
| `retention` | server default | Override retention for stored data |
| `resume_session_id` | none | Reconnect after disconnect |

---

## Comparing to ElevenLabs/OpenAI compat layers

This protocol is **30%+ more efficient** than the JSON-base64 protocols in
bytes-on-the-wire. It also gives you VAD events directly (`vad.speech_*`),
typed errors with `recoverable`, and resume-by-ID.

The trade is that it's Dalston-specific — no existing client SDK speaks it
(other than this repo's). If you have ElevenLabs or OpenAI client code,
those compat layers are the path of least resistance.

---

## See also

- [40-realtime-overview.md](40-realtime-overview.md) — pick the right protocol
- [41-realtime-elevenlabs-compatible.md](41-realtime-elevenlabs-compatible.md)
- [42-realtime-openai-compatible.md](42-realtime-openai-compatible.md)
- [24-using-the-python-sdk.md](24-using-the-python-sdk.md) — `AsyncRealtimeSession`
- [`docs/specs/realtime/WEBSOCKET_API.md`](../specs/realtime/WEBSOCKET_API.md) — full wire spec
- [`dalston/realtime_sdk/protocol.py`](../../dalston/realtime_sdk/protocol.py) — message classes
