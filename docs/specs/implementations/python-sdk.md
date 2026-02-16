# Python SDK Implementation

Implementation patterns for the Dalston Python SDK (`dalston-sdk`).

## Package Layout

```
sdk/
├── dalston/
│   ├── __init__.py          # Public exports
│   ├── _version.py          # Version string
│   ├── client.py            # Dalston, AsyncDalston
│   ├── realtime.py          # RealtimeSession, AsyncRealtimeSession
│   ├── webhook.py           # verify_webhook_signature, parse_webhook_payload
│   └── types.py             # All type definitions
├── tests/
│   ├── test_client.py
│   ├── test_realtime.py
│   └── test_webhook.py
├── pyproject.toml
├── py.typed                  # PEP 561 marker
└── README.md
```

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "dalston-sdk"
version = "0.1.0"
description = "Python SDK for Dalston transcription server"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Multimedia :: Sound/Audio :: Speech",
]
dependencies = [
    "httpx>=0.27.0",
    "websockets>=12.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-httpx>=0.30",
    "ruff>=0.3",
    "mypy>=1.9",
]

[tool.hatch.build.targets.wheel]
packages = ["dalston"]

[tool.ruff]
target-version = "py310"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.10"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Type Definitions

All types use `@dataclass` for simplicity and automatic `__eq__`, `__repr__`. Enums inherit from `str` for JSON serialization compatibility.

```python
# dalston/types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SpeakerDetection(str, Enum):
    """Speaker detection mode.

    - NONE: No speaker identification
    - DIARIZE: Use diarization to identify speakers
    - PER_CHANNEL: Treat each audio channel as a separate speaker
    """
    NONE = "none"
    DIARIZE = "diarize"
    PER_CHANNEL = "per_channel"


class PIIDetectionTier(str, Enum):
    """PII detection thoroughness level.

    - FAST: Regex-only detection (fastest, lower recall)
    - STANDARD: Regex + GLiNER ML model (balanced)
    - THOROUGH: Regex + GLiNER + LLM verification (highest accuracy, slowest)
    """
    FAST = "fast"
    STANDARD = "standard"
    THOROUGH = "thorough"


class PIIRedactionMode(str, Enum):
    """Audio redaction mode for detected PII.

    - SILENCE: Replace PII audio segments with silence
    - BEEP: Replace PII audio segments with a beep tone
    """
    SILENCE = "silence"
    BEEP = "beep"


@dataclass
class Word:
    """A single word with timing and optional speaker."""
    text: str
    start: float
    end: float
    confidence: float | None = None
    speaker_id: str | None = None


@dataclass
class Job:
    """Transcription job with status and results.

    The `progress` and `current_stage` fields are Dalston-specific.
    """
    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    progress: int | None = None        # 0-100, Dalston-specific
    current_stage: str | None = None   # e.g., "transcribe", "diarize"
    transcript: Transcript | None = None
```

## Batch Client Pattern

Use httpx for both sync and async HTTP:

```python
# dalston/client.py
class Dalston:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def transcribe(
        self,
        file: str | Path | BinaryIO | None = None,
        audio_url: str | None = None,
        language: str = "auto",
        speaker_detection: SpeakerDetection | str = SpeakerDetection.NONE,
        num_speakers: int | None = None,
        timestamps_granularity: TimestampGranularity | str = TimestampGranularity.WORD,
        webhook_url: str | None = None,
        webhook_metadata: dict | None = None,
        # PII detection parameters
        pii_detection: bool = False,
        pii_detection_tier: PIIDetectionTier | str | None = None,
        pii_entity_types: list[str] | None = None,
        redact_pii_audio: bool = False,
        pii_redaction_mode: PIIRedactionMode | str | None = None,
    ) -> Job:
        """Submit audio for transcription.

        PII Detection:
            pii_detection: Enable PII detection in transcript
            pii_detection_tier: Detection thoroughness (fast, standard, thorough)
            pii_entity_types: Entity types to detect (e.g., ["ssn", "credit_card_number"])
            redact_pii_audio: Generate redacted audio file
            pii_redaction_mode: Audio redaction mode (silence or beep)
        """
        # Build multipart form, handle file upload
        # POST to /v1/audio/transcriptions
        ...

    def wait_for_completion(
        self,
        job_id: UUID | str,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        on_progress: Callable[[int, str | None], None] | None = None,
    ) -> Job:
        """Wait for job completion with optional progress callback."""
        ...
```

