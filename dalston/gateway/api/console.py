"""Console API endpoints for the web management interface.

GET /api/console/dashboard - Aggregated dashboard data
GET /api/console/metrics - Key operational metrics for dashboard charts
GET /api/console/jobs/{job_id}/tasks - Get task DAG for a job
GET /api/console/engines - Get batch and realtime engine status
GET /api/console/nodes - Infrastructure topology (engines grouped by node)
DELETE /api/console/jobs/{job_id} - Delete a job and its artifacts (admin)
GET /api/console/settings - List setting namespaces
GET /api/console/settings/{namespace} - Get settings in a namespace
PATCH /api/console/settings/{namespace} - Update settings
POST /api/console/settings/{namespace}/reset - Reset to defaults
"""

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audit import AuditService
from dalston.common.events import publish_job_cancel_requested
from dalston.common.models import JobStatus, TaskStatus
from dalston.common.registry import (
    UNIFIED_INSTANCE_KEY_PREFIX,
    UNIFIED_INSTANCE_SET_KEY,
    _parse_datetime,
    _parse_json_list,
)
from dalston.common.streams_types import CONSUMER_GROUP
from dalston.common.utils import compute_duration_ms, compute_interval_union_ms
from dalston.db.models import TaskModel
from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.dependencies import (
    get_audit_service,
    get_console_service,
    get_db,
    get_jobs_service,
    get_principal,
    get_redis,
    get_security_manager,
    get_session_router,
    get_storage_service,
)
from dalston.gateway.error_codes import Err
from dalston.gateway.models.responses import JobCancelledResponse
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.console import ConsoleService, normalize_stage
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService
from dalston.orchestrator.session_coordinator import SessionCoordinator

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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    session_router: SessionCoordinator = Depends(get_session_router),
    console_service: ConsoleService = Depends(get_console_service),
) -> DashboardResponse:
    """Get aggregated dashboard data in a single call."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    stats = await console_service.get_dashboard_stats(db)

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
            running_jobs=stats.status_counts.get(JobStatus.RUNNING.value, 0),
            queued_jobs=stats.status_counts.get(JobStatus.PENDING.value, 0),
            completed_today=stats.completed_today,
            failed_today=stats.failed_today,
        ),
        realtime=realtime,
        recent_jobs=[
            JobSummary(
                id=dto.id,
                status=dto.status,
                created_at=dto.created_at,
                started_at=dto.started_at,
                completed_at=dto.completed_at,
            )
            for dto in stats.recent_jobs
        ],
    )


class TaskResponse(BaseModel):
    """Task in the job pipeline."""

    id: UUID
    stage: str
    engine_id: str
    status: str
    dependencies: list[UUID]
    ready_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    wait_ms: int | None = None
    duration_ms: int | None = None
    error: str | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Response for task list endpoint."""

    job_id: UUID
    tasks: list[TaskResponse]
    total_wait_ms: int | None = None


@router.get(
    "/jobs/{job_id}/tasks",
    response_model=TaskListResponse,
    summary="Get job tasks",
    description="Get all tasks in the job's processing pipeline.",
)
async def get_job_tasks(
    job_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    console_service: ConsoleService = Depends(get_console_service),
) -> TaskListResponse:
    """Get task DAG for a job."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    job_dto = await console_service.get_job_with_tasks_admin(
        db, job_id, DEFAULT_TENANT_ID
    )

    if job_dto is None:
        raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

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
        job_dto.tasks,
        key=lambda t: (stage_order.get(t.stage, 99), t.engine_id),
    )

    return TaskListResponse(
        job_id=job_dto.id,
        tasks=[
            TaskResponse(
                id=task.id,
                stage=task.stage,
                engine_id=task.engine_id,
                status=task.status,
                dependencies=task.dependencies,
                ready_at=task.ready_at,
                started_at=task.started_at,
                completed_at=task.completed_at,
                wait_ms=compute_duration_ms(task.ready_at, task.started_at),
                duration_ms=compute_duration_ms(task.started_at, task.completed_at),
                error=task.error,
            )
            for task in sorted_tasks
        ],
        total_wait_ms=compute_interval_union_ms(
            (task.ready_at, task.started_at) for task in sorted_tasks
        ),
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
    request: dict | None = None
    response: dict | None = None


@router.get(
    "/jobs/{job_id}/tasks/{task_id}/artifacts",
    response_model=TaskArtifactResponse,
    summary="Get task artifacts",
    description="Get detailed task information including input/output artifacts.",
)
async def get_task_artifacts(
    job_id: UUID,
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    console_service: ConsoleService = Depends(get_console_service),
    storage: StorageService = Depends(get_storage_service),
) -> TaskArtifactResponse:
    """Get task artifacts for debugging."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    job_dto = await console_service.get_job_with_tasks_admin(db, job_id)

    if job_dto is None:
        raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

    # Find the task
    task = next((t for t in job_dto.tasks if t.id == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail=Err.TASK_NOT_FOUND)

    # Calculate duration
    duration_ms = None
    if task.started_at and task.completed_at:
        delta = task.completed_at - task.started_at
        duration_ms = int(delta.total_seconds() * 1000)

    # Fetch artifacts from S3 if task has started
    request_data = None
    response_data = None
    if task.status != "pending":
        request_data = await storage.get_task_request(job_id, task_id)
        response_data = await storage.get_task_response(job_id, task_id)

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
        dependencies=task.dependencies,
        request=request_data,
        response=response_data,
    )


