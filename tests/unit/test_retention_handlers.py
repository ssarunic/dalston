"""Unit tests for retention-related orchestrator handlers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import structlog

from dalston.common.models import RetentionMode
from dalston.orchestrator.handlers import _compute_purge_after


class TestComputePurgeAfter:
    """Tests for _compute_purge_after function."""

    @pytest.fixture
    def logger(self):
        """Create a mock logger."""
        return structlog.get_logger().bind(job_id="test")

    def _make_job(
        self,
        retention_mode: str,
        retention_hours: int | None = None,
        completed_at: datetime | None = None,
    ):
        """Create a mock job with retention settings."""
        job = MagicMock()
        job.retention_mode = retention_mode
        job.retention_hours = retention_hours
        job.completed_at = completed_at or datetime.now(UTC)
        job.purge_after = None
        return job

    @pytest.mark.asyncio
    async def test_auto_delete_with_hours(self, logger):
        """Test auto_delete mode schedules purge after retention hours."""
        completed_at = datetime(2026, 2, 13, 10, 0, 0, tzinfo=UTC)
        job = self._make_job(
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=24,
            completed_at=completed_at,
        )

        await _compute_purge_after(job, logger)

        assert job.purge_after is not None
        expected_purge = completed_at + timedelta(hours=24)
        assert job.purge_after == expected_purge

    @pytest.mark.asyncio
    async def test_auto_delete_various_hours(self, logger):
        """Test auto_delete with different hour values."""
        completed_at = datetime(2026, 2, 13, 12, 0, 0, tzinfo=UTC)

        # 1 hour retention
        job = self._make_job(
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=1,
            completed_at=completed_at,
        )
        await _compute_purge_after(job, logger)
        assert job.purge_after == completed_at + timedelta(hours=1)

        # 168 hours (1 week) retention
        job = self._make_job(
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=168,
            completed_at=completed_at,
        )
        await _compute_purge_after(job, logger)
        assert job.purge_after == completed_at + timedelta(hours=168)

    @pytest.mark.asyncio
    async def test_auto_delete_without_hours_no_purge(self, logger):
        """Test auto_delete with no hours doesn't schedule purge."""
        job = self._make_job(
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=None,
        )

        await _compute_purge_after(job, logger)

        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_auto_delete_without_completed_at_no_purge(self, logger):
        """Test auto_delete without completed_at doesn't schedule purge."""
        job = self._make_job(
            retention_mode=RetentionMode.AUTO_DELETE.value,
            retention_hours=24,
            completed_at=None,
        )
        job.completed_at = None  # Explicitly unset

        await _compute_purge_after(job, logger)

        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_none_mode_immediate_purge(self, logger):
        """Test none mode schedules immediate purge."""
        before = datetime.now(UTC)
        job = self._make_job(retention_mode=RetentionMode.NONE.value)

        await _compute_purge_after(job, logger)

        after = datetime.now(UTC)
        assert job.purge_after is not None
        assert before <= job.purge_after <= after

    @pytest.mark.asyncio
    async def test_keep_mode_no_purge(self, logger):
        """Test keep mode never schedules purge."""
        job = self._make_job(
            retention_mode=RetentionMode.KEEP.value,
            retention_hours=None,
        )

        await _compute_purge_after(job, logger)

        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_keep_mode_preserves_null_purge(self, logger):
        """Test keep mode keeps purge_after as null even with hours."""
        job = self._make_job(
            retention_mode=RetentionMode.KEEP.value,
            retention_hours=24,  # Hours should be ignored for keep mode
        )

        await _compute_purge_after(job, logger)

        assert job.purge_after is None

    @pytest.mark.asyncio
    async def test_unknown_mode_no_purge(self, logger):
        """Test unknown retention mode doesn't crash and doesn't purge."""
        job = self._make_job(
            retention_mode="unknown_mode",
            retention_hours=24,
        )

        await _compute_purge_after(job, logger)

        assert job.purge_after is None


class TestPurgeAfterTimezoneHandling:
    """Tests for timezone handling in purge_after computation."""

    @pytest.fixture
    def logger(self):
        return structlog.get_logger().bind(job_id="test")

    @pytest.mark.asyncio
    async def test_purge_after_preserves_utc(self, logger):
        """Test that purge_after is computed in UTC."""
        completed_at = datetime(2026, 2, 13, 23, 30, 0, tzinfo=UTC)
        job = MagicMock()
        job.retention_mode = RetentionMode.AUTO_DELETE.value
        job.retention_hours = 2
        job.completed_at = completed_at
        job.purge_after = None

        await _compute_purge_after(job, logger)

        # Should be 2026-02-14 01:30:00 UTC (crosses midnight)
        assert job.purge_after is not None
        assert job.purge_after.tzinfo == UTC
        assert job.purge_after.year == 2026
        assert job.purge_after.month == 2
        assert job.purge_after.day == 14
        assert job.purge_after.hour == 1
        assert job.purge_after.minute == 30

    @pytest.mark.asyncio
    async def test_none_mode_uses_utc(self, logger):
        """Test that none mode immediate purge uses UTC."""
        job = MagicMock()
        job.retention_mode = RetentionMode.NONE.value
        job.retention_hours = None
        job.completed_at = datetime.now(UTC)
        job.purge_after = None

        await _compute_purge_after(job, logger)

        assert job.purge_after is not None
        assert job.purge_after.tzinfo == UTC
