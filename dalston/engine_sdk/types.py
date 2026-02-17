"""Data types for engine SDK."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from dalston.common.pipeline_types import (
    AlignOutput,
    AudioRedactOutput,
    DiarizeOutput,
    PIIDetectOutput,
    PrepareOutput,
    TranscribeOutput,
)

# Type variable for generic output model parsing
T = TypeVar("T", bound=BaseModel)


class EngineCapabilities(BaseModel):
    """What an engine can do. Published in heartbeats, declared in catalog.

    This schema defines engine capabilities for:
    - Validation: Check if a job's requirements match running engine capabilities
    - Catalog: Declare what engines could be started and their resource needs
    - Routing: (future) Select best engine for a given job

    Attributes:
        engine_id: Unique engine identifier (e.g., "parakeet", "faster-whisper")
        version: Engine version string
        stages: Pipeline stages this engine handles (e.g., ["transcribe"])
        languages: ISO 639-1 codes supported, None means all languages
        supports_word_timestamps: Whether engine produces word-level timestamps
        supports_streaming: Whether engine supports streaming transcription
        model_variants: Available model variants (e.g., ["large-v3", "medium"])
        gpu_required: Whether GPU is required for this engine
        gpu_vram_mb: Estimated VRAM usage in MB
        supports_cpu: Whether CPU inference is supported (M30)
        min_ram_gb: Minimum system RAM in GB (M30)
        rtf_gpu: Real-time factor on GPU (M30)
        rtf_cpu: Real-time factor on CPU (M30)
        max_concurrent_jobs: Maximum concurrent job limit (M30)
        includes_diarization: Whether output includes speaker labels (M31)
    """

    engine_id: str
    version: str
    stages: list[str]
    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    supports_streaming: bool = False
    model_variants: list[str] | None = None
    gpu_required: bool = False
    gpu_vram_mb: int | None = None
    # M30: New fields for hardware and performance metadata
    supports_cpu: bool = True
    min_ram_gb: int | None = None
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None
    max_concurrent_jobs: int | None = None
    # M31: Capability-driven routing - output includes speaker labels (skip diarize stage)
    includes_diarization: bool = False


@dataclass
class TaskInput:
    """Input data provided to an engine's process method.

    Attributes:
        task_id: Unique identifier for this task
        job_id: Parent job identifier
        audio_path: Path to the audio file (downloaded from S3 to local temp)
        previous_outputs: Results from dependency tasks, keyed by stage name
        config: Engine-specific configuration from job parameters
        media: Audio file metadata (for prepare stage - format, duration, etc.)
        stage: Stage name for this task (e.g., "transcribe", "audio_redact_ch0")
    """

    task_id: str
    job_id: str
    audio_path: Path
    previous_outputs: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] | None = None
    stage: str = "unknown"

    def get_prepare_output(self) -> PrepareOutput | None:
        """Get typed prepare stage output.

        Returns:
            PrepareOutput if present and valid, None otherwise
        """
        return self._get_typed_output("prepare", PrepareOutput)

    def get_transcribe_output(self, key: str = "transcribe") -> TranscribeOutput | None:
        """Get typed transcribe stage output.

        Args:
            key: Output key, defaults to "transcribe". Use "transcribe_ch0" etc
                 for per-channel mode.

        Returns:
            TranscribeOutput if present and valid, None otherwise
        """
        return self._get_typed_output(key, TranscribeOutput)

    def get_align_output(self, key: str = "align") -> AlignOutput | None:
        """Get typed align stage output.

        Args:
            key: Output key, defaults to "align". Use "align_ch0" etc
                 for per-channel mode.

        Returns:
            AlignOutput if present and valid, None otherwise
        """
        return self._get_typed_output(key, AlignOutput)

    def get_diarize_output(self) -> DiarizeOutput | None:
        """Get typed diarize stage output.

        Returns:
            DiarizeOutput if present and valid, None otherwise
        """
        return self._get_typed_output("diarize", DiarizeOutput)

    def get_pii_detect_output(self, key: str = "pii_detect") -> PIIDetectOutput | None:
        """Get typed PII detection stage output.

        Args:
            key: Output key, defaults to "pii_detect". Use "pii_detect_ch0" etc
                 for per-channel mode.

        Returns:
            PIIDetectOutput if present and valid, None otherwise
        """
        return self._get_typed_output(key, PIIDetectOutput)

    def get_audio_redact_output(
        self, key: str = "audio_redact"
    ) -> AudioRedactOutput | None:
        """Get typed audio redaction stage output.

        Args:
            key: Output key, defaults to "audio_redact". Use "audio_redact_ch0" etc
                 for per-channel mode.

        Returns:
            AudioRedactOutput if present and valid, None otherwise
        """
        return self._get_typed_output(key, AudioRedactOutput)

    def _get_typed_output(self, key: str, model: type[T]) -> T | None:
        """Get a typed output from previous_outputs.

        Args:
            key: The stage key in previous_outputs
            model: Pydantic model class to validate against

        Returns:
            Validated model instance or None if not present/invalid
        """
        data = self.previous_outputs.get(key)
        if data is None:
            return None

        try:
            return model.model_validate(data)
        except Exception:
            # Fall back to returning None if validation fails
            # This allows gradual migration - engines can handle raw dicts
            return None

    def get_raw_output(self, key: str) -> dict[str, Any] | None:
        """Get raw (unvalidated) output from previous_outputs.

        Use this when you need the raw dict without type validation,
        or during migration from untyped to typed outputs.

        Args:
            key: The stage key in previous_outputs

        Returns:
            Raw dict or None if not present
        """
        return self.previous_outputs.get(key)


@dataclass
class TaskOutput:
    """Output data returned from an engine's process method.

    Attributes:
        data: Structured result - either a Pydantic model or dict.
              Pydantic models are automatically converted to dict for serialization.
        artifacts: Additional files produced, keyed by name with Path values
    """

    data: BaseModel | dict[str, Any]
    artifacts: dict[str, Path] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert data to dictionary for serialization.

        Returns:
            Dictionary representation of the output data
        """
        if isinstance(self.data, BaseModel):
            return self.data.model_dump(mode="json", exclude_none=False)
        return self.data