# Engine models
class BatchEngine(BaseModel):
    """Batch engine status."""

    engine_id: str
    stage: str
    status: Literal[
        "idle", "processing", "loading", "downloading", "error", "offline", "stale"
    ]
    queue_depth: int
    processing: int
    interfaces: list[str] = ["batch", "realtime"]


class VocabularySupportResponse(BaseModel):
    """Vocabulary boosting capability for an engine."""

    method: str = "none"
    batch: bool = False
    realtime: bool = False


class RealtimeWorker(BaseModel):
    """Realtime worker status."""

    instance: str
    endpoint: str
    status: Literal["ready", "busy", "draining", "offline", "unhealthy"]
    capacity: int
    active_sessions: int
    models: list[str]  # M43: Currently loaded models (dynamic)
    engine_id: str | None = None  # M43: Model engine_id (e.g., "faster-whisper")
    vocabulary_support: VocabularySupportResponse | None = None
    interfaces: list[str] = ["batch", "realtime"]


class EnginesResponse(BaseModel):
    """Response for engines endpoint."""

    batch_engines: list[BatchEngine]
    realtime_engines: list[RealtimeWorker]


# Heartbeat timeout thresholds (seconds)
HEARTBEAT_STALE_THRESHOLD = 30  # Mark as stale after 30s without heartbeat


