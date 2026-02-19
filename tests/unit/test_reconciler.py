"""Unit tests for the reconciliation sweeper.

Tests that the sweeper correctly identifies and fixes inconsistencies
between Redis Streams PEL and PostgreSQL task states.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import TaskStatus


class TestReconciliationSweeper:
    """Tests for ReconciliationSweeper initialization and lifecycle."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        redis.eval = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session_factory(self):
        return MagicMock()

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    def test_init(self, mock_redis, mock_db_session_factory, mock_settings):
        """Test sweeper initialization."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
        )

        assert sweeper._redis is mock_redis
        assert sweeper._running is False
        assert sweeper._is_leader is False

    def test_init_with_custom_interval(
        self, mock_redis, mock_db_session_factory, mock_settings
    ):
        """Test sweeper with custom reconcile interval."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
            reconcile_interval_seconds=120,
        )

        assert sweeper._reconcile_interval == 120

    @pytest.mark.asyncio
    async def test_start_stop(self, mock_redis, mock_db_session_factory, mock_settings):
        """Test sweeper start and stop."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
            reconcile_interval_seconds=1,
        )

        await sweeper.start()
        assert sweeper._running is True
        assert sweeper._task is not None

        await sweeper.stop()
        assert sweeper._running is False


class TestLeaderElection:
    """Tests for reconciler leader election."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db_session_factory(self):
        return MagicMock()

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_success(
        self, mock_redis, mock_db_session_factory, mock_settings
    ):
        """Test successful leader lock acquisition."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        mock_redis.set = AsyncMock(return_value=True)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
        )

        result = await sweeper._acquire_leader_lock()

        assert result is True
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_failure(
        self, mock_redis, mock_db_session_factory, mock_settings
    ):
        """Test failed leader lock acquisition."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        mock_redis.set = AsyncMock(return_value=None)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
        )

        result = await sweeper._acquire_leader_lock()

        assert result is False

    @pytest.mark.asyncio
    async def test_release_leader_lock(
        self, mock_redis, mock_db_session_factory, mock_settings
    ):
        """Test leader lock release."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        mock_redis.eval = AsyncMock(return_value=1)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=mock_db_session_factory,
            settings=mock_settings,
        )

        await sweeper._release_leader_lock()

        mock_redis.eval.assert_called_once()


class TestOrphanedDbTasks:
    """Tests for detecting orphaned DB tasks (RUNNING but not in PEL)."""

    @pytest.mark.asyncio
    async def test_finds_orphaned_db_task(self):
        """Test detection of task RUNNING in DB but not in PEL."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()
        old_started_at = datetime.now(UTC) - timedelta(minutes=15)

        # Mock task
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.stage = "transcribe"
        mock_task.started_at = old_started_at
        mock_task.status = TaskStatus.RUNNING.value

        # Mock DB session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        # Mock Redis
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.publish = AsyncMock()

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=MagicMock(),
        )

        # Task is not in PEL
        pel_task_ids: set[str] = set()

        count = await sweeper._reconcile_orphaned_db_tasks(mock_db, pel_task_ids)

        assert count == 1
        assert mock_task.status == TaskStatus.FAILED.value
        assert "orphaned" in mock_task.error.lower()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_task_in_pel(self):
        """Test that tasks in PEL are not marked as orphaned."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()
        old_started_at = datetime.now(UTC) - timedelta(minutes=15)

        # Mock task
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.stage = "transcribe"
        mock_task.started_at = old_started_at
        mock_task.status = TaskStatus.RUNNING.value

        # Mock DB session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        sweeper = ReconciliationSweeper(
            redis=AsyncMock(),
            db_session_factory=MagicMock(),
            settings=MagicMock(),
        )

        # Task IS in PEL
        pel_task_ids = {str(task_id)}

        count = await sweeper._reconcile_orphaned_db_tasks(mock_db, pel_task_ids)

        assert count == 0
        # Status should NOT have changed
        assert mock_task.status == TaskStatus.RUNNING.value
        mock_db.commit.assert_not_called()


class TestOrphanedPelEntries:
    """Tests for detecting orphaned PEL entries (in PEL but not RUNNING in DB)."""

    @pytest.mark.asyncio
    async def test_finds_orphaned_pel_entry(self):
        """Test detection of PEL entry where DB task is not RUNNING."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        # Mock DB query result - task is COMPLETED, not RUNNING
        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.COMPLETED.value)]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Mock pending entry
        mock_pending_entry = MagicMock()
        mock_pending_entry.task_id = str(task_id)
        mock_pending_entry.message_id = "1234567890-0"

        # Mock Redis
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        sweeper = ReconciliationSweeper(
            redis=mock_redis,
            db_session_factory=MagicMock(),
            settings=MagicMock(),
        )

        # Patch get_pending and ack_task
        with (
            patch(
                "dalston.orchestrator.reconciler.get_pending",
                new_callable=AsyncMock,
                return_value=[mock_pending_entry],
            ),
            patch(
                "dalston.orchestrator.reconciler.ack_task",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(mock_db, pel_by_stage)

            assert count == 1
            mock_ack.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_running_task_in_pel(self):
        """Test that PEL entries for RUNNING tasks are not ACKed."""
        from dalston.orchestrator.reconciler import ReconciliationSweeper

        task_id = uuid4()

        # Mock DB query result - task IS RUNNING
        mock_result = MagicMock()
        mock_result.all.return_value = [(task_id, TaskStatus.RUNNING.value)]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        sweeper = ReconciliationSweeper(
            redis=AsyncMock(),
            db_session_factory=MagicMock(),
            settings=MagicMock(),
        )

        with (
            patch(
                "dalston.orchestrator.reconciler.get_pending",
                new_callable=AsyncMock,
            ),
            patch(
                "dalston.orchestrator.reconciler.ack_task",
                new_callable=AsyncMock,
            ) as mock_ack,
        ):
            pel_by_stage = {"transcribe": {str(task_id)}}

            count = await sweeper._reconcile_orphaned_pel_entries(mock_db, pel_by_stage)

            assert count == 0
            mock_ack.assert_not_called()
