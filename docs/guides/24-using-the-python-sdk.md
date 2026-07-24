# Using the Python SDK

> Use `Dalston` or `AsyncDalston` for batch and control-plane operations.
> Use `RealtimeSession` or `AsyncRealtimeSession` for streaming. Webhook
> helpers verify and parse Standard Webhooks deliveries.

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

In distributed mode, `transcribe()` normally returns a pending `Job`;
`wait_for_completion()` polls until done (or raises if the job fails). A
transient Lite request can complete inline. All the knobs:

```python
job = client.transcribe(
    file="meeting.mp3",                    # or audio_url="https://..."
    model="auto",                          # model registry ID or "auto"
    language="auto",
    vocabulary=["PostgreSQL", "Kubernetes"],
    speaker_detection="diarize",           # "none", "diarize", "per_channel"
    num_speakers=2,                        # exact (overrides min/max)
    min_speakers=2,
    max_speakers=4,
    timestamps_granularity="word",         # "none", "segment", "word"
    pii_detection=True,
    pii_entity_types=["ssn", "credit_card_number"],
    redact_pii_audio=True,
    pii_redaction_mode="beep",
    retention=30,                          # days; 0 = transient, -1 = permanent
    lite_profile="compliance",              # ignored by distributed mode
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

### Optional output semantics

Treat language, timestamps, and confidence as capability-dependent:

- `job.transcript.language_code` can be `None` or `und`.
- `word.confidence` can be `None`.
- `segments`, `words`, and `speakers` can be absent.
- Requesting word timestamps does not guarantee them if the selected engine
  lacks the capability.

The native HTTP response also carries warnings and original-audio metadata that
the current typed SDK does not expose on every dataclass. Use the REST response
directly if those fields are required for policy decisions, and test the
selected engine rather than assuming a field exists.

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

## Batch and control-plane methods

```python
client.list_jobs(status="completed", limit=50)
job = client.get_job(job_id)
client.cancel(job_id)
client.list_engines()
client.list_models()
client.get_model(model_id)
client.get_realtime_status()
client.create_session_token()
client.list_realtime_sessions()
client.get_realtime_session(session_id)
client.delete_realtime_session(session_id)
```

The sync and async clients expose matching methods:

| Area | Methods |
| --- | --- |
| Jobs | `transcribe`, `get_job`, `list_jobs`, `cancel`, `wait_for_completion`, `export` |
| Models | `list_models`, `get_model` |
| Engines | `list_engines` |
| Realtime control plane | `get_realtime_status`, `create_session_token`, `list_realtime_sessions`, `get_realtime_session`, `delete_realtime_session` |
| Service | `health`, `close` |

The package still defines `get_job_artifacts` and `get_session_artifacts`, but
they target legacy `/v2` routes that the current gateway does not mount. Do not
use them as supported control-plane operations until the routes or SDK are
reconciled. Use the native job task-artifact endpoints instead.

Model pull/remove/sync, webhook endpoint administration, audit queries, job
rename/deletion, retained audio download, and task artifacts remain
REST/console operations in the current SDK.

Full job deletion is available through the REST API
(`DELETE /v1/audio/transcriptions/{job_id}`) and the web console.

---

## Webhooks

Set up a webhook in the web console (or via API), then verify signatures on
your end:

```python
from dalston_sdk import (
    verify_webhook_signature,
    parse_webhook_payload,
    WebhookEventType,
    WebhookVerificationError,
)

# In your HTTP handler:
def webhook_handler(headers, body):
    try:
        valid = verify_webhook_signature(
            payload=body,
            signature=headers["webhook-signature"],
            msg_id=headers["webhook-id"],
            timestamp=headers["webhook-timestamp"],
            secret="whsec_...",                 # from the console
            max_age=300,                        # reject replays > 5 min old
        )
    except WebhookVerificationError:
        return 401, "invalid signature"
    if not valid:
        return 401, "invalid signature"

    payload = parse_webhook_payload(body)
    if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        print(f"Transcription {payload.transcription_id} completed")
        print(payload.data)
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
    print(payload.type, payload.transcription_id)
```

---

## Recipes

### Wait with a timeout

```python
job = client.transcribe("long.mp3")
try:
    job = client.wait_for_completion(job.id, timeout=600)  # seconds
except TimeoutException:
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
| `RealtimeException` | The server emitted an `error` frame on a streaming session |
| `WebhookVerificationError` | Signature didn't verify |

The exception types above inherit from `DalstonError`. `RealtimeError` is the
typed payload carried by a realtime error message, not an exception class.

---

## See also

- [01-quickstart.md](01-quickstart.md)
- [23-using-the-cli.md](23-using-the-cli.md) — same API, command-line shape
- [40-realtime-overview.md](40-realtime-overview.md) — pick the right protocol
- [`sdk/dalston_sdk/__init__.py`](../../sdk/dalston_sdk/__init__.py) — full export list
