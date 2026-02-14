"""Pydantic response schemas for Gateway API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dalston.common.models import JobStatus


class RetentionInfo(BaseModel):
    """Retention information for a job or session."""

    policy_id: UUID | None = Field(
        default=None, description="Reference to retention policy"
    )
    policy_name: str | None = Field(default=None, description="Policy name at creation")
    mode: str = Field(description="Retention mode: auto_delete, keep, none")
    hours: int | None = Field(default=None, description="Hours to retain")
    scope: str | None = Field(
        default=None, description="Deletion scope: all, audio_only"
    )
    purge_after: datetime | None = Field(
        default=None, description="Scheduled purge time (computed on completion)"
    )
    purged_at: datetime | None = Field(
        default=None, description="Actual purge time (set when purged)"
    )


class PIIEntityResponse(BaseModel):
    """Detected PII entity in response."""

    entity_type: str = Field(description="Entity type (e.g., 'credit_card_number')")
    category: str = Field(description="Category: pii, pci, phi")
    start_offset: int = Field(description="Character offset in text")
    end_offset: int = Field(description="Character offset in text")
    start_time: float = Field(description="Audio time in seconds")
    end_time: float = Field(description="Audio time in seconds")
    confidence: float = Field(description="Detection confidence 0.0-1.0")
    speaker: str | None = Field(default=None, description="Speaker ID if diarized")
    redacted_value: str = Field(description="Redacted representation")


class PIIInfo(BaseModel):
    """PII detection information for a job."""

    enabled: bool = Field(description="Whether PII detection was enabled")
    detection_tier: str | None = Field(
        default=None, description="Detection tier: fast, standard, thorough"
    )
    entities_detected: int | None = Field(
        default=None, description="Total number of PII entities detected"
    )
    entity_summary: dict[str, int] | None = Field(
        default=None, description="Count of entities by type"
    )
    redacted_audio_available: bool = Field(
        default=False, description="Whether redacted audio is available"
    )


class JobCreatedResponse(BaseModel):
    """Response for POST /v1/audio/transcriptions."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus = JobStatus.PENDING
    created_at: datetime


class JobCancelledResponse(BaseModel):
    """Response for POST /v1/audio/transcriptions/{job_id}/cancel."""

    id: UUID
    status: JobStatus
    message: str


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

    # Retention info (M25)
    retention: RetentionInfo | None = Field(
        default=None, description="Retention policy and status"
    )

    # PII detection info (M26)
    pii: PIIInfo | None = Field(
        default=None, description="PII detection status and results"
    )
    redacted_text: str | None = Field(
        default=None, description="Transcript with PII redacted"
    )
    entities: list[PIIEntityResponse] | None = Field(
        default=None, description="Detected PII entities"
    )

    # Result summary stats (populated on successful completion)
    audio_duration_seconds: float | None = Field(
        default=None, description="Audio duration in seconds"
    )
    result_language_code: str | None = Field(
        default=None, description="Detected language code"
    )
    result_word_count: int | None = Field(default=None, description="Total word count")
    result_segment_count: int | None = Field(
        default=None, description="Number of transcript segments"
    )
    result_speaker_count: int | None = Field(
        default=None, description="Number of speakers (null if no diarization)"
    )
    result_character_count: int | None = Field(
        default=None, description="Total character count"
    )


class JobSummary(BaseModel):
    """Summary of a job for list responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: int | None = None

    # Result summary stats (for quick display without fetching transcript)
    audio_duration_seconds: float | None = Field(
        default=None, alias="audio_duration", description="Audio duration in seconds"
    )
    result_language_code: str | None = None
    result_word_count: int | None = None
    result_segment_count: int | None = None
    result_speaker_count: int | None = None


class JobListResponse(BaseModel):
    """Response for GET /v1/audio/transcriptions."""

    jobs: list[JobSummary]
    cursor: str | None = None
    has_more: bool


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

    stage: str = Field(
        description="Pipeline stage name (e.g., prepare, transcribe, align)"
    )
    task_id: UUID = Field(description="UUID of the underlying task")
    engine_id: str = Field(description="Engine that executed this task")
    status: str = Field(
        description="Task status: pending, ready, running, completed, failed, skipped"
    )
    required: bool = Field(
        description="Whether this stage was required for job success"
    )
    started_at: datetime | None = Field(
        default=None, description="When execution began"
    )
    completed_at: datetime | None = Field(
        default=None, description="When execution finished"
    )
    duration_ms: int | None = Field(
        default=None, description="Wall-clock duration in milliseconds"
    )
    retries: int | None = Field(
        default=None, description="Number of retries attempted (omitted if 0)"
    )
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


# =============================================================================
# PII Entity Types (M26)
# =============================================================================


class PIIEntityTypeResponse(BaseModel):
    """PII entity type for listing available types."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Entity type identifier (e.g., 'credit_card_number')")
    category: str = Field(description="Category: pii, pci, phi")
    display_name: str = Field(description="Human-readable name")
    description: str | None = Field(
        default=None, description="Description of the entity type"
    )
    detection_method: str = Field(
        description="Detection method: regex, gliner, regex+luhn, etc."
    )
    is_default: bool = Field(description="Whether included in default detection")


class PIIEntityTypesResponse(BaseModel):
    """Response for GET /v1/pii/entity-types."""

    entity_types: list[PIIEntityTypeResponse]
    total: int = Field(description="Total number of entity types")
