# Real-time: ElevenLabs-compatible WebSocket

> Already have ElevenLabs Scribe code? Change one URL and you're done.
> The wire protocol matches; auth uses your Dalston API key.

This is the right doc when:

- You're migrating off ElevenLabs and want to keep your existing client
- You want to test Dalston with ElevenLabs's own JS/Python SDKs
- You need a hosted-grade STT API but want to self-host the backend

For the protocol comparison, see [40-realtime-overview.md](40-realtime-overview.md).

---

## The endpoint

```
wss://<your-dalston>/v1/speech-to-text/realtime
    ?api_key=dk_...
    &model_id=scribe_v1                # scribe_v1 → parakeet-0.6b
                                       # scribe_v2 → parakeet-1.1b
    &language_code=en                  # ISO 639-1 or "auto"
    &audio_format=pcm_16000            # pcm_16000, pcm_8000, ulaw_8000, ...
    &commit_strategy=vad               # "vad" (auto-commit on silence) or "manual"
    &include_timestamps=true
    &keyterms=["PostgreSQL","Tailscale"]    # JSON array, max 100 / 50 chars
```

Source: [`dalston/gateway/api/v1/realtime.py:409`](../../dalston/gateway/api/v1/realtime.py#L409).

---

## Wire protocol summary

**Client → server** (always JSON):

```json
{
  "message_type": "input_audio_chunk",
  "audio_base_64": "<base64 PCM>",
  "commit": false,
  "sample_rate": 16000
}
```

To force a commit (manual mode):

```json
{ "message_type": "input_audio_chunk", "audio_base_64": "", "commit": true }
```

To close gracefully:

```json
{ "message_type": "close_connection" }
```

**Server → client** (also JSON):

| `message_type` | Content |
|---|---|
| `partial_transcript` | `text` (interim, may change) |
| `committed_transcript` | `text` (final) |
| `committed_transcript_with_timestamps` | `text` + `words: [{text, start, end}, ...]` |
| `language_detection` | `language_code`, `language_confidence` |
| `error` | `code`, `message` |

The full schema lives in [`docs/specs/realtime/WEBSOCKET_API.md`](../specs/realtime/WEBSOCKET_API.md).

---

## JavaScript — browser, mic capture

A complete client for in-browser microphone streaming. From the canonical
example at [`docs/specs/examples/websocket-clients.md`](../specs/examples/websocket-clients.md):

```javascript
const apiKey = 'dk_...';
const url = `wss://dalston.example.com/v1/speech-to-text/realtime?` +
            `api_key=${apiKey}&model_id=scribe_v1&language_code=en` +
            `&commit_strategy=vad&include_timestamps=true`;

const ws = new WebSocket(url);

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
        const base64 = btoa(String.fromCharCode(...new Uint8Array(int16.buffer)));
        ws.send(JSON.stringify({
            message_type: 'input_audio_chunk',
            audio_base_64: base64,
            commit: false,
        }));
    };

    source.connect(processor);
    processor.connect(audioCtx.destination);
};

ws.onmessage = (event) => {
    const m = JSON.parse(event.data);
    if (m.message_type === 'partial_transcript') {
        console.log('partial:', m.text);
    } else if (m.message_type === 'committed_transcript' ||
               m.message_type === 'committed_transcript_with_timestamps') {
        console.log('final:', m.text, m.words);
    } else if (m.message_type === 'error') {
        console.error(m.code, m.message);
    }
};
```

> **Browser secure context.** `getUserMedia` requires HTTPS or `localhost`.
> Local dev works on `http://localhost:8000`. AWS deployment via
> `dalston-aws` exposes HTTPS on `*.ts.net` automatically.

---

## Python — server-side or CLI usage

