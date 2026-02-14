"""Unit tests for CleanupWorker."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from dalston.config import Settings
from dalston.orchestrator.cleanup import (
    PURGE_LOCK_JOB_KEY,
    PURGE_LOCK_SESSION_KEY,
    PURGE_LOCK_TTL_SECONDS,
    CleanupWorker,
)


class TestCleanupWorkerInit:
    """Tests for CleanupWorker initialization."""

    def test_init_sets_attributes(self):
        """Test that CleanupWorker sets attributes correctly."""
        mock_factory = MagicMock()
        settings = Settings()

        worker = CleanupWorker(
            db_session_factory=mock_factory,
            settings=settings,
            audit_service=None,
        )

        assert worker.db_session_factory is mock_factory
        assert worker.settings is settings
        assert worker.audit_service is None
        assert worker._running is False
        assert worker._task is None
        assert worker._redis is None


class TestCleanupWorkerStartStop:
    """Tests for CleanupWorker start/stop methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        redis.close = AsyncMock()
        return redis

    @pytest.fixture
    def worker(self, mock_db_session):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        settings = Settings()
        settings.retention_cleanup_interval_seconds = 0.1  # Fast for tests
        settings.retention_cleanup_batch_size = 10
        return CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )

    @pytest.mark.asyncio
    async def test_start_creates_task_and_redis(self, worker, mock_redis):
        """Test that start creates a background task and Redis connection."""
        assert worker._running is False
        assert worker._task is None
        assert worker._redis is None

        with patch(
            "dalston.orchestrator.cleanup.aioredis.from_url", return_value=mock_redis
        ):
            await worker.start()

            assert worker._running is True
            assert worker._task is not None
            assert worker._redis is mock_redis

            # Clean up
            await worker.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, worker, mock_redis):
        """Test that starting an already running worker logs warning."""
        with patch(
            "dalston.orchestrator.cleanup.aioredis.from_url", return_value=mock_redis
        ):
            await worker.start()

            with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
                await worker.start()
                mock_logger.warning.assert_called_with("cleanup_worker_already_running")

            await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task_and_closes_redis(self, worker, mock_redis):
        """Test that stop cancels the background task and closes Redis."""
        with patch(
            "dalston.orchestrator.cleanup.aioredis.from_url", return_value=mock_redis
        ):
            await worker.start()
            task = worker._task

            await worker.stop()

            assert worker._running is False
            assert task.cancelled() or task.done()
            assert worker._redis is None
            mock_redis.close.assert_awaited_once()


