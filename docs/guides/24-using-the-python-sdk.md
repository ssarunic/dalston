# Using the Python SDK

> Three classes do everything: `Dalston` for sync batch, `AsyncDalston` for
> async batch, `AsyncRealtimeSession` for streaming. Plus webhook signature
> helpers. That's the whole API surface.

```bash
pip install -e ./sdk
```

Source: [`sdk/dalston_sdk/`](../../sdk/dalston_sdk/). Public exports are
listed in [`sdk/dalston_sdk/__init__.py`](../../sdk/dalston_sdk/__init__.py).

---

## Sync batch — the everyday client

```python
from dalston_sdk import Dalston

client = Dalston(
    base_url="http://localhost:8000",       # or your tailnet URL
    api_key="dk_...",
)

job = client.transcribe("meeting.mp3", language="en")
job = client.wait_for_completion(job.id)
print(job.transcript.text)
```

`transcribe()` returns immediately with a pending `Job`; `wait_for_completion()`
polls until done (or raises if the job fails). All the knobs:

```python
job = client.transcribe(
    file="meeting.mp3",                    # or audio_url="https://..."
    model="auto",                          # engine_id or "auto"
    language="auto",
    vocabulary=["PostgreSQL", "Kubernetes"],
    speaker_detection="diarize",           # "none", "diarize", "per-channel"
    num_speakers=2,                        # exact (overrides min/max)
    timestamps_granularity="word",         # "none", "segment", "word"
    retention=30,                          # days; 0 = transient, -1 = permanent
)
```

Iterate results:

```python
for segment in job.transcript.segments:
    speaker = segment.speaker_id or "unknown"
    print(f"[{segment.start:.1f}–{segment.end:.1f}] {speaker}: {segment.text}")
    for word in segment.words or []:
        print(f"  {word.text}  {word.start:.2f}s")
```

---

## Async batch — for I/O-bound workloads

```python
import asyncio
from dalston_sdk import AsyncDalston

async def main():
    async with AsyncDalston(
        base_url="http://localhost:8000",
        api_key="dk_...",
    ) as client:
        # Submit many in parallel
        jobs = await asyncio.gather(*[
            client.transcribe(p, language="en")
            for p in ["a.mp3", "b.mp3", "c.mp3"]
        ])
        # Wait on all of them
        results = await asyncio.gather(*[
            client.wait_for_completion(j.id) for j in jobs
        ])
        for r in results:
            print(r.transcript.text[:80])

asyncio.run(main())
```

---

## Real-time streaming

```python
import asyncio
from dalston_sdk import AsyncRealtimeSession, RealtimeMessageType

async def main():
    async with AsyncRealtimeSession(
        base_url="ws://localhost:8000",
        api_key="dk_...",
        language="en",
        word_timestamps=True,
        vocabulary=["PostgreSQL"],
    ) as session:
        await session.connect()

        # Producer: send PCM frames
        async def feed():
            for chunk in pcm_chunks_from_mic():
                await session.send_audio(chunk)

        # Consumer: receive transcripts and VAD events
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

The session uses Dalston's binary WebSocket protocol — raw PCM bytes,
no base64 overhead. See [43-realtime-dalston-native.md](43-realtime-dalston-native.md)
for the wire-level details, or [40-realtime-overview.md](40-realtime-overview.md)
for the protocol comparison.

A synchronous `RealtimeSession` exists too, with the same constructor args
but blocking `connect()` / `send_audio()`. Use it from non-async code.

---

## Job management

```python
client.list_jobs(status="completed", limit=50)
job = client.get_job(job_id)
client.cancel(job_id)
client.get_job_artifacts(job_id)  # S3 references for raw outputs
```

Full job deletion is available through the CLI (`dalston jobs delete JOB_ID`) or
the REST API (`DELETE /v1/audio/transcriptions/{job_id}`).

---

## Webhooks

Set up a webhook in the web console (or via API), then verify signatures on
your end:

```python
from dalston_sdk import (
    verify_webhook_signature,
    parse_webhook_payload,
    WebhookEventType,
)

# In your HTTP handler:
def webhook_handler(headers, body):
    if not verify_webhook_signature(
        body=body,
        signature=headers["X-Dalston-Signature"],
        secret="whsec_...",                     # from the console
        timestamp=headers["X-Dalston-Timestamp"],
        max_age=300,                            # reject replays > 5 min old
    ):
        return 401, "invalid signature"

    payload = parse_webhook_payload(body)
    if payload.event == WebhookEventType.JOB_COMPLETED:
        print(f"Job {payload.job_id} done — text: {payload.data.transcript.text}")
    return 200, "ok"
```

FastAPI shortcut:

```python
from fastapi import Depends, FastAPI
from dalston_sdk import fastapi_webhook_dependency, WebhookPayload

verify = fastapi_webhook_dependency(secret="whsec_...", max_age=300)

app = FastAPI()

@app.post("/webhooks/dalston")
async def handle(payload: WebhookPayload = Depends(verify)):
    print(payload.event, payload.job_id)
```

---

## Recipes

### Wait with a timeout

```python
job = client.transcribe("long.mp3")
try:
    job = client.wait_for_completion(job.id, timeout=600)  # seconds
except TimeoutError:
    client.cancel(job.id)
```

### Stream-friendly polling

```python
job = client.transcribe("file.mp3")
while True:
    job = client.get_job(job.id)
    if job.status in ("completed", "failed", "cancelled"):
        break
    time.sleep(2)
```

### Bulk transcribe a folder

```python
from pathlib import Path
import concurrent.futures as cf

def one(path):
    j = client.transcribe(path, model="nemo", language="en")
    j = client.wait_for_completion(j.id)
    return path, j.transcript.text

paths = list(Path("audio").glob("*.mp3"))
with cf.ThreadPoolExecutor(max_workers=4) as ex:
    for path, text in ex.map(one, paths):
        Path(f"out/{path.stem}.txt").write_text(text)
```

### Resume a long file across runs

```python
job = client.transcribe("big.mp3")
print(f"submitted: {job.id}")
# ...later, in a different process:
client = Dalston(base_url=..., api_key=...)
final = client.wait_for_completion("<the id you saved>")
```

---

## Errors you'll actually see

| Exception | When |
|---|---|
| `AuthenticationError` | Missing or invalid API key |
| `ForbiddenError` | API key lacks the required scope |
| `RateLimitError` | Per-key rate limit hit |
| `ValidationError` | Bad request (e.g. neither `file` nor `audio_url`) |
| `NotFoundError` | Job ID doesn't exist or you can't see it |
| `ServerError` | 5xx from the gateway |
| `ConnectError` | Network failure |
| `TimeoutException` | Request took too long |
| `RealtimeError` | The server emitted an `error` frame on a streaming session |
| `WebhookVerificationError` | Signature didn't verify |

All inherit from `DalstonError`.

---

## See also

- [01-quickstart.md](01-quickstart.md)
- [23-using-the-cli.md](23-using-the-cli.md) — same API, command-line shape
- [40-realtime-overview.md](40-realtime-overview.md) — pick the right protocol
- [`sdk/dalston_sdk/__init__.py`](../../sdk/dalston_sdk/__init__.py) — full export list
