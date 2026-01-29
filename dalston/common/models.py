from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """Job lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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