class TestCleanupWorkerLocking:
    """Tests for Redis lock acquisition and release."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.close = AsyncMock()
        return redis

    @pytest.fixture
    def worker(self, mock_redis):
        @asynccontextmanager
        async def session_factory():
            yield AsyncMock()

        settings = Settings()
        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis
        return worker

    @pytest.mark.asyncio
    async def test_acquire_job_lock_success(self, worker, mock_redis):
        """Test successful job lock acquisition."""
        mock_redis.set = AsyncMock(return_value=True)
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        result = await worker._acquire_job_lock(job_id)

        assert result is True
        mock_redis.set.assert_awaited_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == PURGE_LOCK_JOB_KEY.format(job_id=str(job_id))
        assert call_args[1]["nx"] is True
        assert call_args[1]["ex"] == PURGE_LOCK_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_acquire_job_lock_already_locked(self, worker, mock_redis):
        """Test job lock acquisition when already locked."""
        mock_redis.set = AsyncMock(return_value=None)  # NX returns None if key exists
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        result = await worker._acquire_job_lock(job_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_job_lock_no_redis(self, worker):
        """Test job lock acquisition returns False when Redis not connected."""
        worker._redis = None
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        result = await worker._acquire_job_lock(job_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_release_job_lock(self, worker, mock_redis):
        """Test job lock release."""
        mock_redis.delete = AsyncMock()
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        await worker._release_job_lock(job_id)

        expected_key = PURGE_LOCK_JOB_KEY.format(job_id=str(job_id))
        mock_redis.delete.assert_awaited_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_acquire_session_lock_success(self, worker, mock_redis):
        """Test successful session lock acquisition."""
        mock_redis.set = AsyncMock(return_value=True)
        session_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

        result = await worker._acquire_session_lock(session_id)

        assert result is True
        expected_key = PURGE_LOCK_SESSION_KEY.format(session_id=str(session_id))
        mock_redis.set.assert_awaited_once()
        assert mock_redis.set.call_args[0][0] == expected_key


class TestCleanupWorkerSweep:
    """Tests for CleanupWorker._sweep method."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)  # Lock always acquired
        redis.delete = AsyncMock()
        redis.close = AsyncMock()
        return redis

    @pytest.fixture
    def settings(self):
        s = Settings()
        s.retention_cleanup_interval_seconds = 300
        s.retention_cleanup_batch_size = 100
        return s

    def _make_job(
        self,
        job_id: UUID,
        tenant_id: UUID,
        retention_scope: str = "all",
        purge_after: datetime | None = None,
    ):
        """Create a mock job."""
        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_scope = retention_scope
        job.purge_after = purge_after or datetime.now(UTC) - timedelta(hours=1)
        job.purged_at = None
        return job

    def _make_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        purge_after: datetime | None = None,
    ):
        """Create a mock realtime session."""
        session = MagicMock()
        session.id = session_id
        session.tenant_id = tenant_id
        session.purge_after = purge_after or datetime.now(UTC) - timedelta(hours=1)
        session.purged_at = None
        return session

    @pytest.mark.asyncio
    async def test_sweep_with_no_expired_items(self, mock_redis, settings):
        """Test sweep with no expired jobs or sessions."""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
            await worker._sweep()

            # Should not log completion if nothing purged
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_all_scope(self, mock_redis, settings):
        """Test purging jobs with retention_scope=all using two-phase commit."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="all")

        # Track DB sessions
        query_session = AsyncMock()
        update_session = AsyncMock()

        # Query session returns job list
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        # Update session returns fresh job record
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            assert purged == 1
            # S3 artifacts deleted
            mock_storage.delete_job_artifacts.assert_awaited_once_with(job_id)
            # Job marked as purged
            assert job_record.purged_at is not None
            update_session.commit.assert_awaited()
            # Lock released
            mock_redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_audio_only_scope(self, mock_redis, settings):
        """Test purging jobs with retention_scope=audio_only."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="audio_only")

        query_session = AsyncMock()
        update_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            assert purged == 1
            mock_storage.delete_job_audio.assert_awaited_once_with(job_id)
            mock_storage.delete_job_artifacts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_with_audit(self, mock_redis, settings):
        """Test that purge logs to audit service after DB commit."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="all")

        query_session = AsyncMock()
        update_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        mock_audit = AsyncMock()

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
            audit_service=mock_audit,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            MockStorageService.return_value = AsyncMock()

            await worker._purge_expired_jobs()

            mock_audit.log_job_purged.assert_awaited_once_with(
                job_id=job_id,
                tenant_id=tenant_id,
                artifacts_deleted=["audio", "tasks", "transcript"],
            )

    @pytest.mark.asyncio
    async def test_purge_job_skipped_when_locked(self, mock_redis, settings):
        """Test that job is skipped when lock cannot be acquired."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id)

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis
        # Lock acquisition fails
        mock_redis.set = AsyncMock(return_value=None)

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            assert purged == 0
            mock_storage.delete_job_artifacts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purge_job_handles_s3_error(self, mock_redis, settings):
        """Test that S3 error is handled and lock is released."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id)

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            mock_storage.delete_job_artifacts.side_effect = Exception("S3 error")
            MockStorageService.return_value = mock_storage

            with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
                purged = await worker._purge_expired_jobs()

                assert purged == 0
                mock_logger.error.assert_called()
                # Lock still released in finally block
                mock_redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_sessions(self, mock_redis, settings):
        """Test purging expired realtime sessions."""
        session_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        session = self._make_session(session_id, tenant_id)

        query_session = AsyncMock()
        update_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [session]
        query_session.execute.return_value = mock_result

        session_record = MagicMock()
        session_record.purged_at = None
        update_session.get.return_value = session_record

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            sess = session_calls[call_index]
            call_index += 1
            yield sess

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_sessions()

            assert purged == 1
            mock_storage.delete_session_artifacts.assert_awaited_once_with(session_id)
            assert session_record.purged_at is not None

    @pytest.mark.asyncio
    async def test_purge_session_handles_error(self, mock_redis, settings):
        """Test that session purge handles errors and releases lock."""
        session_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        session = self._make_session(session_id, tenant_id)

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [session]
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            mock_storage.delete_session_artifacts.side_effect = Exception("S3 error")
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_sessions()

            assert purged == 0
            # Lock still released
            mock_redis.delete.assert_awaited()


class TestCleanupWorkerBatchSize:
    """Tests for batch size limiting in cleanup worker."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_respects_batch_size_limit(self, mock_redis):
        """Test that cleanup respects batch size configuration."""
        settings = Settings()
        settings.retention_cleanup_batch_size = 5

        # Create 10 jobs
        jobs = []
        for i in range(10):
            job = MagicMock()
            job.id = UUID(f"aaaaaaaa-aaaa-aaaa-aaaa-{i:012d}")
            job.tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
            job.retention_scope = "all"
            job.purged_at = None
            jobs.append(job)

        # Track sessions: 1 query + 5 updates
        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = jobs[:5]  # Only batch size
        query_session.execute.return_value = mock_result

        update_sessions = [AsyncMock() for _ in range(5)]
        for s in update_sessions:
            job_record = MagicMock()
            job_record.purged_at = None
            s.get.return_value = job_record

        all_sessions = [query_session] + update_sessions
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = all_sessions[call_index]
            call_index += 1
            yield session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            MockStorageService.return_value = AsyncMock()

            purged = await worker._purge_expired_jobs()

            # Should only process batch_size jobs
            assert purged == 5


