"""Type definitions for the Dalston SDK.

All types use dataclasses for simplicity and automatic __eq__, __repr__.
Enums inherit from str for JSON serialization compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class JobStatus(str, Enum):
    """Job processing status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
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


class TimestampGranularity(str, Enum):
    """Timestamp granularity level.

    - NONE: No timestamps
    - SEGMENT: Timestamps per segment only
    - WORD: Word-level timestamps (default)
    """

    NONE = "none"
    SEGMENT = "segment"
    WORD = "word"


class ExportFormat(str, Enum):
    """Transcript export formats."""

    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"
    JSON = "json"


# -----------------------------------------------------------------------------
# Transcript Data Types
# -----------------------------------------------------------------------------


@dataclass
class Word:
    """A single word with timing and optional speaker."""

    text: str
    start: float
    end: float
    confidence: float | None = None
    speaker_id: str | None = None


@dataclass
class Segment:
    """A transcript segment (sentence or phrase)."""

    id: int
    text: str
    start: float
    end: float
    speaker_id: str | None = None
    words: list[Word] | None = None


@dataclass
class Speaker:
    """Speaker information from diarization."""

    id: str
    label: str | None = None
    total_duration: float | None = None


@dataclass
class Transcript:
    """Complete transcript with all data."""

    text: str
    language_code: str | None = None
    words: list[Word] | None = None
    segments: list[Segment] | None = None
    speakers: list[Speaker] | None = None
    metadata: dict[str, Any] | None = None


# -----------------------------------------------------------------------------
# Job Types
# -----------------------------------------------------------------------------


@dataclass
class Job:
    """Transcription job with status and results.

    The progress and current_stage fields are Dalston-specific
    and provide detailed progress tracking during processing.
    """

    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    progress: int | None = None  # 0-100, Dalston-specific
    current_stage: str | None = None  # e.g., "transcribe", "diarize"
    transcript: Transcript | None = None


@dataclass
class JobSummary:
    """Summary of a job for list responses."""

    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: int | None = None


@dataclass
class JobList:
    """Paginated list of jobs."""

    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int


# -----------------------------------------------------------------------------
# Real-time Types
# -----------------------------------------------------------------------------


class RealtimeMessageType(str, Enum):
    """Real-time WebSocket message types."""

    # Session lifecycle
    SESSION_BEGIN = "session.begin"
    SESSION_END = "session.end"

    # Transcripts
    TRANSCRIPT_PARTIAL = "transcript.partial"
    TRANSCRIPT_FINAL = "transcript.final"

    # VAD events
    VAD_SPEECH_START = "vad.speech_start"
    VAD_SPEECH_END = "vad.speech_end"

    # Control messages (client -> server)
    FLUSH = "flush"
    END = "end"

    # Errors
    ERROR = "error"


@dataclass
class SessionBegin:
    """Session initialization message from server."""

    session_id: str
    model: str
    language: str
    sample_rate: int
    encoding: str


@dataclass
class SessionEnd:
    """Session end message from server."""

    session_id: str
    total_audio_seconds: float
    total_billed_seconds: float | None = None


@dataclass
class TranscriptPartial:
    """Partial (interim) transcript result."""

    text: str
    is_final: bool = False


@dataclass
class TranscriptFinal:
    """Final transcript result for an utterance."""

    text: str
    start: float
    end: float
    words: list[Word] | None = None
    confidence: float | None = None
    speaker_id: str | None = None


@dataclass
class VADEvent:
    """Voice Activity Detection event."""

    type: str  # "speech_start" or "speech_end"
    timestamp: float


@dataclass
class RealtimeError:
    """Error message from real-time session."""

    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass
class RealtimeMessage:
    """Union type for all real-time messages."""

    type: RealtimeMessageType
    data: (
        SessionBegin
        | SessionEnd
        | TranscriptPartial
        | TranscriptFinal
        | VADEvent
        | RealtimeError
    )


# -----------------------------------------------------------------------------
# Webhook Types
# -----------------------------------------------------------------------------


class WebhookEventType(str, Enum):
    """Webhook event types (Standard Webhooks format)."""

    TRANSCRIPTION_COMPLETED = "transcription.completed"
    TRANSCRIPTION_FAILED = "transcription.failed"
    TRANSCRIPTION_CANCELLED = "transcription.cancelled"


@dataclass
class WebhookPayload:
    """Webhook callback payload (Standard Webhooks format).

    See: https://github.com/standard-webhooks/standard-webhooks

    The payload follows the Standard Webhooks envelope:
    - object: Always "event"
    - id: Unique event ID (evt_...)
    - type: Event type string
    - created_at: Unix timestamp
    - data: Event-specific data
    """

    object: str  # Always "event"
    id: str  # Event ID (evt_...)
    type: WebhookEventType
    created_at: int  # Unix timestamp
    data: dict[str, Any]  # Contains transcription_id, status, etc.

    @property
    def transcription_id(self) -> str | None:
        """Get transcription_id from data."""
        return self.data.get("transcription_id")

    @property
    def status(self) -> str | None:
        """Get status from data."""
        return self.data.get("status")

    @property
    def webhook_metadata(self) -> dict[str, Any] | None:
        """Get echoed webhook_metadata from data."""
        return self.data.get("webhook_metadata")


# -----------------------------------------------------------------------------
# System Status Types
# -----------------------------------------------------------------------------


@dataclass
class HealthStatus:
    """Server health status."""

    status: str


@dataclass
class RealtimeStatus:
    """Real-time transcription system status."""

    status: str  # "ready", "at_capacity", "unavailable"
    total_capacity: int
    active_sessions: int
    available_capacity: int
    worker_count: int
    ready_workers: int


# -----------------------------------------------------------------------------
# Session Token Types
# -----------------------------------------------------------------------------


@dataclass
class SessionToken:
    """Ephemeral session token for client-side WebSocket auth.

    Session tokens are short-lived and designed for use in browser
    clients that need to connect to WebSocket endpoints without
    exposing long-lived API keys.
    """

    token: str
    expires_at: datetime
    scopes: list[str]
    tenant_id: UUID


# -----------------------------------------------------------------------------
# Model Types
# -----------------------------------------------------------------------------


@dataclass
class ModelCapabilities:
    """Model capabilities and features."""

    languages: int  # Number of supported languages (1 = English-only)
    streaming: bool  # Supports real-time streaming
    word_timestamps: bool  # Supports word-level timestamps


@dataclass
class Model:
    """Transcription model information."""

    id: str  # Model identifier (e.g., "whisper-large-v3")
    name: str  # Human-readable name
    description: str  # Brief description
    capabilities: ModelCapabilities
    tier: str  # "fast", "balanced", or "accurate"


@dataclass
class ModelList:
    """List of available models."""

    models: list[Model]
    aliases: dict[str, str]  # Alias mappings (e.g., "fast" -> "distil-whisper")
