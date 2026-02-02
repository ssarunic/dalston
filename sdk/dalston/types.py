"""Type definitions for the Dalston SDK.

All types use dataclasses for simplicity and automatic __eq__, __repr__.
Enums inherit from str for JSON serialization compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    data: SessionBegin | SessionEnd | TranscriptPartial | TranscriptFinal | VADEvent | RealtimeError


# -----------------------------------------------------------------------------
# Webhook Types
# -----------------------------------------------------------------------------


class WebhookEventType(str, Enum):
    """Webhook event types."""

    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"


@dataclass
class WebhookPayload:
    """Webhook callback payload."""

    event: WebhookEventType
    job_id: UUID
    timestamp: datetime
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None  # Echoed from job creation
