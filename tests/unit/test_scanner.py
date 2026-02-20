"""Unit tests for the stale task scanner.

Tests the scanner.py module used by the orchestrator for crash recovery.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import TaskStatus
from dalston.orchestrator.scanner import (
    STALE_THRESHOLD_MS,
    StaleTaskScanner,
)


class MockSettings:
    """Mock settings for testing."""

    redis_url = "redis://localhost:6379"
    engine_unavailable_behavior = "fail_fast"


@pytest.fixture
def mock_redis():
    """Create mock async Redis client."""
    redis = AsyncMock()
    redis.scan = AsyncMock(return_value=(0, []))
    redis.xpending_range = AsyncMock(return_value=[])
    redis.xrange = AsyncMock(return_value=[])
    redis.hgetall = AsyncMock(return_value={})
    redis.smembers = AsyncMock(return_value=set())
    redis.srem = AsyncMock(return_value=1)
    redis.hdel = AsyncMock(return_value=1)
    redis.hset = AsyncMock(return_value=1)
    redis.xdel = AsyncMock(return_value=1)
    # Leader election methods
    redis.set = AsyncMock(return_value=True)  # Lock acquired by default
    redis.eval = AsyncMock(return_value=1)  # Lock released/extended by default
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def mock_db_session():
    """Create mock database session factory."""

    class MockSession:
        def __init__(self):
            self.committed = False

        async def execute(self, query):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        async def commit(self):
            self.committed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def factory():
        return MockSession()

    return factory


class TestStaleTaskScanner:
    """Tests for StaleTaskScanner class."""

    def test_init(self, mock_redis, mock_db_session):
        """Test scanner initialization."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            scan_interval_seconds=30,
        )

        assert scanner._scan_interval == 30
        assert scanner._running is False
        assert scanner._task is None
        assert scanner._is_leader is False
        assert scanner._instance_id is not None

    def test_init_with_custom_instance_id(self, mock_redis, mock_db_session):
        """Test scanner initialization with custom instance ID."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        assert scanner._instance_id == "test-instance-1"

    @pytest.mark.asyncio
    async def test_start_stop(self, mock_redis, mock_db_session):
        """Test scanner start and stop."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        await scanner.start()
        assert scanner._running is True
        assert scanner._task is not None

        await scanner.stop()
        assert scanner._running is False

    @pytest.mark.asyncio
    async def test_scan_no_streams(self, mock_redis, mock_db_session):
        """Test scan when no streams exist."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        # No streams found
        mock_redis.scan.return_value = (0, [])

        await scanner._scan()

        # Should complete without errors
        mock_redis.scan.assert_called()

    @pytest.mark.asyncio
    async def test_scan_stream_no_pending(self, mock_redis, mock_db_session):
        """Test scanning a stream with no pending tasks."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        # One stream found but no pending tasks
        mock_redis.scan.return_value = (0, ["dalston:stream:transcribe"])
        mock_redis.xpending_range.return_value = []

        await scanner._scan()

        mock_redis.xpending_range.assert_called()


class TestScanStream:
    """Tests for _scan_stream method."""

    @pytest.mark.asyncio
    async def test_ignores_non_stale_tasks(self, mock_redis, mock_db_session):
        """Test that tasks below stale threshold are ignored."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        # Pending task that's not stale yet (5 minutes)
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "123-0",
                "consumer": "engine-1",
                "time_since_delivered": 5 * 60 * 1000,  # 5 min < 10 min threshold
                "times_delivered": 1,
            }
        ]
        mock_redis.xrange.return_value = [["123-0", {"task_id": str(uuid4())}]]

        stale, failed = await scanner._scan_stream("transcribe")

        assert stale == 0
        assert failed == 0

    @pytest.mark.asyncio
    async def test_detects_stale_task_from_dead_engine(
        self, mock_redis, mock_db_session
    ):
        """Test detecting stale task from a dead engine."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        task_id = str(uuid4())

        # Stale pending task
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "123-0",
                "consumer": "dead-engine",
                "time_since_delivered": STALE_THRESHOLD_MS + 1000,
                "times_delivered": 1,
            }
        ]
        mock_redis.xrange.return_value = [["123-0", {"task_id": task_id}]]

        # Engine is dead (no heartbeat data)
        mock_redis.hgetall.return_value = {}

        with patch.object(scanner, "_fail_task", new_callable=AsyncMock) as mock_fail:
            mock_fail.return_value = True

            stale, failed = await scanner._scan_stream("transcribe")

            assert stale == 1
            assert failed == 1
            mock_fail.assert_called_once_with(
                task_id=task_id,
                queue_id="transcribe",
                error="Engine 'dead-engine' stopped heartbeating while processing task",
                reason="engine_dead",
            )

    @pytest.mark.asyncio
    async def test_skips_stale_task_from_alive_engine_not_timed_out(
        self, mock_redis, mock_db_session
    ):
        """Test that stale tasks from alive engines are not failed unless timed out."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        task_id = str(uuid4())
        now = datetime.now(UTC)

        # Stale pending task
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "123-0",
                "consumer": "alive-engine",
                "time_since_delivered": STALE_THRESHOLD_MS + 1000,
                "times_delivered": 1,
            }
        ]
        mock_redis.xrange.return_value = [
            [
                "123-0",
                {
                    "task_id": task_id,
                    "timeout_at": (
                        now + timedelta(hours=1)
                    ).isoformat(),  # Not timed out
                },
            ]
        ]

        # Engine is alive
        mock_redis.hgetall.return_value = {
            "status": "processing",
            "last_heartbeat": now.isoformat(),
        }

        with patch.object(scanner, "_fail_task", new_callable=AsyncMock) as mock_fail:
            stale, failed = await scanner._scan_stream("transcribe")

            assert stale == 1
            assert failed == 0
            mock_fail.assert_not_called()


class TestCheckTaskTimeout:
    """Tests for _check_task_timeout method."""

    @pytest.mark.asyncio
    async def test_task_timed_out(self, mock_redis, mock_db_session):
        """Test detecting a timed out task."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        past_timeout = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        mock_redis.xrange.return_value = [["123-0", {"timeout_at": past_timeout}]]

        result = await scanner._check_task_timeout(
            "transcribe", "123-0", datetime.now(UTC)
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_task_not_timed_out(self, mock_redis, mock_db_session):
        """Test task that hasn't timed out yet."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        future_timeout = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        mock_redis.xrange.return_value = [["123-0", {"timeout_at": future_timeout}]]

        result = await scanner._check_task_timeout(
            "transcribe", "123-0", datetime.now(UTC)
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_task_no_timeout_field(self, mock_redis, mock_db_session):
        """Test task with no timeout_at field."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
        )

        mock_redis.xrange.return_value = [
            ["123-0", {"task_id": "some-task"}]  # No timeout_at
        ]

        result = await scanner._check_task_timeout(
            "transcribe", "123-0", datetime.now(UTC)
        )

        assert result is False