@router.get(
    "/engines",
    response_model=EnginesResponse,
    summary="Get engine status",
    description="Get status of all batch and realtime engines.",
)
async def get_engines(
    principal: Annotated[Principal, Depends(get_principal)],
    redis: Redis = Depends(get_redis),
    session_router: SessionCoordinator = Depends(get_session_router),
) -> EnginesResponse:
    """Get status of all engines.

    Uses the engine catalog as the source of truth for which engines exist,
    combined with Redis heartbeats for live status.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)
    from dalston.orchestrator.catalog import get_catalog

    catalog = get_catalog()

    # Fetch heartbeats for all registered instances from unified registry
    all_instance_ids = await redis.smembers(UNIFIED_INSTANCE_SET_KEY)
    discovered_heartbeats: dict[str, list[dict[str, str]]] = {}

    for instance_id in all_instance_ids:
        data = await redis.hgetall(f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance_id}")
        if data and "engine_id" in data:
            engine_id = data["engine_id"]
            discovered_heartbeats.setdefault(engine_id, []).append(data)

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
        interfaces = ["batch", "realtime"]  # All engines are unified
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
                    # Unified engines may report realtime statuses —
                    # normalise to batch-compatible values.
                    if instance_status in ("ready", "busy"):
                        instance_status = (
                            "idle" if instance_status == "ready" else "processing"
                        )
                    if instance_status == "processing":
                        best_status = "processing"
                    elif best_status != "processing":
                        best_status = instance_status

                    if heartbeat.get("current_task"):
                        total_processing += 1

                # Pick up interfaces from heartbeat if available
                hb_interfaces = heartbeat.get("interfaces")
                if hb_interfaces:
                    try:
                        import json

                        interfaces = json.loads(hb_interfaces)
                    except (json.JSONDecodeError, TypeError):
                        pass

            status = best_status
            processing = total_processing

        batch_engines.append(
            BatchEngine(
                engine_id=engine_id,
                stage=stage,
                status=status,
                queue_depth=queue_depth,
                processing=processing,
                interfaces=interfaces,
            )
        )

    # Get realtime workers and track which engine types have running workers
    realtime_engines = []
    running_engine_types: set[str] = set()

    try:
        workers = await session_router.list_workers()
        for worker in workers:
            running_engine_types.add(worker.engine_id or "unknown")
            vocab_resp = None
            if worker.vocabulary_support is not None:
                vocab_resp = VocabularySupportResponse(
                    method=worker.vocabulary_support.method.value,
                    batch=worker.vocabulary_support.batch,
                    realtime=worker.vocabulary_support.realtime,
                )
            realtime_engines.append(
                RealtimeWorker(
                    instance=worker.instance,
                    endpoint=worker.endpoint,
                    status=worker.status,
                    capacity=worker.capacity,
                    active_sessions=worker.active_sessions,
                    models=worker.models,  # M43: Dynamically loaded models
                    engine_id=worker.engine_id,  # M43: Model engine_id
                    vocabulary_support=vocab_resp,
                    interfaces=["batch", "realtime"],
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
                    instance=f"{engine_id} (offline)",
                    endpoint="",
                    status="offline",
                    capacity=entry.capabilities.max_concurrency or 4,
                    active_sessions=0,
                    models=[],
                    engine_id=entry.capabilities.engine_id,  # M43: Model engine_id
                    interfaces=["batch", "realtime"],
                )
            )

    return EnginesResponse(
        batch_engines=batch_engines,
        realtime_engines=realtime_engines,
    )


# ---------------------------------------------------------------------------
# Infrastructure topology (M78)
# ---------------------------------------------------------------------------


def _safe_int(value: str | None, default: int) -> int:
    """Parse an integer string, returning *default* on any failure."""
    try:
        return int(value or default)
    except (ValueError, TypeError):
        return default


_GB_PATTERN = re.compile(r"([\d.]+)\s*GB", re.IGNORECASE)


def _parse_gb(value: str) -> float:
    """Parse a GPU memory string like "4.2GB" to a float. Returns 0.0 on failure."""
    m = _GB_PATTERN.match(value.strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0


class NodeEngine(BaseModel):
    """Engine instance on a node."""

    instance: str
    engine_id: str
    stage: str
    status: str
    capacity: int
    active_batch: int
    active_realtime: int
    gpu_memory_used: str
    gpu_memory_total: str
    interfaces: list[str]
    loaded_model: str | None
    is_healthy: bool


class NodeView(BaseModel):
    """A compute node hosting one or more engine instances."""

    node_id: str
    hostname: str
    deploy_env: str
    aws_az: str | None
    aws_instance_type: str | None
    engine_count: int
    engines: list[NodeEngine]
    gpu_memory_used_gb: float
    gpu_memory_total_gb: float


class NodesResponse(BaseModel):
    """Response for infrastructure topology endpoint."""

    nodes: list[NodeView]


@router.get(
    "/nodes",
    response_model=NodesResponse,
    summary="Get infrastructure topology",
    description="Engine instances grouped by compute node.",
)
async def get_nodes(
    principal: Annotated[Principal, Depends(get_principal)],
    redis: Redis = Depends(get_redis),
) -> NodesResponse:
    """Get infrastructure topology — engines grouped by node."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    all_instance_ids = await redis.smembers(UNIFIED_INSTANCE_SET_KEY)
    nodes: dict[str, dict[str, Any]] = {}
    now = datetime.now(UTC)

    for instance_id in all_instance_ids:
        data = await redis.hgetall(f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance_id}")
        if not data or "engine_id" not in data:
            continue

        node_id = data.get("node_id")
        if not node_id:
            continue  # skip pre-M78 engines without node identity

        # Determine health from heartbeat freshness
        is_healthy = False
        parsed_hb = _parse_datetime(data.get("last_heartbeat"))
        if parsed_hb:
            is_healthy = (now - parsed_hb).total_seconds() < HEARTBEAT_STALE_THRESHOLD

        interfaces = _parse_json_list(data.get("interfaces")) or ["batch"]

        engine = NodeEngine(
            instance=data.get("instance", instance_id),
            engine_id=data["engine_id"],
            stage=data.get("stage", "unknown"),
            status=data.get("status", "offline"),
            capacity=_safe_int(data.get("capacity", "1"), 1),
            active_batch=_safe_int(data.get("active_batch", "0"), 0),
            active_realtime=_safe_int(data.get("active_realtime", "0"), 0),
            gpu_memory_used=data.get("gpu_memory_used", "0GB"),
            gpu_memory_total=data.get("gpu_memory_total", "0GB"),
            interfaces=interfaces,
            loaded_model=data.get("loaded_model") or None,
            is_healthy=is_healthy,
        )

        if node_id not in nodes:
            nodes[node_id] = {
                "node_id": node_id,
                "hostname": data.get("hostname", ""),
                "deploy_env": data.get("deploy_env", "local"),
                "aws_az": data.get("aws_az") or None,
                "aws_instance_type": data.get("aws_instance_type") or None,
                "engines": [],
            }
        nodes[node_id]["engines"].append(engine)

    node_views = []
    for node_data in nodes.values():
        engines: list[NodeEngine] = node_data["engines"]
        used_gb = max((_parse_gb(e.gpu_memory_used) for e in engines), default=0.0)
        total_gb = max((_parse_gb(e.gpu_memory_total) for e in engines), default=0.0)
        node_views.append(
            NodeView(
                node_id=node_data["node_id"],
                hostname=node_data["hostname"],
                deploy_env=node_data["deploy_env"],
                aws_az=node_data["aws_az"],
                aws_instance_type=node_data["aws_instance_type"],
                engine_count=len(engines),
                engines=engines,
                gpu_memory_used_gb=round(used_gb, 2),
                gpu_memory_total_gb=round(total_gb, 2),
            )
        )

    # Sort: AWS nodes first, then by hostname
    node_views.sort(key=lambda n: (0 if n.deploy_env == "aws" else 1, n.hostname))

    return NodesResponse(nodes=node_views)


