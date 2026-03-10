"""Real-time system status and worker management endpoints.

GET /v1/realtime/status - System capacity
GET /v1/realtime/workers - List workers
GET /v1/realtime/workers/{instance} - Get worker status
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dalston.gateway.dependencies import (
    get_principal,
    get_security_manager,
    get_session_router,
)
from dalston.gateway.error_codes import Err
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.orchestrator.session_coordinator import SessionCoordinator

router = APIRouter(prefix="/realtime", tags=["realtime"])


class RealtimeStatusResponse(BaseModel):
    """Real-time system status."""

    status: str
    total_capacity: int
    active_sessions: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class WorkerStatusResponse(BaseModel):
    """Worker status."""

    instance: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]


class WorkersListResponse(BaseModel):
    """List of workers."""

    workers: list[WorkerStatusResponse]
    total: int


@router.get(
    "/status",
    response_model=RealtimeStatusResponse,
    summary="Get realtime system status",
    description="Get capacity and availability information for real-time transcription.",
)
async def get_realtime_status(
    principal: Annotated[Principal, Depends(get_principal)],
    session_router: SessionCoordinator = Depends(get_session_router),
) -> RealtimeStatusResponse:
    """Get real-time transcription system status."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SESSION_READ)

    capacity = await session_router.get_capacity()

    # Determine overall status
    if capacity.ready_workers == 0:
        status = "unavailable"
    elif capacity.available_capacity == 0:
        status = "at_capacity"
    else:
        status = "ready"

    return RealtimeStatusResponse(
        status=status,
        total_capacity=capacity.total_capacity,
        active_sessions=capacity.used_capacity,
        available_capacity=capacity.available_capacity,
        worker_count=capacity.worker_count,
        ready_workers=capacity.ready_workers,
    )


@router.get(
    "/workers",
    response_model=WorkersListResponse,
    summary="List realtime workers",
    description="List all registered real-time transcription workers.",
)
async def list_realtime_workers(
    principal: Annotated[Principal, Depends(get_principal)],
    session_router: SessionCoordinator = Depends(get_session_router),
) -> WorkersListResponse:
    """List all real-time workers."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SESSION_READ)

    workers = await session_router.list_workers()

    return WorkersListResponse(
        workers=[
            WorkerStatusResponse(
                instance=w.instance,
                endpoint=w.endpoint,
                status=w.status,
                capacity=w.capacity,
                active_sessions=w.active_sessions,
                models=w.models,
                languages=w.languages,
            )
            for w in workers
        ],
        total=len(workers),
    )


@router.get(
    "/workers/{instance}",
    response_model=WorkerStatusResponse,
    summary="Get worker status",
    description="Get status of a specific real-time worker.",
    responses={404: {"description": "Worker not found"}},
)
async def get_worker_status(
    instance: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session_router: SessionCoordinator = Depends(get_session_router),
) -> WorkerStatusResponse:
    """Get specific worker status."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.SESSION_READ)

    worker = await session_router.get_worker(instance)

    if worker is None:
        raise HTTPException(status_code=404, detail=Err.WORKER_NOT_FOUND)

    return WorkerStatusResponse(
        instance=worker.instance,
        endpoint=worker.endpoint,
        status=worker.status,
        capacity=worker.capacity,
        active_sessions=worker.active_sessions,
        models=worker.models,
        languages=worker.languages,
    )
