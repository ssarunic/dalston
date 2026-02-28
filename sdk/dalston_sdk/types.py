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


class PIIRedactionMode(str, Enum):
    """Audio redaction mode for detected PII.

    - SILENCE: Replace PII audio segments with silence
    - BEEP: Replace PII audio segments with a beep tone
    """

    SILENCE = "silence"
    BEEP = "beep"


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
class PIIEntity:
    """A detected PII entity in the transcript."""

    entity_type: str  # e.g., "name", "ssn", "credit_card_number"
    category: str  # e.g., "pii"
    start_offset: int  # Character offset in text
    end_offset: int
    start_time: float | None = None  # Audio timestamp
    end_time: float | None = None
    confidence: float | None = None
    speaker: str | None = None
    redacted_value: str | None = None  # e.g., "[NAME]"
    original_text: str | None = None  # The actual PII text


@dataclass
class PIIInfo:
    """Summary of PII detection results."""

    enabled: bool
    entities_detected: int = 0
    entity_summary: dict[str, int] | None = None  # e.g., {"name": 3, "ssn": 1}
    redacted_audio_available: bool = False


@dataclass
class Transcript:
    """Complete transcript with all data."""

    text: str
    language_code: str | None = None
    words: list[Word] | None = None
    segments: list[Segment] | None = None
    speakers: list[Speaker] | None = None
    metadata: dict[str, Any] | None = None
    # PII detection results
    redacted_text: str | None = None
    pii_entities: list[PIIEntity] | None = None
    pii_info: PIIInfo | None = None


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
    """Paginated list of jobs with cursor-based pagination."""

    jobs: list[JobSummary]
    cursor: str | None
    has_more: bool


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
    start: float = 0.0
    end: float = 0.0
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


class RealtimeSessionStatus(str, Enum):
    """Realtime session status."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class RealtimeSessionInfo:
    """Realtime transcription session information."""

    id: str
    status: RealtimeSessionStatus
    language: str | None
    model: str | None
    engine: str | None
    audio_duration_seconds: float
    segment_count: int
    word_count: int
    store_audio: bool
    store_transcript: bool
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None


@dataclass
class RealtimeSessionList:
    """Paginated list of realtime sessions with cursor-based pagination."""

    sessions: list[RealtimeSessionInfo]
    cursor: str | None
    has_more: bool


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

    languages: list[str] | None  # Supported language codes, None = all languages
    streaming: bool  # Supports real-time streaming
    word_timestamps: bool  # Supports word-level timestamps
    punctuation: bool = False  # M36: Outputs punctuation
    capitalization: bool = False  # M36: Outputs capitalization


@dataclass
class HardwareRequirements:
    """Engine hardware requirements."""

    gpu_required: bool = False
    supports_cpu: bool = True
    min_vram_gb: float | None = None
    min_ram_gb: float | None = None  # M36: Minimum RAM


@dataclass
class Model:
    """Transcription model information (M36: repurposed from engine info).

    Represents a model variant from the catalog that can be used with
    the `model` parameter in transcription requests.
    """

    id: str  # Model identifier (e.g., "faster-whisper-large-v3-turbo", "parakeet-tdt-1.1b")
    stage: str  # Pipeline stage (e.g., "transcribe")
    capabilities: ModelCapabilities
    hardware: HardwareRequirements | None = None
    # M36: New fields for runtime model management
    name: str | None = None  # Human-readable name
    runtime: str | None = (
        None  # Runtime that loads this model (e.g., "nemo", "faster-whisper")
    )
    runtime_model_id: str | None = None  # Native model ID for loading
    source: str | None = None  # Download source (e.g., HuggingFace model ID)
    size_gb: float | None = None  # Estimated model size


@dataclass
class ModelList:
    """List of available models from the catalog."""

    models: list[Model]


# -----------------------------------------------------------------------------
# Engine Types (M36)
# -----------------------------------------------------------------------------


@dataclass
class EngineCapabilities:
    """Engine capabilities in API response format."""

    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    supports_streaming: bool = False
    max_audio_duration_s: int | None = None
    max_concurrency: int | None = None


@dataclass
class EnginePerformance:
    """Engine performance characteristics."""

    rtf_gpu: float | None = None
    rtf_cpu: float | None = None


@dataclass
class Engine:
    """Running engine information (M36).

    Represents an engine runtime with its current status and loaded model.
    """

    id: str  # Engine/runtime identifier (e.g., "nemo", "faster-whisper")
    stage: str  # Pipeline stage (e.g., "transcribe", "diarize")
    version: str
    status: str  # "running" | "available" | "unhealthy"
    capabilities: EngineCapabilities
    name: str | None = None
    loaded_model: str | None = None  # Currently loaded runtime_model_id
    available_models: list[str] | None = None  # Models on disk, ready to load
    hardware: HardwareRequirements | None = None
    performance: EnginePerformance | None = None


@dataclass
class EngineList:
    """List of engines with status."""

    engines: list[Engine]
    total: int


# -----------------------------------------------------------------------------
# Retention V2 Types
# -----------------------------------------------------------------------------


class ArtifactType(str, Enum):
    """Standard artifact types for retention V2."""

    AUDIO_SOURCE = "audio.source"
    AUDIO_REDACTED = "audio.redacted"
    TRANSCRIPT_RAW = "transcript.raw"
    TRANSCRIPT_REDACTED = "transcript.redacted"
    PII_ENTITIES = "pii.entities"
    PIPELINE_INTERMEDIATE = "pipeline.intermediate"


@dataclass
class Artifact:
    """Persisted artifact with retention metadata."""

    id: str
    artifact_type: str
    uri: str
    sensitivity: str
    compliance_tags: list[str] | None
    store: bool
    ttl_seconds: int | None
    created_at: datetime
    available_at: datetime
    purge_after: datetime | None
    purged_at: datetime | None


@dataclass
class ArtifactList:
    """List of artifacts for an owner."""

    owner_type: str  # job | session
    owner_id: str
    artifacts: list[Artifact]
