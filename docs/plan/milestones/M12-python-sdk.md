# M12: Python SDK

| | |
|---|---|
| **Goal** | Provide a Python SDK for Dalston-native features not covered by ElevenLabs SDK |
| **Duration** | 3-4 days |
| **Dependencies** | M5 (webhooks), M6 (real-time), M8 (ElevenLabs compat) |
| **Deliverable** | `dalston-sdk` PyPI package with batch, real-time, and webhook verification |
| **Status** | Completed (February 2026) |

## User Story

> *"As a Python developer, I can use the ElevenLabs SDK for basic transcription, but I need a Dalston SDK for native features like per-channel speaker detection, binary WebSocket streaming, and webhook verification."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SDK POSITIONING                                   │
│                                                                             │
│  ┌─────────────────────────────────┐   ┌─────────────────────────────────┐  │
│  │      ElevenLabs SDK             │   │      Dalston SDK                │  │
│  │      ──────────────             │   │      ──────────────             │  │
│  │  • Basic transcription          │   │  • Dalston-native batch API     │  │
│  │  • ElevenLabs-compat endpoints  │   │  • Binary WebSocket (no base64) │  │
│  │  • base_url="http://dalston"    │   │  • Webhook verification         │  │
│  │                                 │   │  • Progress tracking            │  │
│  │  ✅ Use for ElevenLabs compat   │   │  • Export formats               │  │
│  │     when migrating existing     │   │  • Hybrid mode (enhance_on_end) │  │
│  │     ElevenLabs integrations     │   │                                 │  │
│  │                                 │   │  ✅ Use for Dalston-native      │  │
│  │                                 │   │     features and best perf      │  │
│  └─────────────────────────────────┘   └─────────────────────────────────┘  │
│                                                                             │
│  Can use both SDKs together — ElevenLabs for compat, Dalston for advanced   │
└─────────────────────────────────────────────────────────────────────────────┘

SDK Components:
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  dalston-sdk                                                                │
│  ├── dalston/                                                               │
│  │   ├── __init__.py          # Dalston, AsyncDalston exports               │
│  │   ├── client.py            # Batch transcription client                  │
│  │   ├── realtime.py          # WebSocket streaming client                  │
│  │   ├── webhook.py           # Webhook verification utilities              │
│  │   └── types.py             # Type definitions                            │
│  ├── pyproject.toml                                                         │
│  └── README.md                                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Features Comparison

| Feature | ElevenLabs SDK | Dalston SDK |
|---------|----------------|-------------|
| Basic transcription | ✅ | ✅ |
| Custom base_url | ✅ | ✅ |
| Speaker detection: diarize | ✅ | ✅ |
| Speaker detection: per_channel | ❌ | ✅ |
| Timestamp granularity control | ❌ | ✅ |
| Progress tracking (% + stage) | ❌ | ✅ |
| Export to SRT/VTT/TXT | ❌ | ✅ |
| Webhook signature verification | ❌ | ✅ |
| Real-time: base64 JSON | ✅ | ❌ |
| Real-time: binary frames | ❌ | ✅ (33% less bandwidth) |
| Real-time: VAD events | ❌ | ✅ |
| Real-time: session metadata | ❌ | ✅ |
| Hybrid mode (enhance_on_end) | ❌ | ✅ |
| System status endpoints | ❌ | ✅ |

---

## Steps

### 12.1: Package Structure

**Deliverables:**

- Create `sdk/` directory in repository root (separate from main dalston package)
- Set up `pyproject.toml` with:
  - Package name: `dalston-sdk`
  - Dependencies: `httpx>=0.27`, `websockets>=12.0`
  - Optional: `[dev]` extras for testing
- Create `py.typed` marker for PEP 561 type hints
- Minimal `README.md` with quick start example

---

### 12.2: Type Definitions

**Deliverables:**

- `types.py` with all Dalston-native types as dataclasses
- Enums: `JobStatus`, `SpeakerDetection`, `TimestampGranularity`, `AudioEncoding`, `ExportFormat`
- Batch types: `Job`, `JobList`, `Transcript`, `Word`, `Segment`, `Speaker`
- Real-time types: `SessionBegin`, `TranscriptPartial`, `TranscriptFinal`, `VADEvent`, `SessionEnd`
- System types: `RealtimeStatus`, `WorkerStatus`
- Webhook types: `WebhookPayload`

