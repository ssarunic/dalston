"""Cleanup worker for expired jobs and sessions.

Periodically scans for jobs and sessions past their purge_after time
and deletes their S3 artifacts while preserving database records for audit.
"""

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audit import AuditService
from dalston.common.models import RetentionScope
from dalston.config import Settings
from dalston.db.models import JobModel, RealtimeSessionModel
from dalston.gateway.services.storage import StorageService

logger = structlog.get_logger()


class CleanupWorker:
    """Worker that periodically purges expired job and session artifacts."""

    def __init__(
        self,
        db_session_factory,
        settings: Settings,
        audit_service: AuditService | None = None,
    ):
        """Initialize cleanup worker.

        Args:
            db_session_factory: Async context manager factory for database sessions
            settings: Application settings
            audit_service: Optional audit service for logging purge events
        """
        self.db_session_factory = db_session_factory
        self.settings = settings
        self.audit_service = audit_service
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the cleanup worker background task."""
        if self._running:
            logger.warning("cleanup_worker_already_running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "cleanup_worker_started",
            interval_seconds=self.settings.retention_cleanup_interval_seconds,
            batch_size=self.settings.retention_cleanup_batch_size,
        )

    async def stop(self) -> None:
        """Stop the cleanup worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("cleanup_worker_stopped")

    async def _run_loop(self) -> None:
        """Main cleanup loop."""
        while self._running:
            try:
                await asyncio.sleep(self.settings.retention_cleanup_interval_seconds)
                await self._sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("cleanup_sweep_error", exc_info=True)
                # Continue running despite errors

    async def _sweep(self) -> None:
        """Perform one sweep of expired jobs and sessions."""
        jobs_purged = await self._purge_expired_jobs()
        sessions_purged = await self._purge_expired_sessions()

        if jobs_purged > 0 or sessions_purged > 0:
            logger.info(
                "cleanup_sweep_complete",
                jobs_purged=jobs_purged,
                sessions_purged=sessions_purged,
            )

    async def _purge_expired_jobs(self) -> int:
        """Find and purge expired jobs.

        Returns:
            Number of jobs purged
        """
        purged_count = 0
        storage = StorageService(self.settings)

        async with self.db_session_factory() as db:
            # Find jobs ready for purge
            query = (
                select(JobModel)
                .where(JobModel.purge_after <= func.now())
                .where(JobModel.purged_at.is_(None))
                .order_by(JobModel.purge_after)
                .limit(self.settings.retention_cleanup_batch_size)
            )
            result = await db.execute(query)
            jobs = list(result.scalars().all())

            for job in jobs:
                try:
                    artifacts_deleted = await self._purge_job_artifacts(
                        job, storage, db
                    )

                    # Mark job as purged
                    job.purged_at = datetime.now(UTC)
                    await db.commit()

                    # Audit log
                    if self.audit_service:
                        await self.audit_service.log_job_purged(
                            job_id=job.id,
                            tenant_id=job.tenant_id,
                            artifacts_deleted=artifacts_deleted,
                        )

                    purged_count += 1
                    logger.info(
                        "job_purged",
                        job_id=str(job.id),
                        retention_scope=job.retention_scope,
                        artifacts_deleted=artifacts_deleted,
                    )

                except Exception:
                    logger.error(
                        "job_purge_failed",
                        job_id=str(job.id),
                        exc_info=True,
                    )
                    await db.rollback()

        return purged_count

    async def _purge_job_artifacts(
        self,
        job: JobModel,
        storage: StorageService,
        db: AsyncSession,
    ) -> list[str]:
        """Delete job artifacts based on retention scope.

        Args:
            job: Job to purge
            storage: Storage service
            db: Database session

        Returns:
            List of artifact types deleted
        """
        artifacts_deleted = []

        if job.retention_scope == RetentionScope.ALL.value:
            # Delete everything
            await storage.delete_job_artifacts(job.id)
            artifacts_deleted = ["audio", "tasks", "transcript"]
        elif job.retention_scope == RetentionScope.AUDIO_ONLY.value:
            # Delete audio and task intermediates, keep transcript
            await storage.delete_job_audio(job.id)
            artifacts_deleted = ["audio", "tasks"]

        return artifacts_deleted

    async def _purge_expired_sessions(self) -> int:
        """Find and purge expired realtime sessions.

        Returns:
            Number of sessions purged
        """
        purged_count = 0
        storage = StorageService(self.settings)

        async with self.db_session_factory() as db:
            # Find sessions ready for purge
            query = (
                select(RealtimeSessionModel)
                .where(RealtimeSessionModel.purge_after <= func.now())
                .where(RealtimeSessionModel.purged_at.is_(None))
                .order_by(RealtimeSessionModel.purge_after)
                .limit(self.settings.retention_cleanup_batch_size)
            )
            result = await db.execute(query)
            sessions = list(result.scalars().all())

            for session in sessions:
                try:
                    await storage.delete_session_artifacts(session.id)

                    # Mark session as purged
                    session.purged_at = datetime.now(UTC)
                    await db.commit()

                    purged_count += 1
                    logger.info(
                        "session_purged",
                        session_id=str(session.id),
                    )

                except Exception:
                    logger.error(
                        "session_purge_failed",
                        session_id=str(session.id),
                        exc_info=True,
                    )
                    await db.rollback()

        return purged_count


async def run_cleanup_worker(
    db_session_factory,
    settings: Settings,
    audit_service: AuditService | None = None,
) -> None:
    """Run the cleanup worker as a standalone coroutine.

    Args:
        db_session_factory: Async context manager factory for database sessions
        settings: Application settings
        audit_service: Optional audit service for logging purge events
    """
    worker = CleanupWorker(db_session_factory, settings, audit_service)
    await worker.start()

    try:
        # Keep running until cancelled
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await worker.stop()
