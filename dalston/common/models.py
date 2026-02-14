from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal
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
# Model Selection Registry (M14)
# =============================================================================


@dataclass
class ModelDefinition:
    """Definition of a transcription model.

    Maps user-facing model names to engine-specific configuration.
    """

    id: str  # API-facing identifier (e.g., "whisper-large-v3")
    engine: str  # Engine ID (e.g., "faster-whisper")
    engine_model: str  # Model name passed to engine (e.g., "large-v3")
    name: str  # Human-readable name
    description: str  # Brief description
    tier: Literal["fast", "balanced", "accurate"]
    languages: int  # Number of supported languages (1 = English-only)
    streaming: bool  # Supports real-time streaming
    word_timestamps: bool  # Supports word-level timing
    vram_gb: float  # Approximate VRAM requirement
    speed_factor: float  # Relative speed (1.0 = baseline)


MODEL_REGISTRY: dict[str, ModelDefinition] = {
    "whisper-large-v3": ModelDefinition(
        id="whisper-large-v3",
        engine="faster-whisper",
        engine_model="large-v3",
        name="Whisper Large v3",
        description="Most accurate multilingual model, 99 languages",
        tier="accurate",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=10.0,
        speed_factor=1.0,
    ),
    "whisper-large-v2": ModelDefinition(
        id="whisper-large-v2",
        engine="faster-whisper",
        engine_model="large-v2",
        name="Whisper Large v2",
        description="Previous generation large model",
        tier="accurate",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=10.0,
        speed_factor=1.0,
    ),
    "whisper-medium": ModelDefinition(
        id="whisper-medium",
        engine="faster-whisper",
        engine_model="medium",
        name="Whisper Medium",
        description="Balanced accuracy and speed",
        tier="balanced",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=5.0,
        speed_factor=2.0,
    ),
    "whisper-small": ModelDefinition(
        id="whisper-small",
        engine="faster-whisper",
        engine_model="small",
        name="Whisper Small",
        description="Fast multilingual transcription",
        tier="fast",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=2.0,
        speed_factor=4.0,
    ),
    "whisper-base": ModelDefinition(
        id="whisper-base",
        engine="faster-whisper",
        engine_model="base",
        name="Whisper Base",
        description="Very fast, lower accuracy",
        tier="fast",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=1.0,
        speed_factor=8.0,
    ),
    "whisper-tiny": ModelDefinition(
        id="whisper-tiny",
        engine="faster-whisper",
        engine_model="tiny",
        name="Whisper Tiny",
        description="Fastest, minimal accuracy",
        tier="fast",
        languages=99,
        streaming=False,
        word_timestamps=True,
        vram_gb=0.5,
        speed_factor=16.0,
    ),
    "distil-whisper": ModelDefinition(
        id="distil-whisper",
        engine="faster-whisper",
        engine_model="distil-large-v3",
        name="Distil-Whisper",
        description="Fast English-only, near large-v3 accuracy",
        tier="fast",
        languages=1,
        streaming=False,
        word_timestamps=True,
        vram_gb=5.0,
        speed_factor=6.0,
    ),
    # NVIDIA Parakeet FastConformer models
    "parakeet-110m": ModelDefinition(
        id="parakeet-110m",
        engine="parakeet",
        engine_model="nvidia/parakeet-tdt_ctc-110m",
        name="Parakeet 110M",
        description="Lightweight English-only, low memory footprint",
        tier="fast",
        languages=1,
        streaming=True,
        word_timestamps=True,
        vram_gb=1.0,
        speed_factor=12.0,
    ),
    "parakeet-0.6b": ModelDefinition(
        id="parakeet-0.6b",
        engine="parakeet",
        engine_model="nvidia/parakeet-rnnt-0.6b",
        name="Parakeet 0.6B",
        description="Fast English-only with native streaming, low latency",
        tier="fast",
        languages=1,
        streaming=True,
        word_timestamps=True,
        vram_gb=4.0,
        speed_factor=10.0,  # RTFx >2000, much faster than Whisper
    ),
    "parakeet-1.1b": ModelDefinition(
        id="parakeet-1.1b",
        engine="parakeet",
        engine_model="nvidia/parakeet-rnnt-1.1b",
        name="Parakeet 1.1B",
        description="Balanced English-only with native streaming",
        tier="balanced",
        languages=1,
        streaming=True,
        word_timestamps=True,
        vram_gb=6.0,
        speed_factor=8.0,
    ),
}

MODEL_ALIASES: dict[str, str] = {
    "fast": "distil-whisper",
    "accurate": "whisper-large-v3",
    "large": "whisper-large-v3",
    "medium": "whisper-medium",
    "small": "whisper-small",
    "base": "whisper-base",
    "tiny": "whisper-tiny",
    "parakeet": "parakeet-110m",
    # ElevenLabs compatibility aliases (map to Parakeet for realtime)
    "scribe_v1": "parakeet-0.6b",
    "scribe_v2": "parakeet-1.1b",
}

DEFAULT_MODEL = "whisper-large-v3"


def resolve_model(model_id: str) -> ModelDefinition:
    """Resolve model ID or alias to ModelDefinition.

    Args:
        model_id: Model identifier or alias

    Returns:
        ModelDefinition for the resolved model

    Raises:
        ValueError: If model not found
    """
    resolved_id = MODEL_ALIASES.get(model_id, model_id)
    if resolved_id not in MODEL_REGISTRY:
        available = ", ".join(sorted(MODEL_REGISTRY.keys()))
        raise ValueError(f"Unknown model: '{model_id}'. Available models: {available}")
    return MODEL_REGISTRY[resolved_id]


def get_available_models() -> list[ModelDefinition]:
    """Return all registered models."""
    return list(MODEL_REGISTRY.values())


def get_model_ids() -> list[str]:
    """Return all model IDs (without aliases)."""
    return list(MODEL_REGISTRY.keys())


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
