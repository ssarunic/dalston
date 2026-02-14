"""Cleanup worker for expired jobs and sessions.

Periodically scans for jobs and sessions past their purge_after time
and deletes their S3 artifacts while preserving database records for audit.

Uses Redis-based coordination to ensure safe two-phase cleanup:
1. Acquire lock on job/session before S3 deletion
2. Delete S3 artifacts (irreversible)
3. Mark as purged in database
4. Release lock

If step 3 fails, the lock expires and the job is retried on next sweep.
S3 deletion is idempotent, so retrying is safe.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import structlog
from redis import asyncio as aioredis
from sqlalchemy import func, select

from dalston.common.audit import AuditService
from dalston.common.models import RetentionScope
from dalston.config import Settings
from dalston.db.models import JobModel, RealtimeSessionModel
from dalston.gateway.services.storage import StorageService

logger = structlog.get_logger()

# Redis key patterns for purge coordination
PURGE_LOCK_JOB_KEY = "dalston:purge_lock:job:{job_id}"
PURGE_LOCK_SESSION_KEY = "dalston:purge_lock:session:{session_id}"

# Lock TTL in seconds - should be longer than max expected purge duration
PURGE_LOCK_TTL_SECONDS = 300  # 5 minutes


class CleanupWorker:
    """Worker that periodically purges expired job and session artifacts.

    Uses Redis locks to coordinate purge operations across multiple workers
    and ensure safe two-phase cleanup (S3 deletion followed by DB update).
    """

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
        self._redis: aioredis.Redis | None = None

    async def start(self) -> None:
        """Start the cleanup worker background task."""
        if self._running:
            logger.warning("cleanup_worker_already_running")
            return

        # Initialize Redis connection for lock coordination
        self._redis = aioredis.from_url(
            self.settings.redis_url,
            decode_responses=True,
        )

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

        # Close Redis connection
        if self._redis:
            await self._redis.close()
            self._redis = None

        logger.info("cleanup_worker_stopped")

    async def _acquire_job_lock(self, job_id: UUID) -> bool:
        """Attempt to acquire a lock for purging a job.

        Uses Redis SET NX EX for atomic lock acquisition with TTL.

        Args:
            job_id: Job UUID to lock

        Returns:
            True if lock acquired, False if already locked
        """
        if not self._redis:
            return False

        key = PURGE_LOCK_JOB_KEY.format(job_id=str(job_id))
        acquired = await self._redis.set(
            key,
            datetime.now(UTC).isoformat(),
            nx=True,
            ex=PURGE_LOCK_TTL_SECONDS,
        )
        return acquired is not None

    async def _release_job_lock(self, job_id: UUID) -> None:
        """Release a job purge lock.

        Args:
            job_id: Job UUID to unlock
        """
        if not self._redis:
            return

        key = PURGE_LOCK_JOB_KEY.format(job_id=str(job_id))
        await self._redis.delete(key)

    async def _acquire_session_lock(self, session_id: UUID) -> bool:
        """Attempt to acquire a lock for purging a session.

        Uses Redis SET NX EX for atomic lock acquisition with TTL.

        Args:
            session_id: Session UUID to lock

        Returns:
            True if lock acquired, False if already locked
        """
        if not self._redis:
            return False

        key = PURGE_LOCK_SESSION_KEY.format(session_id=str(session_id))
        acquired = await self._redis.set(
            key,
            datetime.now(UTC).isoformat(),
            nx=True,
            ex=PURGE_LOCK_TTL_SECONDS,
        )
        return acquired is not None

    async def _release_session_lock(self, session_id: UUID) -> None:
        """Release a session purge lock.

        Args:
            session_id: Session UUID to unlock
        """
        if not self._redis:
            return

        key = PURGE_LOCK_SESSION_KEY.format(session_id=str(session_id))
        await self._redis.delete(key)

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
        """Find and purge expired jobs using two-phase commit.

        Phase 1: Acquire Redis lock and delete S3 artifacts
        Phase 2: Mark job as purged in database

        If Phase 2 fails, the Redis lock expires and the job is retried
        on the next sweep. S3 deletion is idempotent so retry is safe.

        Returns:
            Number of jobs purged
        """
        purged_count = 0
        storage = StorageService(self.settings)

        # Phase 0: Query jobs to purge
        # Note: This query intentionally processes all tenants. The cleanup
        # worker runs as a single system-wide service, not per-tenant.
        async with self.db_session_factory() as db:
            query = (
                select(JobModel)
                .where(JobModel.purge_after <= func.now())
                .where(JobModel.purged_at.is_(None))
                .order_by(JobModel.purge_after)
                .limit(self.settings.retention_cleanup_batch_size)
            )
            result = await db.execute(query)
            jobs = list(result.scalars().all())

        # Process each job with lock coordination
        for job in jobs:
            job_id = job.id
            tenant_id = job.tenant_id
            retention_scope = job.retention_scope

            # Phase 1: Acquire lock before S3 deletion
            if not await self._acquire_job_lock(job_id):
                logger.debug(
                    "job_purge_skipped_locked",
                    job_id=str(job_id),
                )
                continue

            try:
                # Delete S3 artifacts (irreversible operation)
                artifacts_deleted = await self._delete_job_artifacts(
                    job_id, retention_scope, storage
                )

                # Phase 2: Mark as purged in fresh DB session
                # This ensures clean transaction state
                async with self.db_session_factory() as db:
                    job_record = await db.get(JobModel, job_id)
                    if job_record and job_record.purged_at is None:
                        job_record.purged_at = datetime.now(UTC)
                        await db.commit()

                        purged_count += 1
                        logger.info(
                            "job_purged",
                            job_id=str(job_id),
                            retention_scope=retention_scope,
                            artifacts_deleted=artifacts_deleted,
                        )

                        # Audit log (after successful DB commit)
                        if self.audit_service:
                            await self.audit_service.log_job_purged(
                                job_id=job_id,
                                tenant_id=tenant_id,
                                artifacts_deleted=artifacts_deleted,
                            )

            except Exception:
                logger.error(
                    "job_purge_failed",
                    job_id=str(job_id),
                    exc_info=True,
                )
                # Lock will expire, allowing retry on next sweep

            finally:
                # Release lock (best effort - lock will expire anyway)
                await self._release_job_lock(job_id)

        return purged_count

    async def _delete_job_artifacts(
        self,
        job_id: UUID,
        retention_scope: str,
        storage: StorageService,
    ) -> list[str]:
        """Delete job artifacts from S3 based on retention scope.

        This operation is idempotent - deleting already-deleted artifacts
        is safe and will not raise an error.

        Args:
            job_id: Job UUID
            retention_scope: What to delete (all, audio_only)
            storage: Storage service

        Returns:
            List of artifact types deleted
        """
        artifacts_deleted = []

        if retention_scope == RetentionScope.ALL.value:
            # Delete everything
            await storage.delete_job_artifacts(job_id)
            artifacts_deleted = ["audio", "tasks", "transcript"]
        elif retention_scope == RetentionScope.AUDIO_ONLY.value:
            # Delete audio only, keep tasks and transcript
            await storage.delete_job_audio(job_id)
            artifacts_deleted = ["audio"]

        return artifacts_deleted

    async def _purge_expired_sessions(self) -> int:
        """Find and purge expired realtime sessions using two-phase commit.

        Phase 1: Acquire Redis lock and delete S3 artifacts
        Phase 2: Mark session as purged in database

        If Phase 2 fails, the Redis lock expires and the session is retried
        on the next sweep. S3 deletion is idempotent so retry is safe.

        Returns:
            Number of sessions purged
        """
        purged_count = 0
        storage = StorageService(self.settings)

        # Phase 0: Query sessions to purge
        async with self.db_session_factory() as db:
            query = (
                select(RealtimeSessionModel)
                .where(RealtimeSessionModel.purge_after <= func.now())
                .where(RealtimeSessionModel.purged_at.is_(None))
                .order_by(RealtimeSessionModel.purge_after)
                .limit(self.settings.retention_cleanup_batch_size)
            )
            result = await db.execute(query)
            sessions = list(result.scalars().all())

        # Process each session with lock coordination
        for session in sessions:
            session_id = session.id

            # Phase 1: Acquire lock before S3 deletion
            if not await self._acquire_session_lock(session_id):
                logger.debug(
                    "session_purge_skipped_locked",
                    session_id=str(session_id),
                )
                continue

            try:
                # Delete S3 artifacts (irreversible operation)
                await storage.delete_session_artifacts(session_id)

                # Phase 2: Mark as purged in fresh DB session
                async with self.db_session_factory() as db:
                    session_record = await db.get(RealtimeSessionModel, session_id)
                    if session_record and session_record.purged_at is None:
                        session_record.purged_at = datetime.now(UTC)
                        await db.commit()

                        purged_count += 1
                        logger.info(
                            "session_purged",
                            session_id=str(session_id),
                        )

            except Exception:
                logger.error(
                    "session_purge_failed",
                    session_id=str(session_id),
                    exc_info=True,
                )
                # Lock will expire, allowing retry on next sweep

            finally:
                # Release lock (best effort - lock will expire anyway)
                await self._release_session_lock(session_id)

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