---

### 12.3: Batch Client

**Deliverables:**

- `Dalston` sync client class with:
  - `transcribe()` — Submit file or URL with Dalston-native parameters
  - `get_job()` — Get job status with progress tracking
  - `list_jobs()` — Paginated job listing with status filter
  - `cancel_job()` — Cancel pending/running job
  - `wait_for_completion()` — Poll until done with timeout
  - `export()` — Download transcript in SRT/VTT/TXT/JSON
  - `get_realtime_status()` — System capacity info
  - `list_workers()` — Real-time worker status
  - `health()` — Server health check

- `AsyncDalston` async client with same methods

---

### 12.4: Real-time WebSocket Client

**Deliverables:**

- `RealtimeSession` class with binary protocol:
  - `connect()` — Establish WebSocket with config params
  - `send_audio()` — Send raw PCM bytes (no base64 encoding)
  - `flush()` — Force processing of buffered audio
  - `close()` — Graceful session end
  - Async iterator for receiving messages

- Event callbacks:
  - `on_session_begin` — Session established with config
  - `on_partial` — Interim transcription
  - `on_final` — Final utterance with optional words
  - `on_vad_start` / `on_vad_end` — Voice activity events
  - `on_session_end` — Session complete with full transcript
  - `on_error` — Error notification

- `AsyncRealtimeSession` async variant

---

### 12.5: Webhook Verification

**Deliverables:**

- `verify_webhook_signature()` function:
  - Input: payload bytes, signature header, timestamp header, secret
  - Returns: bool (valid/invalid)
  - Handles timing-safe comparison
  - Validates timestamp freshness (configurable tolerance)

- `parse_webhook_payload()` function:
  - Input: raw JSON bytes
  - Returns: `WebhookPayload` dataclass

- Framework integrations (examples in docs):
  - FastAPI dependency
  - Flask decorator
  - Django middleware

---

### 12.6: Documentation

**Deliverables:**

- README with:
  - Installation instructions
  - Quick start for batch transcription
  - Quick start for real-time streaming
  - Webhook verification example
  - Link to full API docs

- Docstrings on all public methods
- Type hints on all public APIs

---

## API Reference

### Batch Client

```python
from dalston import Dalston, SpeakerDetection, ExportFormat

# Initialize
client = Dalston(
    base_url="http://localhost:8000",
    api_key="dk_xxx",  # Optional
    timeout=120.0,
)

# Submit transcription
job = client.transcribe(
    file="audio.mp3",  # or file=open(...), or audio_url="https://..."
    language="auto",
    speaker_detection=SpeakerDetection.DIARIZE,  # or "per_channel"
    num_speakers=2,
    timestamps_granularity="word",  # "none", "segment", "word"
    webhook_url="https://example.com/webhook",
    webhook_metadata={"user_id": "123"},
)

# Poll with progress
job = client.get_job(job.id)
print(f"Progress: {job.progress}% - Stage: {job.current_stage}")

# Wait for completion
result = client.wait_for_completion(job.id, timeout=300)
print(result.transcript.text)

# Export
srt = client.export(job.id, format=ExportFormat.SRT)
```

### Real-time Client

```python
from dalston import RealtimeSession, AudioEncoding

# Callback-based
session = RealtimeSession(
    base_url="ws://localhost:8000",
    api_key="dk_xxx",
    language="en",
    model="fast",
    encoding=AudioEncoding.PCM_S16LE,
    sample_rate=16000,
    enable_vad=True,
    enhance_on_end=True,  # Trigger batch enhancement
)

@session.on_partial
def handle_partial(text, start, end):
    print(f"[partial] {text}")

@session.on_final
def handle_final(text, start, end, confidence, words):
    print(f"[final] {text} ({confidence:.2f})")

@session.on_vad_start
def handle_vad_start(timestamp):
    print(f"Speech started at {timestamp}")

@session.on_session_end
def handle_session_end(session_id, transcript, enhancement_job_id):
    print(f"Full transcript: {transcript}")
    if enhancement_job_id:
        print(f"Enhancement job: {enhancement_job_id}")

# Stream audio
with session:
    with open("audio.pcm", "rb") as f:
        while chunk := f.read(3200):  # 100ms at 16kHz
            session.send_audio(chunk)
    session.flush()

# Async variant
async with AsyncRealtimeSession(...) as session:
    async for message in session:
        match message:
            case TranscriptFinal(text=text):
                print(text)
```

