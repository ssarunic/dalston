"""Jobs API endpoints.

GET /v1/jobs/stats - Get job statistics for dashboard
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.dependencies import RequireJobsRead, get_db, get_jobs_service
from dalston.gateway.models.responses import JobStatsResponse
from dalston.gateway.services.jobs import JobsService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/stats",
    response_model=JobStatsResponse,
    summary="Get job statistics",
    description="Get job statistics including running, queued, and today's completed/failed counts.",
)
async def get_job_stats(
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobStatsResponse:
    """Get job statistics for the current tenant."""
    stats = await jobs_service.get_stats(db, tenant_id=api_key.tenant_id)

    return JobStatsResponse(
        running=stats.running,
        queued=stats.queued,
        completed_today=stats.completed_today,
        failed_today=stats.failed_today,
    )
