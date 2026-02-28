"""Console API endpoints for the web management interface.

GET /api/console/dashboard - Aggregated dashboard data
GET /api/console/jobs/{job_id}/tasks - Get task DAG for a job
GET /api/console/engines - Get batch and realtime engine status
DELETE /api/console/jobs/{job_id} - Delete a job and its artifacts (admin)
GET /api/console/settings - List setting namespaces
GET /api/console/settings/{namespace} - Get settings in a namespace
PATCH /api/console/settings/{namespace} - Update settings
POST /api/console/settings/{namespace}/reset - Reset to defaults
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dalston.common.events import publish_job_cancel_requested, publish_job_created
from dalston.common.models import JobStatus
from dalston.common.streams_types import CONSUMER_GROUP
from dalston.config import Settings
from dalston.db.models import JobModel
from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.dependencies import (
    RequireAdmin,
    get_db,
    get_jobs_service,
    get_redis,
    get_session_router,
    get_settings,
)
from dalston.gateway.models.responses import JobCancelledResponse, JobRetryResponse
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService
from dalston.session_router import SessionRouter

logger = structlog.get_logger()

router = APIRouter(prefix="/api/console", tags=["console"])


async def _get_stream_backlog(redis: Redis, stream_key: str) -> int:
    """Return undelivered message count (group lag) for an engine stream."""
    try:
        groups = await redis.xinfo_groups(stream_key)
    except Exception:
        return 0

    for group in groups:
        name = group.get("name")
        if isinstance(name, bytes):
            name = name.decode()
        if name != CONSUMER_GROUP:
            continue

        lag = group.get("lag")
        if lag is None:
            return 0
        if isinstance(lag, bytes):
            lag = lag.decode()
        try:
            return max(0, int(lag))
        except (TypeError, ValueError):
            return 0

    return 0


# Dashboard models
class SystemStatus(BaseModel):
    """System health status."""

    healthy: bool
    version: str = "0.1.0"


class BatchStats(BaseModel):
    """Batch processing statistics."""

    running_jobs: int
    queued_jobs: int
    completed_today: int
    failed_today: int


class RealtimeCapacity(BaseModel):
    """Realtime worker capacity."""

    total_capacity: int
    used_capacity: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class JobSummary(BaseModel):
    """Job summary for dashboard."""

    id: UUID
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class DashboardResponse(BaseModel):
    """Aggregated dashboard data."""

    system: SystemStatus
    batch: BatchStats
    realtime: RealtimeCapacity
    recent_jobs: list[JobSummary]


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Get dashboard data",
    description="Get aggregated dashboard data including system status, batch stats, realtime capacity, and recent jobs.",
)
async def get_dashboard(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    session_router: SessionRouter = Depends(get_session_router),
) -> DashboardResponse:
    """Get aggregated dashboard data in a single call."""
    # Get job counts by status
    status_counts = await db.execute(
        select(JobModel.status, func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .group_by(JobModel.status)
    )
    counts = {row[0]: row[1] for row in status_counts.all()}

    # Get today's completed/failed counts
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = await db.execute(
        select(func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .where(JobModel.status == JobStatus.COMPLETED.value)
        .where(JobModel.completed_at >= today_start)
    )
    today_failed = await db.execute(
        select(func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .where(JobModel.status == JobStatus.FAILED.value)
        .where(JobModel.completed_at >= today_start)
    )

    # Get recent jobs
    recent_result = await db.execute(
        select(JobModel)
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .order_by(JobModel.created_at.desc())
        .limit(5)
    )
    recent_jobs = recent_result.scalars().all()

    # Get realtime capacity
    try:
        capacity = await session_router.get_capacity()
        realtime = RealtimeCapacity(
            total_capacity=capacity.total_capacity,
            used_capacity=capacity.used_capacity,
            available_capacity=capacity.available_capacity,
            worker_count=capacity.worker_count,
            ready_workers=capacity.ready_workers,
        )
    except Exception:
        # If session router is not available, return zeros
        realtime = RealtimeCapacity(
            total_capacity=0,
            used_capacity=0,
            available_capacity=0,
            worker_count=0,
            ready_workers=0,
        )

    return DashboardResponse(
        system=SystemStatus(healthy=True),
        batch=BatchStats(
            running_jobs=counts.get(JobStatus.RUNNING.value, 0),
            queued_jobs=counts.get(JobStatus.PENDING.value, 0),
            completed_today=today_completed.scalar() or 0,
            failed_today=today_failed.scalar() or 0,
        ),
        realtime=realtime,
        recent_jobs=[
            JobSummary(
                id=job.id,
                status=job.status,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
            )
            for job in recent_jobs
        ],
    )


class TaskResponse(BaseModel):
    """Task in the job pipeline."""

    id: UUID
    stage: str
    engine_id: str
    status: str
    dependencies: list[UUID]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Response for task list endpoint."""

    job_id: UUID
    tasks: list[TaskResponse]