# Job listing for console
class ConsoleJobSummary(BaseModel):
    """Job summary for console listing."""

    id: UUID
    status: str
    display_name: str | None = None
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


@router.get(
    "/jobs",
    response_model=ConsoleJobListResponse,
    summary="List all jobs",
    description="List all jobs across all tenants (admin only).",
)
async def list_console_jobs(
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    console_service: ConsoleService = Depends(get_console_service),
    limit: int = 20,
    cursor: str | None = None,
    status: str | None = None,
    sort: Literal["created_desc", "created_asc"] = "created_desc",
) -> ConsoleJobListResponse:
    """List all jobs for console (admin view) with cursor-based pagination."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    try:
        jobs, next_cursor, has_more = await console_service.list_jobs_admin(
            db, limit=limit, cursor=cursor, status=status, sort=sort
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=Err.INVALID_CURSOR_FORMAT) from None

    return ConsoleJobListResponse(
        jobs=[
            ConsoleJobSummary(
                id=dto.id,
                status=dto.status,
                display_name=dto.display_name,
                model=dto.model,
                audio_uri=dto.audio_uri,
                created_at=dto.created_at,
                started_at=dto.started_at,
                completed_at=dto.completed_at,
                audio_duration_seconds=dto.audio_duration_seconds,
                result_language_code=dto.result_language_code,
                result_word_count=dto.result_word_count,
                result_segment_count=dto.result_segment_count,
                result_speaker_count=dto.result_speaker_count,
            )
            for dto in jobs
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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    console_service: ConsoleService = Depends(get_console_service),
) -> ConsoleJobDetailResponse:
    """Get job details for console (admin view)."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    job_dto = await console_service.get_job_admin(db, job_id)

    if job_dto is None:
        raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

    return ConsoleJobDetailResponse(
        id=job_dto.id,
        status=job_dto.status,
        audio_uri=job_dto.audio_uri,
        parameters=job_dto.parameters,
        result=job_dto.result,
        error=job_dto.error,
        created_at=job_dto.created_at,
        started_at=job_dto.started_at,
        completed_at=job_dto.completed_at,
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
    request: Request,
    job_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
    storage: StorageService = Depends(get_storage_service),
) -> Response:
    """Delete a job and all associated artifacts (admin endpoint).

    No tenant filter — admins can delete any job.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)
    request_id = getattr(request.state, "request_id", None)

    try:
        job = await jobs_service.delete_job(
            db,
            job_id,
            audit_service=audit_service,
            actor_type=principal.actor_type,
            actor_id=principal.actor_id,
            correlation_id=request_id,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if job is None:
        raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

    # Clean up S3 artifacts (best-effort)
    try:
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
    request: Request,
    job_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> JobCancelledResponse:
    """Cancel a job (admin endpoint).

    No tenant filter — admins can cancel any job.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)
    try:
        result = await jobs_service.cancel_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail=Err.JOB_NOT_FOUND)

    # Publish event for orchestrator
    await publish_job_cancel_requested(redis, job_id)

    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_job_cancel_requested(
        job_id=job_id,
        tenant_id=result.job.tenant_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
    )

    return JobCancelledResponse(
        id=result.job.id,
        status=result.status,
        message=result.message,
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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
) -> SettingsNamespaceListResponse:
    """List all settings namespaces."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SETTINGS_READ)
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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Get settings for a namespace."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SETTINGS_READ)
    from dalston.gateway.services.settings import SettingsService

    service = SettingsService()
    ns = await service.get_namespace(db, namespace)
    if ns is None:
        raise HTTPException(
            status_code=404, detail=Err.NAMESPACE_NOT_FOUND.format(namespace=namespace)
        )

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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Update settings in a namespace."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SETTINGS_WRITE)

    from dalston.gateway.services.settings import ConflictError, SettingsService

    service = SettingsService()

    try:
        result = await service.update_namespace(
            db=db,
            namespace=namespace,
            updates=body.settings,
            updated_by=principal.id,
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
                    tenant_id=principal.tenant_id,
                    actor_type=principal.actor_type,
                    actor_id=principal.actor_id,
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
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
) -> NamespaceSettingsResponse:
    """Reset settings namespace to defaults."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SETTINGS_WRITE)

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
                tenant_id=principal.tenant_id,
                actor_type=principal.actor_type,
                actor_id=principal.actor_id,
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


