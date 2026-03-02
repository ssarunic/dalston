"""Job completion polling utilities.

Provides a shared helper for sync API endpoints that need to wait
for a job to reach a terminal state.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from dalston.common.models import JobStatus
from dalston.common.timeouts import (
    SYNC_OPERATION_TIMEOUT_SECONDS,
    SYNC_POLL_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dalston.db.models import JobModel


class JobCompletionResult:
    """Result of waiting for job completion.

    Attributes:
        status: The terminal status reached, or None if timed out
        job: The refreshed job object
        timed_out: True if the wait timed out
    """

    __slots__ = ("status", "job", "timed_out")

    def __init__(
        self, status: JobStatus | None, job: JobModel, timed_out: bool = False
    ):
        self.status = status
        self.job = job
        self.timed_out = timed_out

    @property
    def completed(self) -> bool:
        """True if job completed successfully."""
        return self.status == JobStatus.COMPLETED

    @property
    def failed(self) -> bool:
        """True if job failed."""
        return self.status == JobStatus.FAILED

    @property
    def cancelled(self) -> bool:
        """True if job was cancelled."""
        return self.status == JobStatus.CANCELLED


async def wait_for_job_completion(
    db: AsyncSession,
    job: JobModel,
    timeout_seconds: float = SYNC_OPERATION_TIMEOUT_SECONDS,
    poll_interval: float = SYNC_POLL_INTERVAL_SECONDS,
) -> JobCompletionResult:
    """Wait for a job to reach a terminal state.

    Polls the database at regular intervals until the job reaches
    COMPLETED, FAILED, or CANCELLED status, or until timeout.

    Args:
        db: Database session for refreshing job state
        job: Job model to monitor (will be refreshed in-place)
        timeout_seconds: Maximum time to wait (default: 5 minutes)
        poll_interval: Seconds between polls (default: 1 second)

    Returns:
        JobCompletionResult with the terminal status and refreshed job

    Example:
        result = await wait_for_job_completion(db, job)
        if result.completed:
            transcript = await storage.get_transcript(job.id)
            return format_response(transcript)
        elif result.failed:
            raise HTTPException(500, f"Job failed: {job.error}")
        elif result.cancelled:
            raise HTTPException(400, "Job was cancelled")
        else:  # timed_out
            raise HTTPException(408, "Request timeout")
    """
    elapsed = 0.0

    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        # Expire cached state and refresh from DB
        db.expire(job)
        await db.refresh(job)

        # Check for terminal states
        if job.status == JobStatus.COMPLETED.value:
            return JobCompletionResult(JobStatus.COMPLETED, job)

        if job.status == JobStatus.FAILED.value:
            return JobCompletionResult(JobStatus.FAILED, job)

        if job.status == JobStatus.CANCELLED.value:
            return JobCompletionResult(JobStatus.CANCELLED, job)

    # Timeout
    return JobCompletionResult(None, job, timed_out=True)