@router.get(
    "/jobs/{job_id}/tasks",
    response_model=TaskListResponse,
    summary="Get job tasks",
    description="Get all tasks in the job's processing pipeline.",
)
async def get_job_tasks(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    """Get task DAG for a job."""
    # Fetch job with tasks
    result = await db.execute(
        select(JobModel)
        .where(JobModel.id == job_id)
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .options(selectinload(JobModel.tasks))
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Sort tasks by stage order for display
    stage_order = {
        "prepare": 0,
        "transcribe": 1,
        "align": 2,
        "diarize": 3,
        "pii_detect": 4,
        "audio_redact": 5,
        "refine": 6,
        "merge": 7,
    }
    sorted_tasks = sorted(
        job.tasks,
        key=lambda t: (stage_order.get(t.stage, 99), t.engine_id),
    )

    def compute_duration(task):
        if task.started_at and task.completed_at:
            delta = task.completed_at - task.started_at
            return int(delta.total_seconds() * 1000)
        return None

    return TaskListResponse(
        job_id=job.id,
        tasks=[
            TaskResponse(
                id=task.id,
                stage=task.stage,
                engine_id=task.engine_id,
                status=task.status,
                dependencies=task.dependencies or [],
                started_at=task.started_at,
                completed_at=task.completed_at,
                duration_ms=compute_duration(task),
                error=task.error,
            )
            for task in sorted_tasks
        ],
    )


class TaskArtifactResponse(BaseModel):
    """Task artifact data for debugging."""

    task_id: UUID
    job_id: UUID
    stage: str
    engine_id: str
    status: str
    required: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    retries: int = 0
    max_retries: int = 2
    error: str | None = None
    dependencies: list[UUID] = []
    input: dict | None = None
    output: dict | None = None


@router.get(
    "/jobs/{job_id}/tasks/{task_id}/artifacts",
    response_model=TaskArtifactResponse,
    summary="Get task artifacts",
    description="Get detailed task information including input/output artifacts.",
)
async def get_task_artifacts(
    job_id: UUID,
    task_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> TaskArtifactResponse:
    """Get task artifacts for debugging."""
    from dalston.config import get_settings
    from dalston.gateway.services.storage import StorageService

    # Fetch job with tasks
    result = await db.execute(
        select(JobModel)
        .where(JobModel.id == job_id)
        .options(selectinload(JobModel.tasks))
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Find the task
    task = next((t for t in job.tasks if t.id == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Calculate duration
    duration_ms = None
    if task.started_at and task.completed_at:
        delta = task.completed_at - task.started_at
        duration_ms = int(delta.total_seconds() * 1000)

    # Fetch artifacts from S3 if task has started
    input_data = None
    output_data = None
    if task.status != "pending":
        settings = get_settings()
        storage = StorageService(settings)
        input_data = await storage.get_task_input(job_id, task_id)
        output_data = await storage.get_task_output(job_id, task_id)

    return TaskArtifactResponse(
        task_id=task.id,
        job_id=job_id,
        stage=task.stage,
        engine_id=task.engine_id,
        status=task.status,
        required=task.required,
        started_at=task.started_at,
        completed_at=task.completed_at,
        duration_ms=duration_ms,
        retries=task.retries,
        max_retries=task.max_retries,
        error=task.error,
        dependencies=task.dependencies or [],
        input=input_data,
        output=output_data,
    )


# Engine models
class BatchEngine(BaseModel):
    """Batch engine status."""

    engine_id: str
    stage: str
    status: str  # "healthy" or "unhealthy"
    queue_depth: int
    processing: int


class RealtimeWorker(BaseModel):
    """Realtime worker status."""

    worker_id: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]
    supports_vocabulary: bool = False


class EnginesResponse(BaseModel):
    """Response for engines endpoint."""

    batch_engines: list[BatchEngine]
    realtime_engines: list[RealtimeWorker]


# Heartbeat timeout thresholds (seconds)
HEARTBEAT_STALE_THRESHOLD = 30  # Mark as stale after 30s without heartbeat

# Redis key patterns for engine registry (shared with orchestrator)
ENGINE_SET_KEY = "dalston:batch:engines"
ENGINE_KEY_PREFIX = "dalston:batch:engine:"
ENGINE_INSTANCES_PREFIX = "dalston:batch:engine:instances:"


@router.get(
    "/engines",
    response_model=EnginesResponse,
    summary="Get engine status",
    description="Get status of all batch and realtime engines.",
)
async def get_engines(
    api_key: RequireAdmin,
    redis: Redis = Depends(get_redis),
    session_router: SessionRouter = Depends(get_session_router),
) -> EnginesResponse:
    """Get status of all engines.

    Uses the engine catalog as the source of truth for which engines exist,
    combined with Redis heartbeats for live status.
    """
    from dalston.orchestrator.catalog import get_catalog

    catalog = get_catalog()

    # Fetch heartbeats for all registered engines from Redis
    # Now uses instance-based lookup: engine_id -> instance set -> instance heartbeats
    registered_engine_ids = await redis.smembers(ENGINE_SET_KEY)
    discovered_heartbeats: dict[str, list[dict[str, str]]] = {}

    for engine_id in registered_engine_ids:
        # Get all instances for this logical engine
        instances_key = f"{ENGINE_INSTANCES_PREFIX}{engine_id}"
        instance_ids = await redis.smembers(instances_key)

        instance_heartbeats = []
        for instance_id in instance_ids:
            data = await redis.hgetall(f"{ENGINE_KEY_PREFIX}{instance_id}")
            if data and "engine_id" in data:
                instance_heartbeats.append(data)

        if instance_heartbeats:
            discovered_heartbeats[engine_id] = instance_heartbeats

    now = datetime.now(UTC)
    batch_engines = []

    # Process all batch engines from catalog (skip realtime engines)
    for entry in catalog.get_all_engines():
        # Skip realtime engines (they have empty stages list)
        if not entry.capabilities.stages:
            continue

        engine_id = entry.engine_id
        stage = entry.capabilities.stages[0]

        stream_key = f"dalston:stream:{engine_id}"
        queue_depth = await _get_stream_backlog(redis, stream_key)

        instance_heartbeats = discovered_heartbeats.get(engine_id, [])
        if not instance_heartbeats:
            # No heartbeats = offline
            status = "offline"
            processing = 0
        else:
            # Aggregate status across instances:
            # - Any instance processing = processing
            # - Any instance healthy = idle
            # - All instances stale = stale
            best_status = "stale"
            total_processing = 0

            for heartbeat in instance_heartbeats:
                try:
                    last_seen = datetime.fromisoformat(heartbeat["last_heartbeat"])
                    age = (now - last_seen).total_seconds()
                except (KeyError, ValueError):
                    age = float("inf")

                if age <= HEARTBEAT_STALE_THRESHOLD:
                    instance_status = heartbeat.get("status", "idle")
                    if instance_status == "processing":
                        best_status = "processing"
                    elif best_status != "processing":
                        best_status = instance_status

                    if heartbeat.get("current_task"):
                        total_processing += 1

            status = best_status
            processing = total_processing

        batch_engines.append(
            BatchEngine(
                engine_id=engine_id,
                stage=stage,
                status=status,
                queue_depth=queue_depth,
                processing=processing,
            )
        )

    # Get realtime workers and track which engine types have running workers
    realtime_engines = []
    running_engine_types: set[str] = set()

    try:
        workers = await session_router.list_workers()
        for worker in workers:
            running_engine_types.add(worker.engine)
            realtime_engines.append(
                RealtimeWorker(
                    worker_id=worker.worker_id,
                    endpoint=worker.endpoint,
                    status=worker.status,
                    capacity=worker.capacity,
                    active_sessions=worker.active_sessions,
                    models=worker.models,
                    languages=worker.languages,
                    supports_vocabulary=worker.supports_vocabulary,
                )
            )
    except Exception:
        # Session router may not be available
        pass

    # Add offline entries for realtime engines in catalog with no running workers
    for entry in catalog.get_all_engines():
        # Realtime engines have empty stages list
        if entry.capabilities.stages:
            continue

        engine_id = entry.engine_id
        # Check if any workers are running for this engine type
        # Worker engine field uses short name (e.g., "whisper" not "whisper-streaming")
        engine_short_name = engine_id.replace("-streaming", "")
        if engine_short_name not in running_engine_types:
            # No workers running - add offline placeholder
            realtime_engines.append(
                RealtimeWorker(
                    worker_id=f"{engine_id} (offline)",
                    endpoint="",
                    status="offline",
                    capacity=entry.capabilities.max_concurrency or 4,
                    active_sessions=0,
                    models=[],
                    languages=entry.capabilities.languages or [],
                )
            )

    return EnginesResponse(
        batch_engines=batch_engines,
        realtime_engines=realtime_engines,
    )


# Job listing for console
class ConsoleJobSummary(BaseModel):
    """Job summary for console listing."""

    id: UUID
    status: str
    model: str | None = None
    audio_uri: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Result stats (populated on completion)
    audio_duration_seconds: float | None = None
    result_language_code: str | None = None
    result_word_count: int | None = None
    result_segment_count: int | None = None
    result_speaker_count: int | None = None

    model_config = {"from_attributes": True}


class ConsoleJobListResponse(BaseModel):
    """Response for console job listing."""

    jobs: list[ConsoleJobSummary]
    cursor: str | None
    has_more: bool


def _encode_job_cursor(job: JobModel) -> str:
    """Encode a cursor from a job's created_at and id."""
    return f"{job.created_at.isoformat()}:{job.id}"


def _get_transcribe_engine(job: JobModel) -> str | None:
    """Extract the transcribe engine from a job's tasks.

    Returns the engine_id of the 'transcribe' stage task, or falls back
    to the explicitly requested engine in parameters.

    Handles both mono audio (stage='transcribe') and multi-channel audio
    (stage='transcribe_ch0', 'transcribe_ch1', etc.)
    """
    # First try to find the transcribe task
    if job.tasks:
        for task in job.tasks:
            # Match "transcribe" or "transcribe_ch0", "transcribe_ch1", etc.
            if task.stage == "transcribe" or task.stage.startswith("transcribe_ch"):
                return task.engine_id

    # Fallback to explicitly requested engine in parameters
    if job.parameters:
        return job.parameters.get("engine_transcribe")

    return None


def _decode_job_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    """Decode a cursor into created_at and id."""
    try:
        parts = cursor.rsplit(":", 1)
        if len(parts) != 2:
            return None
        created_at = datetime.fromisoformat(parts[0])
        job_id = UUID(parts[1])
        return created_at, job_id
    except (ValueError, TypeError):
        return None


@router.get(
    "/jobs",
    response_model=ConsoleJobListResponse,
    summary="List all jobs",
    description="List all jobs across all tenants (admin only).",
)
async def list_console_jobs(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    cursor: str | None = None,
    status: str | None = None,
    sort: Literal["created_desc", "created_asc"] = "created_desc",
) -> ConsoleJobListResponse:
    """List all jobs for console (admin view) with cursor-based pagination."""
    # Build base query - no tenant filter for admin
    # Eager load tasks to extract transcribe engine
    query = select(JobModel).options(selectinload(JobModel.tasks))

    # Optional status filter
    if status:
        query = query.where(JobModel.status == status)

    # Apply cursor filter
    if cursor:
        decoded = _decode_job_cursor(cursor)
        if decoded:
            cursor_created_at, cursor_id = decoded
            if sort == "created_asc":
                # Get jobs created after the cursor OR same time but with larger ID
                query = query.where(
                    (JobModel.created_at > cursor_created_at)
                    | (
                        (JobModel.created_at == cursor_created_at)
                        & (JobModel.id > cursor_id)
                    )
                )
            else:
                # Get jobs created before the cursor OR same time but with smaller ID
                query = query.where(
                    (JobModel.created_at < cursor_created_at)
                    | (
                        (JobModel.created_at == cursor_created_at)
                        & (JobModel.id < cursor_id)
                    )
                )

    # Fetch limit + 1 to determine has_more
    if sort == "created_asc":
        query = query.order_by(JobModel.created_at.asc(), JobModel.id.asc())
    else:
        query = query.order_by(JobModel.created_at.desc(), JobModel.id.desc())
    query = query.limit(limit + 1)
    result = await db.execute(query)
    jobs = list(result.scalars().all())

    has_more = len(jobs) > limit
    if has_more:
        jobs = jobs[:limit]

    # Next cursor is encoded from the last job
    next_cursor = _encode_job_cursor(jobs[-1]) if jobs and has_more else None

    return ConsoleJobListResponse(
        jobs=[
            ConsoleJobSummary(
                id=job.id,
                status=job.status,
                model=_get_transcribe_engine(job),
                audio_uri=job.audio_uri,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                audio_duration_seconds=job.audio_duration,
                result_language_code=job.result_language_code,
                result_word_count=job.result_word_count,
                result_segment_count=job.result_segment_count,
                result_speaker_count=job.result_speaker_count,
            )
            for job in jobs
        ],
        cursor=next_cursor,
        has_more=has_more,
    )


class ConsoleJobDetailResponse(BaseModel):
    """Detailed job response for console."""

    id: UUID
    status: str
    audio_uri: str | None = None
    parameters: dict | None = None
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.get(
    "/jobs/{job_id}",
    response_model=ConsoleJobDetailResponse,
    summary="Get job details",
    description="Get detailed job information (admin only).",
)
async def get_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> ConsoleJobDetailResponse:
    """Get job details for console (admin view)."""
    result = await db.execute(select(JobModel).where(JobModel.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return ConsoleJobDetailResponse(
        id=job.id,
        status=job.status,
        audio_uri=job.audio_uri,
        parameters=job.parameters,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="Delete a job",
    description="Delete a job and its artifacts. Only terminal-state jobs can be deleted. Admin only.",
    responses={
        204: {"description": "Job deleted successfully"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a terminal state"},
    },
)
async def delete_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> Response:
    """Delete a job and all associated artifacts (admin endpoint).

    No tenant filter — admins can delete any job.
    """
    try:
        job = await jobs_service.delete_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Clean up S3 artifacts (best-effort)
    try:
        storage = StorageService(settings)
        await storage.delete_job_artifacts(job_id)
    except Exception:
        logger.warning(
            "Failed to delete S3 artifacts for job %s", job_id, exc_info=True
        )

    return Response(status_code=204)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobCancelledResponse,
    summary="Cancel a job",
    description="Cancel a pending or running job. Running tasks complete naturally. Admin only.",
    responses={
        200: {"description": "Cancellation requested"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a cancellable state"},
    },
)
async def cancel_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobCancelledResponse:
    """Cancel a job (admin endpoint).

    No tenant filter — admins can cancel any job.
    """
    try:
        result = await jobs_service.cancel_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Publish event for orchestrator
    await publish_job_cancel_requested(redis, job_id)

    return JobCancelledResponse(
        id=result.job.id,
        status=result.status,
        message=result.message,
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobRetryResponse,
    summary="Retry a failed job",
    description="Retry a failed job. Resets it to PENDING and re-queues for processing. Admin only.",
    responses={
        200: {"description": "Job queued for retry"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a retryable state or max retries exceeded"},
    },
)
async def retry_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobRetryResponse:
    """Retry a failed job (admin endpoint).

    No tenant filter — admins can retry any job.
    """
    try:
        result = await jobs_service.retry_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Clean up stale S3 task artifacts (best-effort)
    try:
        storage = StorageService(settings)
        await storage.delete_job_task_artifacts(job_id)
    except Exception:
        logger.warning(
            "retry_s3_cleanup_failed",
            job_id=str(job_id),
            exc_info=True,
        )

    # Publish job.created event so orchestrator rebuilds the DAG
    await publish_job_created(redis, job_id)

    return JobRetryResponse(
        id=result.job.id,
        status=JobStatus(result.job.status),
        retry_count=result.job.retry_count,
        message=f"Job queued for retry (attempt {result.job.retry_count} of 3).",
    )


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


class NamespaceSummaryResponse(BaseModel):
    """Summary of a settings namespace."""

    namespace: str
    label: str
    description: str
    editable: bool
    setting_count: int
    has_overrides: bool


class SettingsNamespaceListResponse(BaseModel):
    """List of setting namespaces."""

    namespaces: list[NamespaceSummaryResponse]


class SettingResponse(BaseModel):
    """A single setting with resolved value."""

    key: str
    label: str
    description: str
    value_type: str
    value: Any
    default_value: Any
    is_overridden: bool
    env_var: str
    min_value: float | None = None
    max_value: float | None = None
    options: list[str] | None = None
    option_labels: list[str] | None = None


class NamespaceSettingsResponse(BaseModel):
    """All settings in a namespace."""

    namespace: str
    label: str
    description: str
    editable: bool
    settings: list[SettingResponse]
    updated_at: datetime | None = None


class UpdateSettingsRequest(BaseModel):
    """Request to update settings in a namespace."""

    settings: dict[str, Any]
    expected_updated_at: datetime | None = None


@router.get(
    "/settings",
    response_model=SettingsNamespaceListResponse,
    summary="List setting namespaces",
    description="List all setting namespaces with override status.",
)
async def list_settings_namespaces(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> SettingsNamespaceListResponse:
    """List all settings namespaces."""
    from dalston.gateway.services.settings import SettingsService

    service = SettingsService()
    summaries = await service.list_namespaces(db)
    return SettingsNamespaceListResponse(
        namespaces=[
            NamespaceSummaryResponse(
                namespace=s.namespace,
                label=s.label,
                description=s.description,
                editable=s.editable,
                setting_count=s.setting_count,
                has_overrides=s.has_overrides,
            )
            for s in summaries
        ]
    )


@router.get(
    "/settings/{namespace}",
    response_model=NamespaceSettingsResponse,
    summary="Get namespace settings",
    description="Get all settings in a namespace with current values and defaults.",
    responses={404: {"description": "Namespace not found"}},
)
async def get_settings_namespace(
    namespace: str,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Get settings for a namespace."""
    from dalston.gateway.services.settings import SettingsService

    service = SettingsService()
    ns = await service.get_namespace(db, namespace)
    if ns is None:
        raise HTTPException(status_code=404, detail=f"Unknown namespace: {namespace}")

    return NamespaceSettingsResponse(
        namespace=ns.namespace,
        label=ns.label,
        description=ns.description,
        editable=ns.editable,
        settings=[
            SettingResponse(
                key=s.key,
                label=s.label,
                description=s.description,
                value_type=s.value_type,
                value=s.value,
                default_value=s.default_value,
                is_overridden=s.is_overridden,
                env_var=s.env_var,
                min_value=s.min_value,
                max_value=s.max_value,
                options=s.options,
                option_labels=s.option_labels,
            )
            for s in ns.settings
        ],
        updated_at=ns.updated_at,
    )


@router.patch(
    "/settings/{namespace}",
    response_model=NamespaceSettingsResponse,
    summary="Update namespace settings",
    description="Update one or more settings in a namespace. Requires admin scope.",
    responses={
        400: {"description": "Validation error"},
        404: {"description": "Namespace not found"},
        409: {"description": "Optimistic locking conflict"},
    },
)
async def update_settings_namespace(
    namespace: str,
    body: UpdateSettingsRequest,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Update settings in a namespace."""
    from dalston.gateway.services.settings import ConflictError, SettingsService

    service = SettingsService()

    try:
        result = await service.update_namespace(
            db=db,
            namespace=namespace,
            updates=body.settings,
            updated_by=api_key.id,
            expected_updated_at=body.expected_updated_at,
        )
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    ns = result.namespace_settings

    # Audit log
    if result.old_values:
        try:
            from dalston.gateway.dependencies import get_audit_service

            audit = get_audit_service()
            changes = {}
            for key, old_val in result.old_values.items():
                new_val = body.settings.get(key)
                if old_val != new_val:
                    changes[key] = {"old": old_val, "new": new_val}

            if changes:
                await audit.log(
                    action="settings.updated",
                    resource_type="settings",
                    resource_id=namespace,
                    tenant_id=api_key.tenant_id,
                    actor_type="api_key",
                    actor_id=str(api_key.id),
                    detail={"changes": changes},
                )
        except Exception:
            logger.warning("Failed to audit settings change", exc_info=True)

    return NamespaceSettingsResponse(
        namespace=ns.namespace,
        label=ns.label,
        description=ns.description,
        editable=ns.editable,
        settings=[
            SettingResponse(
                key=s.key,
                label=s.label,
                description=s.description,
                value_type=s.value_type,
                value=s.value,
                default_value=s.default_value,
                is_overridden=s.is_overridden,
                env_var=s.env_var,
                min_value=s.min_value,
                max_value=s.max_value,
                options=s.options,
                option_labels=s.option_labels,
            )
            for s in ns.settings
        ],
        updated_at=ns.updated_at,
    )


@router.post(
    "/settings/{namespace}/reset",
    response_model=NamespaceSettingsResponse,
    summary="Reset namespace to defaults",
    description="Delete all DB overrides, reverting to environment variable defaults.",
    responses={
        400: {"description": "Namespace is read-only"},
        404: {"description": "Namespace not found"},
    },
)
async def reset_settings_namespace(
    namespace: str,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Reset settings namespace to defaults."""
    from dalston.gateway.services.settings import SettingsService

    service = SettingsService()

    try:
        result = await service.reset_namespace(db, namespace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    ns = result.namespace_settings

    # Audit log
    if result.old_values:
        try:
            from dalston.gateway.dependencies import get_audit_service

            audit = get_audit_service()
            await audit.log(
                action="settings.reset",
                resource_type="settings",
                resource_id=namespace,
                tenant_id=api_key.tenant_id,
                actor_type="api_key",
                actor_id=str(api_key.id),
                detail={"reset_keys": list(result.old_values.keys())},
            )
        except Exception:
            logger.warning("Failed to audit settings reset", exc_info=True)

    return NamespaceSettingsResponse(
        namespace=ns.namespace,
        label=ns.label,
        description=ns.description,
        editable=ns.editable,
        settings=[
            SettingResponse(
                key=s.key,
                label=s.label,
                description=s.description,
                value_type=s.value_type,
                value=s.value,
                default_value=s.default_value,
                is_overridden=s.is_overridden,
                env_var=s.env_var,
                min_value=s.min_value,
                max_value=s.max_value,
                options=s.options,
                option_labels=s.option_labels,
            )
            for s in ns.settings
        ],
        updated_at=ns.updated_at,
    )
