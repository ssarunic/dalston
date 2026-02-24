from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Retention Policy Types (M25)
# =============================================================================


class RetentionMode(str, Enum):
    """Retention behavior mode for jobs and sessions."""

    AUTO_DELETE = "auto_delete"  # Delete after hours expires
    KEEP = "keep"  # Keep forever (purge_after stays NULL)
    NONE = "none"  # Delete immediately on completion


class RetentionScope(str, Enum):
    """What to delete when purging."""

    ALL = "all"  # Delete audio, task intermediates, and transcript
    AUDIO_ONLY = (
        "audio_only"  # Delete audio only, keep task intermediates and transcript
    )


def retention_to_ttl_seconds(retention_days: int) -> int | None:
    """Convert retention days to TTL in seconds.

    Args:
        retention_days: Number of days to retain, 0 for transient, -1 for permanent

    Returns:
        0 for transient (immediate purge after completion)
        None for permanent (never auto-delete)
        N*86400 for N days retention
    """
    if retention_days == 0:
        return 0
    if retention_days == -1:
        return None
    return retention_days * 86400


def validate_retention(retention_days: int) -> None:
    """Validate retention days value.

    Args:
        retention_days: Number of days (0=transient, -1=permanent, 1-3650=days)

    Raises:
        ValueError: Invalid retention value
    """
    if retention_days == 0 or retention_days == -1:
        return
    if retention_days < 1 or retention_days > 3650:
        raise ValueError(
            "Retention days must be 0 (transient), -1 (permanent), or 1-3650"
        )


def format_retention_display(retention_days: int) -> str:
    """Format retention days for human display.

    Args:
        retention_days: Number of days (0=transient, -1=permanent, N=days)

    Returns:
        Human-readable string: "Transient", "Permanent", "1 day", "30 days"
    """
    if retention_days == 0:
        return "Transient"
    if retention_days == -1:
        return "Permanent"
    if retention_days == 1:
        return "1 day"
    return f"{retention_days} days"


# Legacy function for backwards compatibility during migration
def parse_retention(value: str) -> int | None:
    """Parse legacy retention string to TTL in seconds.

    DEPRECATED: Use retention_to_ttl_seconds() with integer retention_days.
    """
    if value == "none":
        return 0
    if value == "forever":
        return None
    if value.endswith("d"):
        try:
            days = int(value[:-1])
        except ValueError as err:
            raise ValueError(f"Invalid retention format: {value}") from err
        if days < 1 or days > 3650:
            raise ValueError("Retention days must be between 1 and 3650")
        return days * 86400
    raise ValueError(
        f"Invalid retention format: {value}. "
        "Use 'none', 'forever', or '{N}d' (e.g., '30d')"
    )


RETENTION_DEFAULT = 30  # 30 days


# =============================================================================
# Retention V2 Types (Artifact-Centric Model)
# =============================================================================


class ArtifactType(str, Enum):
    """Standard artifact types for retention V2."""

    AUDIO_SOURCE = "audio.source"
    AUDIO_REDACTED = "audio.redacted"
    TRANSCRIPT_RAW = "transcript.raw"
    TRANSCRIPT_REDACTED = "transcript.redacted"
    PII_ENTITIES = "pii.entities"
    PIPELINE_INTERMEDIATE = "pipeline.intermediate"


class ArtifactSensitivity(str, Enum):
    """Sensitivity classification for artifacts."""

    RAW_PII = "raw_pii"  # Contains unredacted PII
    REDACTED = "redacted"  # PII has been redacted
    METADATA = "metadata"  # No PII content (e.g., stats, counts)


class ArtifactOwnerType(str, Enum):
    """Owner type for artifacts."""

    JOB = "job"
    SESSION = "session"


@dataclass
class RetentionRule:
    """Single artifact retention rule."""

    store: bool
    ttl_seconds: int | None = None  # null = keep forever, 0 = immediate purge


@dataclass
class ResolvedRetention:
    """Fully resolved retention configuration for all artifact types."""

    rules: dict[str, RetentionRule]
    template_id: UUID | None = None
    template_name: str | None = None


# =============================================================================
# PII Detection Types (M26)
# =============================================================================


class PIIDetectionTier(str, Enum):
    """PII detection tier controlling speed/accuracy tradeoff."""

    FAST = "fast"  # Presidio regex only (<5ms)
    STANDARD = "standard"  # Presidio + GLiNER (~100ms)
    THOROUGH = "thorough"  # Presidio + GLiNER + LLM (1-3s)


class PIIRedactionMode(str, Enum):
    """Audio redaction mode."""

    SILENCE = "silence"  # Replace with silence (volume=0)
    BEEP = "beep"  # Replace with 1kHz tone


class PIIEntityCategory(str, Enum):
    """PII entity category for compliance classification."""

    PII = "pii"  # Personal: name, email, phone, SSN, etc.
    PCI = "pci"  # Payment: credit card, IBAN, CVV, etc.
    PHI = "phi"  # Health: MRN, conditions, medications, etc.


@dataclass
class PIIEntity:
    """Detected PII entity with position and timing information."""

    entity_type: str  # e.g., "credit_card_number"
    category: PIIEntityCategory  # pii, pci, phi
    start_offset: int  # Character offset in text
    end_offset: int  # Character offset in text
    start_time: float  # Audio time (seconds)
    end_time: float  # Audio time (seconds)
    confidence: float  # Detection confidence 0.0-1.0
    speaker: str | None  # Speaker ID if diarized
    redacted_value: str  # e.g., "****7890"
    original_text: str  # The original detected text


@dataclass
class PIIDetectionResult:
    """Result of PII detection on a transcript."""

    entities: list[PIIEntity]
    redacted_text: str
    entity_count_by_type: dict[str, int]
    detection_tier: PIIDetectionTier
    processing_time_ms: int


# =============================================================================
# Job and Task Models
# =============================================================================


class JobStatus(str, Enum):
    """Job lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class Job(BaseModel):
    """Batch transcription job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    status: JobStatus = JobStatus.PENDING
    audio_uri: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Computed fields for API responses
    text: str | None = None

    # Result summary stats (populated on successful completion)
    result_language_code: str | None = None
    result_word_count: int | None = None
    result_segment_count: int | None = None
    result_speaker_count: int | None = None
    result_character_count: int | None = None


class Task(BaseModel):
    """Atomic processing unit within a job's DAG."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    stage: str
    engine_id: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[UUID] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    input_uri: str | None = None
    output_uri: str | None = None
    retries: int = 0
    max_retries: int = 2
    required: bool = True
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
