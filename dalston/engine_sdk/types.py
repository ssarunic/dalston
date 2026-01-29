"""Data types for engine SDK."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass
class TaskOutput:
    """Output data returned from an engine's process method.

    Attributes:
        data: Structured result dictionary (e.g., transcript, segments)
        artifacts: Additional files produced, keyed by name with Path values
    """

    data: dict[str, Any]
    artifacts: dict[str, Path] | None = None