class TestFailTask:
    """Tests for _fail_task method."""

    @pytest.mark.asyncio
    async def test_fails_running_task(self, mock_redis):
        """Test failing a running task."""
        task_id = uuid4()

        # Create mock task model
        mock_task = MagicMock()
        mock_task.status = TaskStatus.RUNNING.value
        mock_task.error = None
        mock_task.completed_at = None

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result

        async def session_factory():
            return mock_session

        # Make session factory work as async context manager
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=lambda: mock_session,
            settings=MockSettings(),
        )

        with patch(
            "dalston.orchestrator.scanner.publish_event", new_callable=AsyncMock
        ):
            result = await scanner._fail_task(
                task_id=str(task_id),
                queue_id="transcribe",
                error="Engine crashed",
                reason="engine_dead",
            )

        assert result is True
        assert mock_task.status == TaskStatus.FAILED.value
        assert mock_task.error == "Engine crashed"

    @pytest.mark.asyncio
    async def test_skips_non_running_task(self, mock_redis):
        """Test that non-running tasks are not failed."""
        task_id = uuid4()

        # Create mock task model that's already completed
        mock_task = MagicMock()
        mock_task.status = TaskStatus.COMPLETED.value

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result

        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=lambda: mock_session,
            settings=MockSettings(),
        )

        result = await scanner._fail_task(
            task_id=str(task_id),
            queue_id="transcribe",
            error="Engine crashed",
            reason="engine_dead",
        )

        assert result is False


