"""Dalston SDK - Python client for Dalston transcription server.

This SDK provides both synchronous and asynchronous clients for:
- Batch transcription via REST API
- Real-time streaming transcription via WebSocket
- Webhook signature verification

Quick Start:
    ```python
    from dalston_sdk import Dalston

    # Batch transcription
    client = Dalston(base_url="http://localhost:8000")
    job = client.transcribe("audio.mp3")
    job = client.wait_for_completion(job.id)
    print(job.transcript.text)

    # Real-time streaming
    from dalston_sdk import AsyncRealtimeSession

    async with AsyncRealtimeSession() as session:
        await session.connect()
        await session.send_audio(audio_bytes)
        async for msg in session:
            print(msg.data.text)
    ```
"""

from ._version import __version__

# Clients
from .client import AsyncDalston, Dalston

# Exceptions
from .exceptions import (
    AuthenticationError,
    ConnectError,
    ConnectionError,  # Deprecated alias for ConnectError
    DalstonError,
    ForbiddenError,
    NotFoundError,
    PermissionError,  # Deprecated alias for ForbiddenError
    RateLimitError,
    ServerError,
    TimeoutError,  # Deprecated alias for TimeoutException
    TimeoutException,
    ValidationError,
    WebhookVerificationError,
)
from .exceptions import (
    RealtimeError as RealtimeException,
)

# Real-time
from .realtime import AsyncRealtimeSession, RealtimeSession

# Types
from .types import (
    ExportFormat,
    HealthStatus,
    Job,
    JobList,
    JobStatus,
    JobSummary,
    Model,
    ModelCapabilities,
    ModelList,
    RealtimeError,
    RealtimeMessage,
    RealtimeMessageType,
    RealtimeStatus,
    Segment,
    SessionBegin,
    SessionEnd,
    SessionToken,
    Speaker,
    SpeakerDetection,
    TimestampGranularity,
    Transcript,
    TranscriptFinal,
    TranscriptPartial,
    VADEvent,
    WebhookEventType,
    WebhookPayload,
    Word,
)

# Webhook
from .webhook import (
    fastapi_webhook_dependency,
    flask_verify_webhook,
    parse_webhook_payload,
    verify_webhook_signature,
)

__all__ = [
    # Version
    "__version__",
    # Clients
    "Dalston",
    "AsyncDalston",
    "RealtimeSession",
    "AsyncRealtimeSession",
    # Webhook
    "verify_webhook_signature",
    "parse_webhook_payload",
    "fastapi_webhook_dependency",
    "flask_verify_webhook",
    # Enums
    "JobStatus",
    "SpeakerDetection",
    "TimestampGranularity",
    "ExportFormat",
    "RealtimeMessageType",
    "WebhookEventType",
    # Transcript types
    "Word",
    "Segment",
    "Speaker",
    "Transcript",
    # Job types
    "Job",
    "JobSummary",
    "JobList",
    # Model types
    "Model",
    "ModelCapabilities",
    "ModelList",
    # Real-time types
    "SessionBegin",
    "SessionEnd",
    "TranscriptPartial",
    "TranscriptFinal",
    "VADEvent",
    "RealtimeMessage",
    "RealtimeError",
    # System status types
    "HealthStatus",
    "RealtimeStatus",
    # Session token types
    "SessionToken",
    # Webhook types
    "WebhookPayload",
    # Exceptions (new names)
    "DalstonError",
    "AuthenticationError",
    "ForbiddenError",
    "NotFoundError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "ConnectError",
    "TimeoutException",
    "WebhookVerificationError",
    "RealtimeException",
    # Deprecated aliases (shadow builtins - avoid using)
    "PermissionError",
    "ConnectionError",
    "TimeoutError",
]