## Real-time WebSocket Pattern

Use websockets library with binary protocol for efficiency:

```python
# dalston/realtime.py
class AsyncRealtimeSession:
    """Async WebSocket client for real-time transcription.

    Uses binary protocol (no base64 encoding).
    """

    async def connect(self) -> SessionBegin:
        """Establish WebSocket with config params."""
        url = self._build_url()  # includes language, model, encoding, etc.
        self._ws = await websockets.connect(url)
        # Wait for session.begin message
        ...

    async def send_audio(self, audio: bytes) -> None:
        """Send raw PCM bytes directly (no base64)."""
        await self._ws.send(audio)

    async def flush(self) -> None:
        """Force processing of buffered audio."""
        await self._ws.send(json.dumps({"type": "flush"}))

    async def close(self) -> SessionEnd | None:
        """Gracefully close, wait for session.end."""
        await self._ws.send(json.dumps({"type": "end"}))
        # Wait for session.end message
        ...

    async def __aiter__(self) -> AsyncIterator[RealtimeMessage]:
        """Iterate over incoming messages."""
        async for raw in self._ws:
            yield self._parse_message(json.loads(raw))
```

### Sync Wrapper with Callbacks

```python
class RealtimeSession:
    """Sync wrapper using decorator-based callbacks."""

    def __init__(self, *args, **kwargs):
        self._async_session = AsyncRealtimeSession(*args, **kwargs)
        self._callbacks = defaultdict(list)

    def on_final(self, fn: Callable) -> Callable:
        """Register callback for transcript.final."""
        self._callbacks["final"].append(fn)
        return fn

    def on_vad_start(self, fn: Callable) -> Callable:
        """Register callback for vad.speech_start."""
        self._callbacks["vad_start"].append(fn)
        return fn

    # Runs async session in background thread, dispatches to callbacks
```

## Webhook Verification Pattern

Timing-safe comparison prevents timing attacks:

```python
# dalston/webhook.py
def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    secret: str,
    max_age: int = 300,
) -> bool:
    """Verify Dalston webhook signature.

    Args:
        payload: Raw request body bytes
        signature: X-Dalston-Signature header ("sha256=...")
        timestamp: X-Dalston-Timestamp header
        secret: Webhook secret from Dalston config
        max_age: Maximum age in seconds (default 5 minutes)

    Returns:
        True if signature is valid
    """
    # Validate timestamp freshness
    ts = int(timestamp)
    if abs(time.time() - ts) > max_age:
        raise WebhookVerificationError("Timestamp too old")

    # Compute expected signature
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected = "sha256=" + hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    # Timing-safe comparison
    return hmac.compare_digest(expected, signature)
```

### Framework Helpers

```python
def fastapi_webhook_dependency(secret: str):
    """FastAPI dependency for webhook verification."""
    from fastapi import Depends, HTTPException, Request

    async def verify(request: Request) -> WebhookPayload:
        body = await request.body()
        signature = request.headers.get("X-Dalston-Signature", "")
        timestamp = request.headers.get("X-Dalston-Timestamp", "")

        if not verify_webhook_signature(body, signature, timestamp, secret):
            raise HTTPException(401, "Invalid signature")

        return parse_webhook_payload(body)

    return verify
```

## Error Handling

Consistent exception hierarchy with HTTP status codes:

```python
class DalstonError(Exception):
    """Base exception for all SDK errors."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(DalstonError):
    """API key invalid or missing (401)."""

class NotFoundError(DalstonError):
    """Resource not found (404)."""

class RateLimitError(DalstonError):
    """Rate limit exceeded (429)."""
    retry_after: int | None

class ServerError(DalstonError):
    """Server-side error (5xx)."""
```

## Testing Approach

- **pytest-httpx** for mocking HTTP requests
- **pytest-asyncio** for async tests
- **No real server needed** for unit tests

```python
def test_transcribe_with_per_channel(client, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/audio/transcriptions",
        json={"id": "...", "status": "pending", "created_at": "..."},
    )

    job = client.transcribe(
        file="test.mp3",
        speaker_detection=SpeakerDetection.PER_CHANNEL,
    )

    request = httpx_mock.get_request()
    assert b"per_channel" in request.content
```

## Design Decisions

### Why separate SDK instead of extending ElevenLabs SDK?

1. **Different type contracts** — Dalston returns `progress`, `current_stage`, different speaker formats
2. **Binary WebSocket protocol** — ElevenLabs uses base64 JSON, Dalston uses raw binary
3. **Additional features** — VAD events, session metadata, hybrid mode not in ElevenLabs
4. **Cleaner API** — Purpose-built for Dalston, not adapter pattern

### Why httpx over requests?

1. **Async support** — Same API for sync and async
2. **Modern** — HTTP/2, timeouts, connection pooling built-in
3. **Type hints** — Better IDE support

### Why websockets over websocket-client?

1. **Async-first** — Native asyncio support
2. **Binary frames** — Clean handling of binary messages
3. **Well-maintained** — Active development, good docs

---

## Implementation Status

**Status:** Complete
**Location:** `/sdk/`
**Tests:** 43 passing

### What Was Built

All spec features implemented:

- `Dalston` and `AsyncDalston` batch clients
- `RealtimeSession` and `AsyncRealtimeSession` with callbacks
- Webhook verification with FastAPI/Flask helpers
- All types as dataclasses
- Full test suite

### Additions (beyond spec)

- `exceptions.py` — Separated into own module
- `PermissionError` — Added 403 handling
- `flask_verify_webhook()` — Flask decorator helper
- Expanded README with parameter tables
- `health()` method — Check server health
- `get_realtime_status()` method — Get real-time capacity info
- `HealthStatus` and `RealtimeStatus` types

### Package Rename

The Python package was renamed from `dalston` to `dalston_sdk` to avoid import conflicts with the `dalston/` server package in the repo root. Users import as `from dalston_sdk import Dalston`. The PyPI distribution name remains `dalston-sdk`.

### PII Detection (M26)

Added PII detection and audio redaction parameters to `transcribe()`:

- `pii_detection` — Enable PII detection in transcript
- `pii_detection_tier` — Detection thoroughness (fast, standard, thorough)
- `pii_entity_types` — Specific entity types to detect
- `redact_pii_audio` — Generate redacted audio file
- `pii_redaction_mode` — Audio redaction mode (silence or beep)

New types:

- `PIIDetectionTier` — Enum for detection tier
- `PIIRedactionMode` — Enum for audio redaction mode

### Session Management (M24)

Added realtime session management methods to both `Dalston` and `AsyncDalston`:

- `list_realtime_sessions(limit, status)` — List realtime sessions with optional filtering
- `get_realtime_session(session_id)` — Get detailed session information
- `delete_realtime_session(session_id)` — Delete a non-active session

New types added:

- `RealtimeSessionStatus` — Enum for session status (active, completed, error, interrupted)
- `RealtimeSessionInfo` — Dataclass with full session metadata
- `RealtimeSessionList` — Paginated list response

### Audio URL Support

The `audio_url` parameter now supports:

- Direct HTTPS URLs (public or presigned)
- S3 presigned URLs
- GCS presigned URLs
- Google Drive share links (auto-converted to direct download)
- Dropbox share links (auto-converted to direct download)

### Deferred

- Retry logic — Users handle `RateLimitError.retry_after` manually
- Connection pooling config — Using httpx defaults