```python
import asyncio
import base64
import json
import websockets

API_KEY = "dk_..."
URL = (
    "wss://dalston.example.com/v1/speech-to-text/realtime"
    f"?api_key={API_KEY}"
    "&model_id=scribe_v1"
    "&language_code=en"
    "&commit_strategy=vad"
    "&include_timestamps=true"
)

async def transcribe(audio_chunks):
    async with websockets.connect(URL) as ws:
        async def send():
            async for chunk in audio_chunks:        # bytes (PCM 16kHz mono)
                await ws.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(chunk).decode(),
                    "commit": False,
                }))
            await ws.send(json.dumps({"message_type": "close_connection"}))

        async def recv():
            async for raw in ws:
                m = json.loads(raw)
                t = m.get("message_type")
                if t == "partial_transcript":
                    print(f"\r{m['text']}", end="", flush=True)
                elif t in ("committed_transcript", "committed_transcript_with_timestamps"):
                    print(f"\n[final] {m['text']}")
                elif t == "error":
                    print(f"\nerror: {m.get('code')} {m.get('message')}")
                    break

        await asyncio.gather(send(), recv())


async def chunks_from_file(path: str):
    """Yield 100ms PCM chunks from a 16-bit 16 kHz mono raw file."""
    chunk_size = 3200    # 100 ms at 16 kHz, 16-bit
    with open(path, "rb") as f:
        while data := f.read(chunk_size):
            yield data
            await asyncio.sleep(0.1)        # pace for streaming feel

asyncio.run(transcribe(chunks_from_file("audio.pcm")))
```

To convert mp3 / wav to raw PCM:

```bash
ffmpeg -i input.mp3 -f s16le -ac 1 -ar 16000 audio.pcm
```

---

## Auto-commit (VAD) vs manual commit

`commit_strategy=vad` is the default and what most clients want. The
server detects speech start, accumulates audio while you're speaking, and
emits a `committed_transcript` when it detects silence. You don't manage
boundaries.

`commit_strategy=manual` puts you in charge: send `commit: true` whenever
you want a final transcript for the audio so far. Use this when you have
better speech-boundary signals from your application — e.g. push-to-talk
UIs.

VAD knobs (only relevant in `vad` mode):

| Param | Default | What |
|---|---|---|
| `vad_threshold` | 0.5 | 0–1 sensitivity for speech detection |
| `min_speech_duration_ms` | engine default | shorter speech is ignored |
| `min_silence_duration_ms` | engine default | silence shorter than this doesn't trigger commit |
| `prefix_padding_ms` | engine default | audio kept before detected speech start |

---

## Common errors

- **`4001 Invalid API key`** — your key is wrong, doesn't have `realtime`
  scope, or hit rate limit. Mint a new key with the right scope in the
  console.
- **`Invalid commit_strategy`** — must be `manual` or `vad`. Anything else
  closes the connection.
- **`Invalid audio_format`** — see the supported list:
  `pcm_16000`, `pcm_8000`, `pcm_22050`, `pcm_44100`, `pcm_48000`,
  `ulaw_8000`, `alaw_8000`. Match this to your actual encoder.
- **No partials arriving** — confirm `commit_strategy=vad` and that you're
  actually sending audio bytes (not silent zero buffers).

---

## When to use this vs the OpenAI / Dalston native versions

- **Migrating off ElevenLabs** — this is your path. URL change, done.
- **Browser-based mic capture from scratch** — Dalston native is more
  efficient (binary frames, no base64 overhead). See
  [43-realtime-dalston-native.md](43-realtime-dalston-native.md).
- **OpenAI Realtime SDK already in your codebase** — see
  [42-realtime-openai-compatible.md](42-realtime-openai-compatible.md).

---

## See also

- [40-realtime-overview.md](40-realtime-overview.md) — protocol comparison
- [42-realtime-openai-compatible.md](42-realtime-openai-compatible.md)
- [43-realtime-dalston-native.md](43-realtime-dalston-native.md)
- [`docs/specs/realtime/WEBSOCKET_API.md`](../specs/realtime/WEBSOCKET_API.md)
- [`docs/specs/elevenlabs/PARITY_GAPS.md`](../specs/elevenlabs/PARITY_GAPS.md) — what's not 1:1 with ElevenLabs
- [`docs/specs/examples/websocket-clients.md`](../specs/examples/websocket-clients.md) — full sample apps
