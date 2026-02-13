"""Unit tests for CleanupWorker."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from dalston.config import Settings
from dalston.orchestrator.cleanup import CleanupWorker


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


class TestCleanupWorkerStartStop:
    """Tests for CleanupWorker start/stop methods."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

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
    async def test_start_creates_task(self, worker):
        """Test that start creates a background task."""
        assert worker._running is False
        assert worker._task is None

        await worker.start()

        assert worker._running is True
        assert worker._task is not None

        # Clean up
        await worker.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, worker):
        """Test that starting an already running worker logs warning."""
        await worker.start()

        with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
            await worker.start()
            mock_logger.warning.assert_called_with("cleanup_worker_already_running")

        await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, worker):
        """Test that stop cancels the background task."""
        await worker.start()
        task = worker._task

        await worker.stop()

        assert worker._running is False
        assert task.cancelled() or task.done()


class TestCleanupWorkerSweep:
    """Tests for CleanupWorker._sweep method."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        # Default to return empty results
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        return session

    @pytest.fixture
    def mock_storage(self):
        storage = AsyncMock()
        return storage

    @pytest.fixture
    def settings(self):
        s = Settings()
        s.retention_cleanup_interval_seconds = 300
        s.retention_cleanup_batch_size = 100
        return s

    @pytest.fixture
    def worker(self, mock_db_session, settings):
        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        return CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )

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
    async def test_sweep_with_no_expired_items(self, worker, mock_db_session):
        """Test sweep with no expired jobs or sessions."""
        with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
            await worker._sweep()

            # Should not log completion if nothing purged
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_all_scope(
        self, worker, mock_db_session, settings
    ):
        """Test purging jobs with retention_scope=all."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="all")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            assert purged == 1
            mock_storage.delete_job_artifacts.assert_awaited_once_with(job_id)
            assert job.purged_at is not None
            mock_db_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_audio_only_scope(
        self, worker, mock_db_session, settings
    ):
        """Test purging jobs with retention_scope=audio_only."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="audio_only")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_jobs()

            assert purged == 1
            mock_storage.delete_job_audio.assert_awaited_once_with(job_id)
            mock_storage.delete_job_artifacts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_jobs_with_audit(self, mock_db_session, settings):
        """Test that purge logs to audit service."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id, retention_scope="all")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        mock_audit = AsyncMock()

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
            audit_service=mock_audit,
        )

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            MockStorageService.return_value = AsyncMock()

            await worker._purge_expired_jobs()

            mock_audit.log_job_purged.assert_awaited_once_with(
                job_id=job_id,
                tenant_id=tenant_id,
                artifacts_deleted=["audio", "tasks", "transcript"],
            )

    @pytest.mark.asyncio
    async def test_purge_job_handles_error(self, worker, mock_db_session):
        """Test that purge continues on error and rolls back."""
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job = self._make_job(job_id, tenant_id)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [job]
        mock_db_session.execute.return_value = mock_result

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            mock_storage.delete_job_artifacts.side_effect = Exception("S3 error")
            MockStorageService.return_value = mock_storage

            with patch("dalston.orchestrator.cleanup.logger") as mock_logger:
                purged = await worker._purge_expired_jobs()

                assert purged == 0
                mock_logger.error.assert_called()
                mock_db_session.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_purge_expired_sessions(self, worker, mock_db_session):
        """Test purging expired realtime sessions."""
        session_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        session = self._make_session(session_id, tenant_id)

        # First call returns empty jobs, second returns session
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []

        mock_result_session = MagicMock()
        mock_result_session.scalars.return_value.all.return_value = [session]

        mock_db_session.execute.side_effect = [mock_result_empty, mock_result_session]

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            MockStorageService.return_value = mock_storage

            await worker._sweep()

            mock_storage.delete_session_artifacts.assert_awaited_once_with(session_id)
            assert session.purged_at is not None

    @pytest.mark.asyncio
    async def test_purge_session_handles_error(self, worker, mock_db_session):
        """Test that session purge continues on error."""
        session_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        session = self._make_session(session_id, tenant_id)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [session]
        mock_db_session.execute.return_value = mock_result

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            mock_storage = AsyncMock()
            mock_storage.delete_session_artifacts.side_effect = Exception("S3 error")
            MockStorageService.return_value = mock_storage

            purged = await worker._purge_expired_sessions()

            assert purged == 0
            mock_db_session.rollback.assert_awaited()


class TestCleanupWorkerBatchSize:
    """Tests for batch size limiting in cleanup worker."""

    @pytest.fixture
    def mock_db_session(self):
        session = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_respects_batch_size_limit(self, mock_db_session):
        """Test that cleanup respects batch size configuration."""
        settings = Settings()
        settings.retention_cleanup_batch_size = 5

        @asynccontextmanager
        async def session_factory():
            yield mock_db_session

        worker = CleanupWorker(
            db_session_factory=session_factory,
            settings=settings,
        )

        # Create 10 jobs
        jobs = []
        for i in range(10):
            job = MagicMock()
            job.id = UUID(f"aaaaaaaa-aaaa-aaaa-aaaa-{i:012d}")
            job.tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
            job.retention_scope = "all"
            job.purged_at = None
            jobs.append(job)

        # Only return first 5 (batch size)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = jobs[:5]
        mock_db_session.execute.return_value = mock_result

        with patch("dalston.orchestrator.cleanup.StorageService") as MockStorageService:
            MockStorageService.return_value = AsyncMock()

            purged = await worker._purge_expired_jobs()

            # Should only process batch_size jobs
            assert purged == 5


class TestCleanupWorkerRunLoop:
    """Tests for the cleanup worker run loop."""

    @pytest.mark.asyncio
    async def test_run_loop_sleeps_between_sweeps(self):
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

        await worker.start()
        await asyncio.sleep(0.2)  # Wait for at least 2 sweeps
        await worker.stop()

        # Should have done at least 2 sweeps
        assert sweep_count >= 2

    @pytest.mark.asyncio
    async def test_run_loop_handles_sweep_error(self):
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

        with patch("dalston.orchestrator.cleanup.logger"):
            await worker.start()
            await asyncio.sleep(0.2)
            await worker.stop()

        # Should have attempted multiple sweeps despite errors
        assert error_count >= 2
