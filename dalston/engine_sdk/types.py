"""Data types for the batch engine SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from dalston.common.artifacts import MaterializedArtifact, ProducedArtifact
from dalston.common.pipeline_types import (
    AlignOutput,
    AudioRedactOutput,
    DiarizeOutput,
    PIIDetectOutput,
    PrepareOutput,
    Transcript,
)

# Type variable for generic output model parsing
T = TypeVar("T", bound=BaseModel)
PayloadT = TypeVar("PayloadT")
OutputT = TypeVar("OutputT")


class EngineCapabilities(BaseModel):
    """What an engine can do. Published in heartbeats, declared in catalog."""

    runtime: str
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
    max_concurrency: int | None = None
    # M31: Capability-driven routing - output includes speaker labels (skip diarize stage)
    includes_diarization: bool = False


@dataclass
class EngineInput(Generic[PayloadT]):
    """Input envelope provided to an engine's process method."""

    task_id: str
    job_id: str
    stage: str = "unknown"
    config: dict[str, Any] = field(default_factory=dict)
    payload: PayloadT | dict[str, Any] | None = None
    previous_outputs: dict[str, Any] = field(default_factory=dict)
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

    def get_prepare_output(self) -> PrepareOutput | None:
        return self._get_typed_output("prepare", PrepareOutput)

    def get_transcript(self, key: str = "transcribe") -> Transcript | None:
        """Get transcribe output as Transcript."""
        return self._get_typed_output(key, Transcript)

    def get_align_output(self, key: str = "align") -> AlignOutput | None:
        return self._get_typed_output(key, AlignOutput)

    def get_diarize_output(self) -> DiarizeOutput | None:
        return self._get_typed_output("diarize", DiarizeOutput)

    def get_pii_detect_output(self, key: str = "pii_detect") -> PIIDetectOutput | None:
        return self._get_typed_output(key, PIIDetectOutput)

    def get_audio_redact_output(
        self, key: str = "audio_redact"
    ) -> AudioRedactOutput | None:
        return self._get_typed_output(key, AudioRedactOutput)

    def _get_typed_output(self, key: str, model: type[T]) -> T | None:
        data = self.previous_outputs.get(key)
        if data is None:
            return None
        return model.model_validate(data)

    def get_raw_output(self, key: str) -> dict[str, Any] | None:
        return self.previous_outputs.get(key)


@dataclass
class EngineOutput(Generic[OutputT]):
    """Output envelope returned by an engine's process method."""

    data: BaseModel | dict[str, Any]
    produced_artifacts: list[ProducedArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if isinstance(self.data, BaseModel):
            return self.data.model_dump(mode="json", exclude_none=False)
        return self.data