# ---------------------------------------------------------------------------
# Metrics endpoints
# ---------------------------------------------------------------------------


class ThroughputBucket(BaseModel):
    """Hourly job count for throughput chart."""

    hour: str  # ISO 8601 hour start, e.g. "2026-02-25T14:00:00+00:00"
    completed: int
    failed: int


class SuccessRate(BaseModel):
    """Job success rate over a time window."""

    window: str  # e.g. "1h", "24h"
    total: int
    completed: int
    failed: int
    rate: float  # 0.0 – 1.0


class EngineMetric(BaseModel):
    """Per-engine performance summary."""

    engine_id: str
    stage: str
    completed: int
    failed: int
    avg_duration_ms: float | None
    p95_duration_ms: float | None
    queue_depth: int


class MetricsResponse(BaseModel):
    """Key operational metrics for the dashboard."""

    throughput: list[ThroughputBucket]
    success_rates: list[SuccessRate]
    total_audio_minutes: float
    total_jobs_all_time: int
    engines: list[EngineMetric]
    grafana_url: str | None


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Get operational metrics",
    description="Key operational metrics for the web console dashboard.",
)
async def get_metrics(
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    console_service: ConsoleService = Depends(get_console_service),
) -> MetricsResponse:
    """Return key metrics for the web console.

    Queries the database for job/task statistics and Redis for queue depths.
    Designed to be called on a polling interval (e.g. 30s) by the frontend.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    # Get metrics from service
    throughput_data = await console_service.get_hourly_throughput(db)
    success_rate_data = await console_service.get_success_rates(db)
    total_audio_minutes = await console_service.get_total_audio_minutes(db)
    total_jobs_all_time = await console_service.get_total_jobs_count(db)

    # Convert service dataclasses to API response models
    throughput = [
        ThroughputBucket(hour=b.hour, completed=b.completed, failed=b.failed)
        for b in throughput_data
    ]
    success_rates = [
        SuccessRate(
            window=r.window,
            total=r.total,
            completed=r.completed,
            failed=r.failed,
            rate=r.rate,
        )
        for r in success_rate_data
    ]

    # -- Per-engine metrics ------------------------------------------------
    from dalston.orchestrator.catalog import get_catalog

    catalog = get_catalog()
    engine_metrics: list[EngineMetric] = []

    for entry in catalog.get_all_engines():
        if not entry.capabilities.stages:
            continue  # skip realtime engines

        engine_id = entry.engine_id
        stage = entry.capabilities.stages[0]

        # Task stats from DB (last 24h)
        stats = await console_service.get_engine_task_stats(db, engine_id)

        # Queue depth from Redis
        stream_key = f"dalston:stream:{engine_id}"
        queue_depth = await _get_stream_backlog(redis, stream_key)

        engine_metrics.append(
            EngineMetric(
                engine_id=engine_id,
                stage=stage,
                completed=stats.completed,
                failed=stats.failed,
                avg_duration_ms=stats.avg_duration_ms,
                p95_duration_ms=stats.p95_duration_ms,
                queue_depth=queue_depth,
            )
        )

    # Sort engines by pipeline stage order, then alphabetically by engine_id
    _STAGE_ORDER = {
        "prepare": 0,
        "transcribe": 1,
        "align": 2,
        "diarize": 3,
        "pii_detect": 4,
        "audio_redact": 5,
    }
    engine_metrics.sort(key=lambda e: (_STAGE_ORDER.get(e.stage, 99), e.engine_id))

    # Grafana URL from environment (optional)
    grafana_url = os.environ.get("DALSTON_GRAFANA_URL")

    return MetricsResponse(
        throughput=throughput,
        success_rates=success_rates,
        total_audio_minutes=round(total_audio_minutes, 1),
        total_jobs_all_time=total_jobs_all_time,
        engines=engine_metrics,
        grafana_url=grafana_url,
    )


class QueueBoardTask(BaseModel):
    """Single task entry in the queue board flat list."""

    task_id: UUID
    job_id: UUID
    stage: str
    status: str
    engine_id: str | None = None
    duration_ms: int | None = None
    wait_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class QueueBoardJob(BaseModel):
    """Active job entry in the queue board."""

    job_id: UUID
    display_name: str | None = None
    status: str
    created_at: datetime
    audio_duration_seconds: float | None = None


class QueueBoardStageHealth(BaseModel):
    """Aggregated queue health for a single visible stage."""

    stage: str
    queue_depth: int
    processing: int
    total_workers: int
    avg_duration_ms: float | None = None


class QueueBoardResponse(BaseModel):
    """Aggregated queue board data for the operational monitoring page."""

    jobs: list[QueueBoardJob]
    tasks: list[QueueBoardTask]
    visible_stages: list[str]
    hidden_stages: list[str]
    stages: list[QueueBoardStageHealth]
    completed_last_hour: int
    avg_pipeline_ms: float | None = None


@router.get(
    "/queue-board",
    response_model=QueueBoardResponse,
    summary="Get queue board data",
    description=(
        "Return all active batch jobs with their tasks (flat list), plus "
        "per-stage queue health and pipeline-level summary stats. Designed "
        "for a cross-job operational view polled every 2 seconds."
    ),
)
async def get_queue_board(
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    console_service: ConsoleService = Depends(get_console_service),
) -> QueueBoardResponse:
    """Return the data feeding the Queue Board page.

    Combines a single database query (active jobs + eagerly-loaded tasks)
    with per-stage Redis queue depth aggregation, so the frontend can
    regroup the flat task list into any of the three supported layouts
    (Grid, Stage Board, Job Strips) without a follow-up fetch.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.CONSOLE_ACCESS)

    board = await console_service.get_queue_board(db)

    from dalston.orchestrator.catalog import get_catalog

    catalog = get_catalog()
    visible_set = set(board.visible_stages)

    all_instance_ids = list(await redis.smembers(UNIFIED_INSTANCE_SET_KEY))
    instance_hashes = await asyncio.gather(
        *(
            redis.hgetall(f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance_id}")
            for instance_id in all_instance_ids
        )
    )
    heartbeats_by_engine: dict[str, list[dict[str, str]]] = {}
    for data in instance_hashes:
        if data and "engine_id" in data:
            heartbeats_by_engine.setdefault(data["engine_id"], []).append(data)

    now = datetime.now(UTC)
    stage_totals: dict[str, QueueBoardStageHealth] = {
        stage: QueueBoardStageHealth(
            stage=stage,
            queue_depth=0,
            processing=0,
            total_workers=0,
            avg_duration_ms=None,
        )
        for stage in board.visible_stages
    }

    # Multi-stage engines (e.g. whisperx-full) register against their primary
    # stage at `stages[0]`, matching the /engines endpoint behaviour.
    visible_engines: list[tuple[str, str]] = []  # (stage, engine_id)
    for entry in catalog.get_all_engines():
        if not entry.capabilities.stages:
            continue
        stage = entry.capabilities.stages[0]
        if stage in visible_set:
            visible_engines.append((stage, entry.engine_id))

    backlogs = await asyncio.gather(
        *(
            _get_stream_backlog(redis, f"dalston:stream:{engine_id}")
            for _, engine_id in visible_engines
        )
    )

    for (stage, engine_id), queue_depth in zip(visible_engines, backlogs, strict=True):
        totals = stage_totals[stage]
        totals.queue_depth += queue_depth
        for heartbeat in heartbeats_by_engine.get(engine_id, []):
            try:
                last_seen = datetime.fromisoformat(heartbeat["last_heartbeat"])
                age = (now - last_seen).total_seconds()
            except (KeyError, ValueError):
                continue
            if age > HEARTBEAT_STALE_THRESHOLD:
                continue
            totals.total_workers += 1
            if heartbeat.get("current_task"):
                totals.processing += 1

    if visible_set:
        hour_ago = now - timedelta(hours=1)
        stage_avg_query = (
            select(
                TaskModel.stage,
                func.count(TaskModel.id).label("row_count"),
                func.avg(
                    func.extract("epoch", TaskModel.completed_at - TaskModel.started_at)
                ).label("avg_seconds"),
            )
            .where(
                TaskModel.status == TaskStatus.COMPLETED.value,
                TaskModel.completed_at >= hour_ago,
                TaskModel.started_at.isnot(None),
                TaskModel.completed_at.isnot(None),
            )
            .group_by(TaskModel.stage)
        )
        stage_avg_rows = (await db.execute(stage_avg_query)).all()
        # Weighted-accumulate per normalized stage so channel-suffixed rows
        # (e.g. transcribe_ch0 + transcribe_ch1) fold into their base stage
        # without overwriting each other.
        accum: dict[str, tuple[int, float]] = {}
        for row in stage_avg_rows:
            if row.avg_seconds is None:
                continue
            count = int(row.row_count or 0)
            if count == 0:
                continue
            normalized = normalize_stage(row.stage)
            if normalized not in stage_totals:
                continue
            prev_count, prev_sum = accum.get(normalized, (0, 0.0))
            accum[normalized] = (
                prev_count + count,
                prev_sum + count * float(row.avg_seconds),
            )
        for normalized, (count, weighted_sum) in accum.items():
            stage_totals[normalized].avg_duration_ms = round(
                (weighted_sum / count) * 1000, 1
            )

    stages_health = [stage_totals[s] for s in board.visible_stages]

    return QueueBoardResponse(
        jobs=[
            QueueBoardJob(
                job_id=job.job_id,
                display_name=job.display_name,
                status=job.status,
                created_at=job.created_at,
                audio_duration_seconds=job.audio_duration_seconds,
            )
            for job in board.jobs
        ],
        tasks=[
            QueueBoardTask(
                task_id=task.task_id,
                job_id=task.job_id,
                stage=task.stage,
                status=task.status,
                engine_id=task.engine_id,
                duration_ms=task.duration_ms,
                wait_ms=task.wait_ms,
                started_at=task.started_at,
                completed_at=task.completed_at,
                error=task.error,
            )
            for task in board.tasks
        ],
        visible_stages=board.visible_stages,
        hidden_stages=board.hidden_stages,
        stages=stages_health,
        completed_last_hour=board.completed_last_hour,
        avg_pipeline_ms=board.avg_pipeline_ms,
    )
