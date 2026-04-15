"""Data types for the batch engine SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from dalston.common.artifacts import MaterializedArtifact, ProducedArtifact
from dalston.common.pipeline_types import (
    STAGE_CONFIG_MAP,
    AlignmentRequest,
    AlignmentResponse,
    AudioRedactRequest,
    DiarizationRequest,
    DiarizationResponse,
    MergeRequest,
    PIIDetectionRequest,
    PIIDetectionResponse,
    PreparationRequest,
    PreparationResponse,
    RedactionResponse,
    StageInput,
    Transcript,
    TranscriptionRequest,
    VocabularySupport,
)

# Type variable for generic output model parsing
T = TypeVar("T", bound=BaseModel)
PayloadT = TypeVar("PayloadT")
OutputT = TypeVar("OutputT")


class EngineCapabilities(BaseModel):
    """What an engine can do. Published in heartbeats, declared in catalog."""

    engine_id: str
    version: str
    stages: list[str]
    supports_word_timestamps: bool = False
    supports_native_streaming: bool = False
    model_variants: list[str] | None = None
    gpu_required: bool = False
    gpu_vram_mb: int | None = None
    # M30: New fields for hardware and performance metadata
    supports_cpu: bool = True
    min_ram_gb: int | None = None
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None
    max_concurrency: int | None = None
    # M31: Capability-driven routing - output includes speaker labels (skip diarize stage)
    includes_diarization: bool = False
    # Vocabulary boosting capability
    vocabulary_support: VocabularySupport | None = None


@dataclass
class TaskRequest(Generic[PayloadT]):
    """Input envelope provided to an engine's process method."""

    task_id: str
    job_id: str
    stage: str = "unknown"
    config: dict[str, Any] = field(default_factory=dict)
    payload: PayloadT | dict[str, Any] | None = None
    previous_responses: dict[str, Any] = field(default_factory=dict)
    audio_path: Path | None = None
    materialized_artifacts: dict[str, MaterializedArtifact] = field(
        default_factory=dict
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audio_path is None:
            if "audio" in self.materialized_artifacts:
                self.audio_path = self.materialized_artifacts["audio"].local_path
            elif self.materialized_artifacts:
                self.audio_path = next(
                    iter(self.materialized_artifacts.values())
                ).local_path

    @property
    def media(self) -> dict[str, Any] | None:
        """Convenience accessor for prepare-stage media payload."""
        if self.payload is None:
            return None
        if isinstance(self.payload, dict):
            media = self.payload.get("media")
            return media if isinstance(media, dict) else None
        if isinstance(self.payload, BaseModel):
            data = self.payload.model_dump(mode="json", exclude_none=True)
            media = data.get("media")
            return media if isinstance(media, dict) else None
        return None

    def get_prepare_response(self) -> PreparationResponse | None:
        return self._get_typed_response("prepare", PreparationResponse)

    def get_transcript(self, key: str = "transcribe") -> Transcript | None:
        """Get transcribe output as Transcript."""
        return self._get_typed_response(key, Transcript)

    def get_stage_config(self) -> StageInput:
        """Validate and return typed config for this task's stage.

        Uses STAGE_CONFIG_MAP to find the right model for self.stage.
        For per-channel stages (e.g. "transcribe_ch0"), strips the
        channel suffix to find the base stage.

        Raises:
            KeyError: If stage has no typed config model in STAGE_CONFIG_MAP
            pydantic.ValidationError: If config doesn't match the model
        """
        # Strip channel suffix for per-channel stages
        base_stage = (
            self.stage.rsplit("_ch", 1)[0] if "_ch" in self.stage else self.stage
        )
        model = STAGE_CONFIG_MAP.get(base_stage)
        if model is None:
            raise KeyError(
                f"No typed config model for stage '{self.stage}' "
                f"(known stages: {sorted(STAGE_CONFIG_MAP.keys())})"
            )
        return model.model_validate(self.config)

    def get_prepare_params(self) -> PreparationRequest:
        """Validate and return typed prepare parameters from config."""
        return PreparationRequest.model_validate(self.config)

    def get_transcribe_params(self) -> TranscriptionRequest:
        """Validate and return typed transcribe parameters from config."""
        return TranscriptionRequest.model_validate(self.config)

    def get_align_params(self) -> AlignmentRequest:
        """Validate and return typed align parameters from config."""
        return AlignmentRequest.model_validate(self.config)

    def get_diarize_params(self) -> DiarizationRequest:
        """Validate and return typed diarize parameters from config."""
        return DiarizationRequest.model_validate(self.config)

    def get_merge_params(self) -> MergeRequest:
        """Validate and return typed merge parameters from config."""
        return MergeRequest.model_validate(self.config)

    def get_pii_detect_params(self) -> PIIDetectionRequest:
        """Validate and return typed PII detection parameters from config."""
        return PIIDetectionRequest.model_validate(self.config)

    def get_audio_redact_params(self) -> AudioRedactRequest:
        """Validate and return typed audio redaction parameters from config."""
        return AudioRedactRequest.model_validate(self.config)

    def get_align_response(self, key: str = "align") -> AlignmentResponse | None:
        return self._get_typed_response(key, AlignmentResponse)

    def get_diarize_response(self) -> DiarizationResponse | None:
        return self._get_typed_response("diarize", DiarizationResponse)

    def get_pii_detect_response(
        self, key: str = "pii_detect"
    ) -> PIIDetectionResponse | None:
        return self._get_typed_response(key, PIIDetectionResponse)

    def get_audio_redact_response(
        self, key: str = "audio_redact"
    ) -> RedactionResponse | None:
        return self._get_typed_response(key, RedactionResponse)

    def _get_typed_response(self, key: str, model: type[T]) -> T | None:
        data = self.previous_responses.get(key)
        if data is None:
            return None
        return model.model_validate(data)

    def get_raw_response(self, key: str) -> dict[str, Any] | None:
        return self.previous_responses.get(key)

    def replace(self, **kwargs: Any) -> TaskRequest[PayloadT]:
        """Return a shallow copy with the given fields replaced.

        Mutable defaults (``config``, ``previous_responses``, ``metadata``,
        ``materialized_artifacts``) are shared by reference with the
        original; callers must avoid mutating them on the copy.
        """
        import dataclasses

        return dataclasses.replace(self, **kwargs)


@dataclass
class TaskResponse(Generic[OutputT]):
    """Output envelope returned by an engine's process method."""

    data: BaseModel | dict[str, Any]
    produced_artifacts: list[ProducedArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if isinstance(self.data, BaseModel):
            return self.data.model_dump(mode="json", exclude_none=False)
        return self.data
