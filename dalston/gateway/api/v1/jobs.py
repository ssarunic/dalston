"""Jobs API endpoints.

GET /v1/jobs/stats - Get job statistics for dashboard
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.dependencies import (
    get_db,
    get_jobs_service,
    get_principal,
    get_security_manager,
)
from dalston.gateway.models.responses import JobStatsResponse
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.jobs import JobsService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/stats",
    response_model=JobStatsResponse,
    summary="Get job statistics",
    description="Get job statistics including running, queued, and today's completed/failed counts.",
)
async def get_job_stats(
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobStatsResponse:
    """Get job statistics for the current tenant."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.JOB_READ)

    # Non-admin keys only see stats for jobs they created
    key_filter = None if principal.is_admin else principal.id
    stats = await jobs_service.get_stats(
        db, tenant_id=principal.tenant_id, created_by_key_id=key_filter
    )

    return JobStatsResponse(
        running=stats.running,
        queued=stats.queued,
        completed_today=stats.completed_today,
        failed_today=stats.failed_today,
    )
