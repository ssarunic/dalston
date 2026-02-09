"""Data types for engine SDK."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from dalston.common.pipeline_types import (
    AlignOutput,
    DiarizeOutput,
    PrepareOutput,
    TranscribeOutput,
)

# Type variable for generic output model parsing
T = TypeVar("T", bound=BaseModel)


@dataclass
class TaskInput:
    """Input data provided to an engine's process method.

    Attributes:
        task_id: Unique identifier for this task
        job_id: Parent job identifier
        audio_path: Path to the audio file (downloaded from S3 to local temp)
        previous_outputs: Results from dependency tasks, keyed by stage name
        config: Engine-specific configuration from job parameters
    """

    task_id: str
    job_id: str
    audio_path: Path
    previous_outputs: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

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
