"""Unit tests for JobsService."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from dalston.common.models import JobStatus
from dalston.gateway.services.jobs import JobsService, JobStats


class TestJobStats:
    """Tests for JobStats dataclass."""

    def test_job_stats_attributes(self):
        stats = JobStats(
            running=5,
            queued=10,
            completed_today=25,
            failed_today=2,
        )

        assert stats.running == 5
        assert stats.queued == 10
        assert stats.completed_today == 25
        assert stats.failed_today == 2


class TestJobsServiceGetStats:
    """Tests for JobsService.get_stats method."""

    @pytest.fixture
    def jobs_service(self) -> JobsService:
        return JobsService()

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db

    def _mock_scalar_result(self, value):
        """Create a mock result that returns a scalar value."""
        result = MagicMock()
        result.scalar.return_value = value
        return result

    @pytest.mark.asyncio
    async def test_get_stats_returns_job_stats(
        self, jobs_service: JobsService, mock_db
    ):
        """Test that get_stats returns JobStats instance with correct counts."""
        # Setup mock to return counts for each query
        mock_db.execute.side_effect = [
            self._mock_scalar_result(3),  # running
            self._mock_scalar_result(7),  # queued
            self._mock_scalar_result(15),  # completed_today
            self._mock_scalar_result(2),  # failed_today
        ]

        stats = await jobs_service.get_stats(mock_db)

        assert isinstance(stats, JobStats)
        assert stats.running == 3
        assert stats.queued == 7
        assert stats.completed_today == 15
        assert stats.failed_today == 2

    @pytest.mark.asyncio
    async def test_get_stats_with_tenant_filter(
        self, jobs_service: JobsService, mock_db
    ):
        """Test that get_stats applies tenant filter when provided."""
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")

        mock_db.execute.side_effect = [
            self._mock_scalar_result(1),  # running
            self._mock_scalar_result(2),  # queued
            self._mock_scalar_result(5),  # completed_today
            self._mock_scalar_result(0),  # failed_today
        ]

        stats = await jobs_service.get_stats(mock_db, tenant_id=tenant_id)

        # Verify the stats were returned correctly
        assert stats.running == 1
        assert stats.queued == 2
        assert stats.completed_today == 5
        assert stats.failed_today == 0

        # Verify execute was called 4 times (once for each stat)
        assert mock_db.execute.call_count == 4

    @pytest.mark.asyncio
    async def test_get_stats_without_tenant_filter(
        self, jobs_service: JobsService, mock_db
    ):
        """Test that get_stats works without tenant filter (all tenants)."""
        mock_db.execute.side_effect = [
            self._mock_scalar_result(10),  # running
            self._mock_scalar_result(20),  # queued
            self._mock_scalar_result(100),  # completed_today
            self._mock_scalar_result(5),  # failed_today
        ]

        stats = await jobs_service.get_stats(mock_db, tenant_id=None)

        assert stats.running == 10
        assert stats.queued == 20
        assert stats.completed_today == 100
        assert stats.failed_today == 5

    @pytest.mark.asyncio
    async def test_get_stats_handles_null_counts(
        self, jobs_service: JobsService, mock_db
    ):
        """Test that get_stats handles None/null counts as 0."""
        mock_db.execute.side_effect = [
            self._mock_scalar_result(None),  # running - None
            self._mock_scalar_result(None),  # queued - None
            self._mock_scalar_result(None),  # completed_today - None
            self._mock_scalar_result(None),  # failed_today - None
        ]

        stats = await jobs_service.get_stats(mock_db)

        # All should default to 0 when None
        assert stats.running == 0
        assert stats.queued == 0
        assert stats.completed_today == 0
        assert stats.failed_today == 0

    @pytest.mark.asyncio
    async def test_get_stats_zero_counts(self, jobs_service: JobsService, mock_db):
        """Test that get_stats correctly returns zero counts."""
        mock_db.execute.side_effect = [
            self._mock_scalar_result(0),  # running
            self._mock_scalar_result(0),  # queued
            self._mock_scalar_result(0),  # completed_today
            self._mock_scalar_result(0),  # failed_today
        ]

        stats = await jobs_service.get_stats(mock_db)

        assert stats.running == 0
        assert stats.queued == 0
        assert stats.completed_today == 0
        assert stats.failed_today == 0


class TestJobsServiceDeleteJob:
    """Tests for JobsService.delete_job method."""

    @pytest.fixture
    def jobs_service(self) -> JobsService:
        return JobsService()

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db

    def _make_job(self, status: str, job_id: UUID | None = None, tenant_id: UUID | None = None):
        """Create a mock JobModel with the given status."""
        job = MagicMock()
        job.id = job_id or UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        job.tenant_id = tenant_id or UUID("00000000-0000-0000-0000-000000000000")
        job.status = status
        return job

    @pytest.mark.asyncio
    async def test_delete_completed_job(self, jobs_service: JobsService, mock_db):
        """Test deleting a completed job succeeds."""
        job = self._make_job(JobStatus.COMPLETED.value)

        with patch.object(jobs_service, "get_job", return_value=job):
            result = await jobs_service.delete_job(mock_db, job.id)

        assert result is job
        mock_db.delete.assert_awaited_once_with(job)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_failed_job(self, jobs_service: JobsService, mock_db):
        """Test deleting a failed job succeeds."""
        job = self._make_job(JobStatus.FAILED.value)

        with patch.object(jobs_service, "get_job", return_value=job):
            result = await jobs_service.delete_job(mock_db, job.id)

        assert result is job
        mock_db.delete.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_delete_cancelled_job(self, jobs_service: JobsService, mock_db):
        """Test deleting a cancelled job succeeds."""
        job = self._make_job(JobStatus.CANCELLED.value)

        with patch.object(jobs_service, "get_job", return_value=job):
            result = await jobs_service.delete_job(mock_db, job.id)

        assert result is job
        mock_db.delete.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_delete_running_job_raises(self, jobs_service: JobsService, mock_db):
        """Test that deleting a running job raises ValueError."""
        job = self._make_job(JobStatus.RUNNING.value)

        with patch.object(jobs_service, "get_job", return_value=job):
            with pytest.raises(ValueError, match="Cannot delete job in 'running' state"):
                await jobs_service.delete_job(mock_db, job.id)

        mock_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_pending_job_raises(self, jobs_service: JobsService, mock_db):
        """Test that deleting a pending job raises ValueError."""
        job = self._make_job(JobStatus.PENDING.value)

        with patch.object(jobs_service, "get_job", return_value=job):
            with pytest.raises(ValueError, match="Cannot delete job in 'pending' state"):
                await jobs_service.delete_job(mock_db, job.id)

        mock_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_job_returns_none(self, jobs_service: JobsService, mock_db):
        """Test that deleting a nonexistent job returns None."""
        with patch.object(jobs_service, "get_job", return_value=None):
            result = await jobs_service.delete_job(
                mock_db, UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
            )

        assert result is None
        mock_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_with_tenant_isolation(self, jobs_service: JobsService, mock_db):
        """Test that delete passes tenant_id for isolation."""
        tenant_id = UUID("12345678-1234-1234-1234-123456789abc")
        job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        job = self._make_job(JobStatus.COMPLETED.value, job_id=job_id, tenant_id=tenant_id)

        with patch.object(jobs_service, "get_job", return_value=job) as mock_get:
            result = await jobs_service.delete_job(mock_db, job_id, tenant_id=tenant_id)

        mock_get.assert_awaited_once_with(mock_db, job_id, tenant_id=tenant_id)
        assert result is job
