"""Internal artifact reference and binding contracts for M51."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def build_task_artifact_id(task_id: str, logical_name: str) -> str:
    """Build canonical task-scoped artifact ID."""
    return f"{task_id}:{logical_name}"


class ArtifactSelector(BaseModel):
    """Describes which upstream artifact should fill an input slot."""

    model_config = ConfigDict(extra="forbid")

    producer_stage: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    channel: int | None = Field(default=None, ge=0)
    role: str | None = Field(default=None)
    required: bool = Field(default=True)


class RequestBinding(BaseModel):
    """Binds an input slot to a selector."""

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(..., min_length=1)
    selector: ArtifactSelector


class ProducedArtifact(BaseModel):
    """Artifact produced by an engine on local filesystem."""

    model_config = ConfigDict(extra="forbid")

    logical_name: str = Field(..., min_length=1)
    local_path: Path
    kind: str = Field(..., min_length=1)
    channel: int | None = Field(default=None, ge=0)
    role: str | None = None
    media_type: str | None = None


class ArtifactReference(BaseModel):
    """Job-scoped artifact record persisted by engine_id infrastructure."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    storage_locator: str = Field(..., min_length=1)
    checksum: str | None = None
    size: int | None = Field(default=None, ge=0)
    media_type: str | None = None
    channel: int | None = Field(default=None, ge=0)
    role: str | None = None
    producer_task_id: str | None = None
    producer_stage: str | None = None


class MaterializedArtifact(BaseModel):
    """Locally materialized artifact ready for engine processing."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    local_path: Path
    channel: int | None = Field(default=None, ge=0)
    role: str | None = None
    media_type: str | None = None
