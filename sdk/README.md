# Dalston SDK

Official Python SDK for [Dalston](https://github.com/ssarunic/dalston), a
self-hosted audio transcription server with real-time streaming support.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Installation

```bash
pip install dalston-sdk
```

For development:

```bash
cd sdk/
pip install -e ".[dev]"
```

## Features

- **Batch transcription** - Upload audio files for high-quality processing
- **Real-time streaming** - Get transcripts as you speak via WebSocket
- **Speaker diarization** - Identify who said what
- **Word-level timestamps** - Precise timing for every word
- **Multiple export formats** - SRT, VTT, TXT, JSON
- **Webhook support** - Get notified when jobs complete
- **Async support** - Both sync and async clients available

---

## Quick Start

The simplest way to transcribe an audio file:

```python
from dalston_sdk import Dalston

client = Dalston(base_url="http://localhost:8000")
job = client.transcribe("meeting.mp3")
job = client.wait_for_completion(job.id)
print(job.transcript.text)
```

---

## Batch Transcription

For pre-recorded audio files. The audio is processed server-side with high accuracy.

### Basic Usage

Submit an audio file and wait for the result:

```python
from dalston_sdk import Dalston

client = Dalston(base_url="http://localhost:8000")

# Submit audio for transcription
job = client.transcribe(
    file="audio.mp3",
    language="auto",  # Auto-detect language
)

# Poll until complete
job = client.wait_for_completion(job.id)

# Access the transcript
print(job.transcript.text)
```

### With Speaker Diarization

Identify different speakers in the audio. Useful for meetings, interviews, or conversations:

```python
job = client.transcribe(
    file="meeting.mp3",
    speaker_detection="diarize",
    num_speakers=3,  # Optional: hint for expected speakers
)

job = client.wait_for_completion(job.id)

# Access speaker-labeled segments
for segment in job.transcript.segments:
    print(f"{segment.speaker_id}: {segment.text}")
```

### Progress Tracking

Monitor transcription progress for longer files:

```python
def on_progress(progress: int, stage: str | None):
    print(f"Progress: {progress}% - Stage: {stage}")

job = client.wait_for_completion(
    job.id,
    poll_interval=2.0,  # Check every 2 seconds
    on_progress=on_progress,
)
```

### Async Client

For applications using asyncio:

```python
import asyncio
from dalston_sdk import AsyncDalston

async def transcribe_async():
    async with AsyncDalston(base_url="http://localhost:8000") as client:
        job = await client.transcribe(file="audio.mp3")
        job = await client.wait_for_completion(job.id)
        return job.transcript.text

text = asyncio.run(transcribe_async())
```

---

## Real-time Streaming

Get transcripts in real-time as audio is being recorded. Perfect for live captioning, voice assistants, or real-time note-taking.

### Async Streaming

The recommended approach for real-time transcription:

```python
import asyncio
from dalston_sdk import AsyncRealtimeSession

async def stream_microphone():
    session = AsyncRealtimeSession(
        base_url="ws://localhost:8000",
        language="en",
        # model omitted: route to any compatible ready worker
        sample_rate=16000,   # 16kHz audio
    )

    # Connect to server
    begin = await session.connect()
    print(f"Session started: {begin.session_id}")

    # Send audio chunks as they come in
    async for chunk in get_audio_chunks():  # Your audio source
        await session.send_audio(chunk)

    # Get final results and close
    end = await session.close()
    print(f"Processed {end.total_audio_seconds}s of audio")

asyncio.run(stream_microphone())
```

### Receiving Transcripts

Iterate over incoming messages to receive partial and final transcripts:

```python
async with AsyncRealtimeSession(base_url="ws://localhost:8000") as session:
    await session.connect()

    # Start sending audio in background task
    asyncio.create_task(send_audio(session))

    # Receive transcripts
    async for message in session:
        if message.type == RealtimeMessageType.TRANSCRIPT_PARTIAL:
            print(f"Partial: {message.data.text}", end="\r")
        elif message.type == RealtimeMessageType.TRANSCRIPT_FINAL:
            print(f"Final: {message.data.text}")
        elif message.type == RealtimeMessageType.VAD_SPEECH_START:
            print("* Speech detected")
```

### Callback-based (Sync)

For simpler integration with synchronous code, use decorator-based callbacks:

```python
from dalston_sdk import (
    RealtimeSession,
    TranscriptFinal,
    TranscriptPartial,
    VADEvent,
)

session = RealtimeSession(
    base_url="ws://localhost:8000",
    language="en",
)

@session.on_final
def handle_transcript(transcript: TranscriptFinal):
    print(f"Transcript: {transcript.text}")

@session.on_partial
def handle_partial(partial: TranscriptPartial):
    print(f"... {partial.text}", end="\r")

@session.on_vad_start
def handle_speech_start(event: VADEvent):
    print("Speech started")

# Connect and stream
session.connect()

for chunk in audio_stream:
    session.send_audio(chunk)

session.close()
```

---

## Export Formats

Export completed transcripts to popular subtitle and text formats:

```python
from dalston_sdk import Dalston, ExportFormat

client = Dalston(base_url="http://localhost:8000")

# SubRip subtitles (.srt)
srt = client.export(job_id, format=ExportFormat.SRT)

# WebVTT subtitles (.vtt)
vtt = client.export(job_id, format=ExportFormat.VTT)

# Plain text with speaker labels
txt = client.export(job_id, format="txt", include_speakers=True)

# Full JSON with all metadata
data = client.export(job_id, format="json")
```

---

## Webhooks

Get notified when transcription jobs complete by registering webhook endpoints
via the admin console.

### Verifying Webhook Signatures

Always verify webhook signatures to ensure requests are authentic:

```python
from dalston_sdk import (
    WebhookEventType,
    WebhookVerificationError,
    parse_webhook_payload,
    verify_webhook_signature,
)

def handle_webhook(request):
    try:
        valid = verify_webhook_signature(
            payload=request.body,
            signature=request.headers["webhook-signature"],
            msg_id=request.headers["webhook-id"],
            timestamp=request.headers["webhook-timestamp"],
            secret="whsec_...",
        )
    except WebhookVerificationError:
        return Response(status=401)
    if not valid:
        return Response(status=401)

    payload = parse_webhook_payload(request.body)
    if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        print(f"Transcription {payload.transcription_id} completed")
        print(payload.data)
```

### FastAPI Integration

Use the built-in dependency for automatic verification:

```python
from fastapi import FastAPI, Depends
from dalston_sdk import fastapi_webhook_dependency, WebhookPayload, WebhookEventType

app = FastAPI()
verify_webhook = fastapi_webhook_dependency("whsec_...")

@app.post("/webhooks/dalston")
async def handle_webhook(payload: WebhookPayload = Depends(verify_webhook)):
    if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        transcription_id = payload.transcription_id
```

---

## Configuration Reference

### Client Options

```python
from dalston_sdk import Dalston

client = Dalston(
    base_url="http://localhost:8000",  # Dalston server URL
    api_key="your-api-key",            # Optional API key
    timeout=120.0,                      # Request timeout in seconds
)
```

### Transcription Parameters

| Parameter | Type | Default | Description |
| --------- | ---- | ------- | ----------- |
| `file` | str/Path/BinaryIO | - | Audio file path or file object |
| `audio_url` | str | - | Remote audio URL instead of `file` |
| `model` | str | `"auto"` | Engine/model ID or automatic selection |
| `language` | str | `"auto"` | Language code (e.g., `"en"`, `"es"`) or `"auto"` |
| `vocabulary` | list[str] | None | Recognition hints |
| `speaker_detection` | str | `"none"` | `"none"`, `"diarize"`, or `"per_channel"` |
| `num_speakers` | int | None | Expected number of speakers (hint for diarization) |
| `min_speakers`, `max_speakers` | int | None | Diarization bounds |
| `timestamps_granularity` | str | `"word"` | `"none"`, `"segment"`, or `"word"` |
| `pii_detection` | bool | `False` | Detect PII |
| `pii_entity_types` | list[str] | None | Restrict PII entity types |
| `redact_pii_audio` | bool | `False` | Produce redacted audio |
| `pii_redaction_mode` | str | None | `silence` or `beep` |
| `retention` | int | server default | `0`, `-1`, or days |
| `lite_profile` | str | `"core"` | `core`, `speaker`, or `compliance` in lite mode |

### Real-time Session Parameters

| Parameter | Type | Default | Description |
| --------- | ---- | ------- | ----------- |
| `base_url` | str | - | WebSocket URL (`ws://` or `wss://`) |
| `language` | str | `"auto"` | Language code or `"auto"` |
| `model` | str | None | Registered model/engine preference; omit for automatic routing |
| `encoding` | str | `"pcm_s16le"` | Audio encoding format |
| `sample_rate` | int | `16000` | Audio sample rate in Hz |
| `enable_vad` | bool | `True` | Emit VAD events |
| `interim_results` | bool | `True` | Send partial transcripts |
| `word_timestamps` | bool | `False` | Include word-level timing |

---

## Control-plane coverage

`Dalston` and `AsyncDalston` expose matching sync/async methods:

| Area | Methods |
| --- | --- |
| Jobs | `transcribe`, `get_job`, `list_jobs`, `cancel`, `wait_for_completion`, `export` |
| Models/engines | `list_models`, `get_model`, `list_engines` |
| Realtime management | `get_realtime_status`, `create_session_token`, `list_realtime_sessions`, `get_realtime_session`, `delete_realtime_session` |
| Service | `health`, `close` |

The package still defines `get_job_artifacts` and `get_session_artifacts`, but
they target legacy `/v2` routes that the current gateway does not mount. Do not
treat them as supported operations until the gateway/SDK contract is
reconciled. Job task artifacts are available from the native REST API.

Job deletion/rename, model mutation, webhook administration, audit queries,
retained audio download, and task artifacts currently use the REST API or
console.

## Result semantics

Optional output follows the selected engine's capabilities:

- language may be absent or `und`;
- word confidence may be `None`;
- segments, words, speakers, and word timestamps may be absent;
- a word-timestamp request can fall back when an engine lacks that capability.

The native HTTP payload also includes warnings and original-audio metadata that
are not represented on every current SDK dataclass. Use the HTTP API directly
when an application must make decisions from those fields.

---

## Error Handling

The SDK provides typed exceptions for different error scenarios:

```python
from dalston_sdk import (
    Dalston,
    DalstonError,        # Base exception
    AuthenticationError, # Invalid/missing API key (401)
    NotFoundError,       # Resource not found (404)
    RateLimitError,      # Too many requests (429)
    ValidationError,     # Invalid parameters (400/422)
    ServerError,         # Server error (5xx)
    TimeoutException,    # Request timeout
    ConnectError,        # Network error
)

try:
    job = client.get_job("invalid-id")
except NotFoundError:
    print("Job not found")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after}s")
except DalstonError as e:
    print(f"Error ({e.status_code}): {e.message}")
```

---

## Type Reference

The SDK exports typed dataclasses for all API responses:

### Job Types

- `Job` - Full job with status and transcript
- `JobSummary` - Abbreviated job info for listings
- `JobList` - Paginated job list

### Transcript Types

- `Transcript` - Complete transcript with all data
- `Word` - Single word with timing
- `Segment` - Sentence/phrase with speaker
- `Speaker` - Speaker metadata

### Real-time Types

- `SessionBegin`, `SessionEnd` - Session lifecycle
- `TranscriptPartial`, `TranscriptFinal` - Streaming results
- `VADEvent` - Voice activity events

### Enums

- `JobStatus` - pending, running, completed, failed, cancelling, cancelled
- `SpeakerDetection` - none, diarize, per_channel
- `ExportFormat` - srt, vtt, txt, json

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy dalston_sdk/

# Linting
ruff check dalston_sdk/
```

## License

MIT
