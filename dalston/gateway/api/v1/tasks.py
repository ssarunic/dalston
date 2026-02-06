"""Task observability API endpoints.

GET /v1/audio/transcriptions/{job_id}/tasks - List tasks for a job
GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts - Get task artifacts
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.utils import compute_duration_ms
from dalston.config import Settings
from dalston.gateway.dependencies import (
    RequireJobsRead,
    get_db,
    get_jobs_service,
    get_settings,
)
from dalston.gateway.models.responses import (
    TaskArtifactResponse,
    TaskListResponse,
    TaskResponse,
)
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/audio/transcriptions", tags=["tasks"])


@router.get(
    "/{job_id}/tasks",
    response_model=TaskListResponse,
    summary="List tasks for a job",
    description="List all tasks in a job's pipeline with their dependencies and status.",
)
async def list_job_tasks(
    job_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> TaskListResponse:
    """List all tasks for a job in topological order.

    Returns task metadata including dependencies for pipeline visualization.
    """
    # Verify job exists and belongs to tenant
    job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Fetch tasks in topological order
    tasks = await jobs_service.get_job_tasks(db, job_id, tenant_id=api_key.tenant_id)

    return TaskListResponse(
        job_id=job_id,
        tasks=[
            TaskResponse(
                task_id=task.id,
                stage=task.stage,
                engine_id=task.engine_id,
                status=task.status,
                required=task.required,
                dependencies=list(task.dependencies) if task.dependencies else [],
                started_at=task.started_at,
                completed_at=task.completed_at,
                duration_ms=compute_duration_ms(task.started_at, task.completed_at),
                retries=task.retries,
                error=task.error,
            )
            for task in tasks
        ],
    )


@router.get(
    "/{job_id}/tasks/{task_id}/artifacts",
    response_model=TaskArtifactResponse,
    summary="Get task artifacts",
    description="Get the raw input and output data for a specific task.",
    responses={
        400: {"description": "Task has not started yet"},
        404: {"description": "Job or task not found"},
    },
)
async def get_task_artifacts(
    job_id: UUID,
    task_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> TaskArtifactResponse:
    """Get the input and output artifacts for a specific task.

    Fetches the raw JSON data that was passed to and produced by the engine.
    """
    # Fetch task (verifies job exists and belongs to tenant)
    task = await jobs_service.get_task(db, job_id, task_id, tenant_id=api_key.tenant_id)

    if task is None:
        # Check if job exists to give appropriate error
        job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "job_not_found", "message": "Job not found"},
            )
        raise HTTPException(
            status_code=404,
            detail={"code": "task_not_found", "message": "Task not found"},
        )

    # Check if task has started (artifacts only exist after task starts)
    if task.status == "pending":
        raise HTTPException(
            status_code=400,
            detail={"code": "no_artifacts", "message": "Task has not started yet"},
        )

    # Fetch artifacts from S3
    storage = StorageService(settings)
    input_data = await storage.get_task_input(job_id, task_id)
    output_data = await storage.get_task_output(job_id, task_id)

    return TaskArtifactResponse(
        task_id=task.id,
        job_id=job_id,
        stage=task.stage,
        engine_id=task.engine_id,
        status=task.status,
        input=input_data,
        output=output_data,
    )