class TestCleanupWorkerRunLoop:
    """Tests for the cleanup worker run loop."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.close = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_run_loop_sleeps_between_sweeps(self, mock_redis):
        """Test that run loop sleeps for configured interval."""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        settings = Settings()
        settings.retention_cleanup_interval_seconds = 0.05  # 50ms for test

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )

        # Track sweep calls
        sweep_count = 0
        original_sweep = worker._sweep

        async def counting_sweep():
            nonlocal sweep_count
            sweep_count += 1
            if sweep_count >= 2:
                worker._running = False  # Stop after 2 sweeps
            await original_sweep()

        worker._sweep = counting_sweep

        with patch(
            "dalston.orchestrator.cleanup.aioredis.from_url", return_value=mock_redis
        ):
            await worker.start()
            await asyncio.sleep(0.2)  # Wait for at least 2 sweeps
            await worker.stop()

        # Should have done at least 2 sweeps
        assert sweep_count >= 2

    @pytest.mark.asyncio
    async def test_run_loop_handles_sweep_error(self, mock_redis):
        """Test that run loop continues after sweep error."""
        mock_db_session = AsyncMock()

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        settings = Settings()
        settings.retention_cleanup_interval_seconds = 0.05

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )

        error_count = 0

        async def failing_sweep():
            nonlocal error_count
            error_count += 1
            if error_count >= 2:
                worker._running = False
            raise Exception("Sweep failed")

        worker._sweep = failing_sweep

        with (
            patch("dalston.orchestrator.cleanup.logger"),
            patch(
                "dalston.orchestrator.cleanup.aioredis.from_url",
                return_value=mock_redis,
            ),
        ):
            await worker.start()
            await asyncio.sleep(0.2)
            await worker.stop()

        # Should have attempted multiple sweeps despite errors
        assert error_count >= 2


class TestTwoPhaseCommitBehavior:
    """Tests for two-phase commit behavior and recovery scenarios."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        return redis

    @pytest.fixture
    def settings(self):
        s = Settings()
        s.retention_cleanup_batch_size = 10
        return s

    @pytest.mark.asyncio
    async def test_db_commit_fails_after_s3_delete(self, mock_redis, settings):
        """Test that lock is released if DB commit fails after S3 delete.

        This is the key recovery scenario: S3 artifacts are deleted but DB
        commit fails. The lock expires, allowing retry on next sweep.
        Since S3 deletion is idempotent, retry is safe.
        """
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")

        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_scope = "all"
        job.purged_at = None

        query_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        query_session.execute.return_value = mock_result

        update_session = AsyncMock()
        job_record = MagicMock()
        job_record.purged_at = None
        update_session.get.return_value = job_record
        update_session.commit.side_effect = Exception("DB commit failed")

        session_calls = [query_session, update_session]
        call_index = 0

        @asynccontextmanager
        async def session_factory():
            nonlocal call_index
            session = session_calls[call_index]
            call_index += 1
            yield session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            with patch("dalston.orchestrator.cleanup.logger"):
                purged = await worker._purge_expired_jobs()

                # Purge count is 0 because commit failed
                assert purged == 0
                # S3 artifacts were deleted
                mock_storage.delete_job_artifacts.assert_awaited_once_with(job_id)
                # Lock was still released (in finally block)
                mock_redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_concurrent_purge_blocked_by_lock(self, mock_redis, settings):
        """Test that concurrent purge attempts are blocked by lock."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")

        job = MagicMock()
        job.id = job_id
        job.tenant_id = tenant_id
        job.retention_scope = "all"
        job.purged_at = None

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )
        worker._redis = mock_redis
        # First call acquires, second call fails
        mock_redis.set = AsyncMock(side_effect=[True, None])

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            # First purge succeeds
            job_record = MagicMock()
            job_record.purged_at = None
            mock_db_session.get.return_value = job_record

            purged1 = await worker._purge_expired_jobs()
            assert purged1 == 1

            # Reset for second attempt
            mock_result.scalars.return_value.all.return_value = [job]

            # Second purge blocked by lock
            purged2 = await worker._purge_expired_jobs()
            assert purged2 == 0