### Webhook Verification

```python
from dalston.webhook import verify_webhook_signature, parse_webhook_payload

# In your webhook handler
def handle_webhook(request):
    payload = request.body
    signature = request.headers["X-Dalston-Signature"]
    timestamp = request.headers["X-Dalston-Timestamp"]

    if not verify_webhook_signature(
        payload=payload,
        signature=signature,
        timestamp=timestamp,
        secret="your_webhook_secret",
        max_age=300,  # Reject if older than 5 minutes
    ):
        return Response(status=401)

    event = parse_webhook_payload(payload)
    print(f"Job {event.transcription_id} {event.status}")
```

---

## Verification

### Install and Test Batch

```bash
# Install from local
pip install -e ./sdk

# Test batch transcription
python -c "
from dalston import Dalston, SpeakerDetection

client = Dalston('http://localhost:8000')
job = client.transcribe(
    file='test.mp3',
    speaker_detection=SpeakerDetection.DIARIZE,
)
result = client.wait_for_completion(job.id)
print(result.transcript.text)
print(f'Speakers: {[s.id for s in result.transcript.speakers]}')
"

# Test export
python -c "
from dalston import Dalston, ExportFormat

client = Dalston('http://localhost:8000')
srt = client.export('JOB_ID', format=ExportFormat.SRT)
print(srt)
"
```

### Test Real-time

```bash
python -c "
from dalston import RealtimeSession

session = RealtimeSession(
    'ws://localhost:8000',
    language='en',
    model='fast',
)

@session.on_final
def on_final(text, *_):
    print(f'Final: {text}')

with session:
    # Stream 16kHz PCM audio
    with open('audio.pcm', 'rb') as f:
        while chunk := f.read(3200):
            session.send_audio(chunk)
    session.flush()
"
```

### Test Webhook Verification

```bash
# Generate test signature
python -c "
import hmac, hashlib, time, json

secret = 'test_secret'
payload = json.dumps({'event': 'transcription.completed', 'transcription_id': 'abc'})
timestamp = str(int(time.time()))
signature = 'sha256=' + hmac.new(
    secret.encode(),
    f'{timestamp}.{payload}'.encode(),
    hashlib.sha256
).hexdigest()

print(f'Timestamp: {timestamp}')
print(f'Signature: {signature}')
print(f'Payload: {payload}')
"

# Verify
python -c "
from dalston.webhook import verify_webhook_signature

valid = verify_webhook_signature(
    payload=b'{\"event\": \"transcription.completed\", \"transcription_id\": \"abc\"}',
    signature='sha256=...',  # from above
    timestamp='...',  # from above
    secret='test_secret',
)
print(f'Valid: {valid}')
"
```

---

## Checkpoint

- [ ] **Package structure** with pyproject.toml, py.typed
- [ ] **Type definitions** for all Dalston-native types
- [ ] **Batch client** with transcribe, get_job, wait_for_completion, export
- [ ] **Async batch client** mirror of sync client
- [ ] **Real-time client** with binary WebSocket, VAD events, hybrid mode
- [ ] **Async real-time client** with async iterator
- [ ] **Webhook verification** with timing-safe comparison
- [ ] **README** with quick start examples
- [ ] **Tests** for all public APIs

---

## Future Enhancements

1. **Streaming file upload** — Upload large files in chunks
2. **Automatic reconnection** — Real-time session recovery on disconnect
3. **Batch processing helper** — Process multiple files with progress bar
4. **CLI wrapper** — `dalston transcribe audio.mp3 --output transcript.srt`
5. **OpenTelemetry integration** — Tracing for debugging
