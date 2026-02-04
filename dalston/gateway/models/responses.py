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
