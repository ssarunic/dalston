"""Pydantic response schemas for Gateway API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dalston.common.models import JobStatus


class JobCreatedResponse(BaseModel):
    """Response for POST /v1/audio/transcriptions."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus = JobStatus.PENDING
    created_at: datetime


class JobResponse(BaseModel):
    """Response for GET /v1/audio/transcriptions/{id}.

    Full job details including transcript when completed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Error info (if failed)
    error: str | None = None

    # Progress info (if running)
    progress: int | None = Field(default=None, ge=0, le=100)
    current_stage: str | None = None

    # Stage breakdown (available once job has tasks)
    stages: list["StageResponse"] | None = Field(
        default=None,
        description="Pipeline stages with status and timing (available once job is RUNNING)",
    )

    # Transcript data (if completed) - populated from S3
    language_code: str | None = None
    text: str | None = None
    words: list[dict[str, Any]] | None = None
    segments: list[dict[str, Any]] | None = None
    speakers: list[dict[str, Any]] | None = None


class JobSummary(BaseModel):
    """Summary of a job for list responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: int | None = None


class JobListResponse(BaseModel):
    """Response for GET /v1/audio/transcriptions."""

    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int


class JobStatsResponse(BaseModel):
    """Response for GET /v1/jobs/stats."""

    running: int
    queued: int
    completed_today: int
    failed_today: int


class ErrorDetail(BaseModel):
    """Error detail following API.md spec."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: ErrorDetail


class StageResponse(BaseModel):
    """Stage information within a job's pipeline.

    Represents the status and timing of a single processing stage.
    """

    stage: str = Field(description="Pipeline stage name (e.g., prepare, transcribe, align)")
    task_id: UUID = Field(description="UUID of the underlying task")
    engine_id: str = Field(description="Engine that executed this task")
    status: str = Field(description="Task status: pending, ready, running, completed, failed, skipped")
    required: bool = Field(description="Whether this stage was required for job success")
    started_at: datetime | None = Field(default=None, description="When execution began")
    completed_at: datetime | None = Field(default=None, description="When execution finished")
    duration_ms: int | None = Field(default=None, description="Wall-clock duration in milliseconds")
    retries: int | None = Field(default=None, description="Number of retries attempted (omitted if 0)")
    error: str | None = Field(default=None, description="Error message if failed")


class TaskResponse(BaseModel):
    """Full task information including dependencies.

    Used in the task list endpoint for pipeline visualization.
    """

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID = Field(description="Task UUID")
    stage: str = Field(description="Pipeline stage name")
    engine_id: str = Field(description="Engine that processed this task")
    status: str = Field(description="Task status")
    required: bool = Field(description="Whether this task is required for job success")
    dependencies: list[UUID] = Field(description="Task IDs this task depends on")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    retries: int = 0
    error: str | None = None


class TaskListResponse(BaseModel):
    """Response for GET /v1/audio/transcriptions/{job_id}/tasks."""

    job_id: UUID
    tasks: list[TaskResponse]


class TaskArtifactResponse(BaseModel):
    """Response for GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts.

    Contains the raw input and output data for a specific task.
    """

    task_id: UUID
    job_id: UUID
    stage: str
    engine_id: str
    status: str
    input: dict[str, Any] | None = Field(
        default=None,
        description="Task input as passed to the engine (from input.json in S3)",
    )
    output: dict[str, Any] | None = Field(
        default=None,
        description="Task output (from output.json in S3). Null if task has not completed.",
    )