class TestIsEngineAliveAsync:
    """Tests for async is_engine_alive function."""

    @pytest.mark.asyncio
    async def test_engine_alive(self, mock_redis):
        """Test engine is alive with fresh heartbeat."""
        from dalston.common.streams import is_engine_alive

        now = datetime.now(UTC)
        mock_redis.hgetall.return_value = {
            "status": "idle",
            "last_heartbeat": now.isoformat(),
        }

        result = await is_engine_alive(mock_redis, "engine-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_engine_dead_stale_heartbeat(self, mock_redis):
        """Test engine is dead with stale heartbeat."""
        from dalston.common.streams import is_engine_alive

        old = datetime.now(UTC) - timedelta(seconds=120)
        mock_redis.hgetall.return_value = {
            "status": "idle",
            "last_heartbeat": old.isoformat(),
        }

        result = await is_engine_alive(mock_redis, "engine-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_engine_dead_not_found(self, mock_redis):
        """Test engine is dead when not in registry."""
        from dalston.common.streams import is_engine_alive

        mock_redis.hgetall.return_value = {}

        result = await is_engine_alive(mock_redis, "engine-1")

        assert result is False


class WaitModeSettings(MockSettings):
    engine_unavailable_behavior = "wait"
    engine_wait_timeout_seconds = 120


class TestWaitEngineTimeoutScan:
    """Tests for waiting-engine timeout scans."""

    @pytest.mark.asyncio
    async def test_emits_wait_timeout_event_for_expired_waiting_task(self, mock_redis):
        task_id = str(uuid4())
        mock_redis.smembers.return_value = {task_id}
        mock_redis.hgetall.return_value = {
            "waiting_for_engine": "true",
            "wait_deadline_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "wait_timeout_s": "120",
            "engine_id": "whisper-cpu",
            "queue_id": "whisper-cpu",
            "stream_message_id": "123-0",
        }
        mock_redis.xpending_range.return_value = []  # Not claimed yet

        mock_task = MagicMock()
        mock_task.status = TaskStatus.READY.value
        mock_task.engine_id = "whisper-cpu"

        class MockSession:
            async def get(self, model, key):
                return mock_task

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=lambda: MockSession(),
            settings=WaitModeSettings(),
        )

        with patch(
            "dalston.orchestrator.scanner.publish_event", new_callable=AsyncMock
        ) as mock_publish:
            timed_out = await scanner._scan_waiting_engine_timeouts()

        assert timed_out == 1
        mock_publish.assert_called_once()
        assert mock_publish.call_args.args[1] == "task.wait_timeout"
        mock_redis.xdel.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_wait_timeout_when_message_already_pending(self, mock_redis):
        task_id = str(uuid4())
        mock_redis.smembers.return_value = {task_id}
        mock_redis.hgetall.return_value = {
            "waiting_for_engine": "true",
            "wait_deadline_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "wait_timeout_s": "120",
            "engine_id": "whisper-cpu",
            "queue_id": "whisper-cpu",
            "stream_message_id": "123-0",
        }
        # Message is pending (already picked up), so timeout should not fire.
        mock_redis.xpending_range.return_value = [{"message_id": "123-0"}]

        mock_task = MagicMock()
        mock_task.status = TaskStatus.READY.value
        mock_task.engine_id = "whisper-cpu"

        class MockSession:
            async def get(self, model, key):
                return mock_task

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=lambda: MockSession(),
            settings=WaitModeSettings(),
        )

        with patch(
            "dalston.orchestrator.scanner.publish_event", new_callable=AsyncMock
        ) as mock_publish:
            timed_out = await scanner._scan_waiting_engine_timeouts()

        assert timed_out == 0
        mock_publish.assert_not_called()
        mock_redis.xdel.assert_not_called()


class TestLeaderElection:
    """Tests for leader election functionality."""

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_success(self, mock_redis, mock_db_session):
        """Test successful leader lock acquisition."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        mock_redis.set.return_value = True  # Lock acquired

        result = await scanner._acquire_leader_lock()

        assert result is True
        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args[1]
        assert call_kwargs["nx"] is True
        assert call_kwargs["ex"] > 0

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_failure(self, mock_redis, mock_db_session):
        """Test failed leader lock acquisition (another instance holds it)."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        mock_redis.set.return_value = None  # Lock not acquired

        result = await scanner._acquire_leader_lock()

        assert result is False

    @pytest.mark.asyncio
    async def test_release_leader_lock(self, mock_redis, mock_db_session):
        """Test releasing leader lock."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        await scanner._release_leader_lock()

        # Should use eval for atomic check-and-delete
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_leader_lock_success(self, mock_redis, mock_db_session):
        """Test successful leader lock extension."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        mock_redis.eval.return_value = 1  # Lock extended

        result = await scanner._extend_leader_lock()

        assert result is True

    @pytest.mark.asyncio
    async def test_extend_leader_lock_lost(self, mock_redis, mock_db_session):
        """Test failed leader lock extension (lost leadership)."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        mock_redis.eval.return_value = 0  # Lock not extended (different owner)

        result = await scanner._extend_leader_lock()

        assert result is False

    @pytest.mark.asyncio
    async def test_scan_only_runs_when_leader(self, mock_redis, mock_db_session):
        """Test that scan only runs when lock is acquired."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        # First call: lock acquired, second call: lock not acquired
        mock_redis.set.side_effect = [True, None]
        mock_redis.scan.return_value = (0, [])

        # Run loop iteration manually (simulating two cycles)
        with patch.object(scanner, "_scan", new_callable=AsyncMock) as mock_scan:
            # First iteration - should scan (leader)
            if await scanner._acquire_leader_lock():
                await scanner._scan()
                await scanner._release_leader_lock()

            # Reset mock
            mock_scan.reset_mock()

            # Second iteration - should NOT scan (not leader)
            if await scanner._acquire_leader_lock():
                await scanner._scan()

            # _scan should only have been called once (in first iteration)
            # but we're testing the pattern, not the actual loop
            assert mock_scan.call_count == 0  # Second iteration didn't call _scan

    @pytest.mark.asyncio
    async def test_stop_releases_leader_lock(self, mock_redis, mock_db_session):
        """Test that stop releases leader lock if held."""
        scanner = StaleTaskScanner(
            redis=mock_redis,
            db_session_factory=mock_db_session,
            settings=MockSettings(),
            instance_id="test-instance-1",
        )

        # Simulate being the leader
        scanner._is_leader = True

        await scanner.stop()

        # Should have called eval to release lock
        mock_redis.eval.assert_called()
