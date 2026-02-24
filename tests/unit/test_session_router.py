"""Unit tests for session_router module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from dalston.session_router.allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.session_router.health import HealthMonitor
from dalston.session_router.registry import (
    ACTIVE_SESSIONS_KEY,
    SESSION_KEY_PREFIX,
    WORKER_KEY_PREFIX,
    WORKER_SESSIONS_SUFFIX,
    WorkerRegistry,
    WorkerState,
)


class TestWorkerState:
    """Tests for WorkerState dataclass."""

    def test_available_capacity(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=2,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 2

    def test_available_capacity_at_capacity(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="busy",
            capacity=4,
            active_sessions=4,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 0

    def test_available_capacity_negative_clamped(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="busy",
            capacity=4,
            active_sessions=5,  # Over capacity
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 0

    def test_is_available_ready_with_capacity(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=2,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.is_available is True

    def test_is_available_busy_with_capacity(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="busy",
            capacity=4,
            active_sessions=3,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="3GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.is_available is True

    def test_is_not_available_offline(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="offline",
            capacity=4,
            active_sessions=0,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="0GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.is_available is False

    def test_is_not_available_draining(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="draining",
            capacity=4,
            active_sessions=1,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="1GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.is_available is False

    def test_is_not_available_at_capacity(self):
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="busy",
            capacity=4,
            active_sessions=4,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        assert worker.is_available is False


class TestWorkerRegistry:
    """Tests for WorkerRegistry class."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def registry(self, mock_redis) -> WorkerRegistry:
        return WorkerRegistry(mock_redis)

    @pytest.mark.asyncio
    async def test_get_worker_found(self, registry: WorkerRegistry, mock_redis):
        mock_redis.hgetall.return_value = {
            "endpoint": "ws://localhost:9000",
            "status": "ready",
            "capacity": "4",
            "active_sessions": "2",
            "models_loaded": '["faster-whisper-large-v3", "faster-whisper-distil-large-v3"]',
            "languages_supported": '["auto"]',
            "gpu_memory_used": "2GB",
            "gpu_memory_total": "8GB",
            "last_heartbeat": "2024-01-15T10:30:00+00:00",
            "started_at": "2024-01-15T10:00:00+00:00",
        }

        worker = await registry.get_worker("worker-1")

        assert worker is not None
        assert worker.worker_id == "worker-1"
        assert worker.endpoint == "ws://localhost:9000"
        assert worker.status == "ready"
        assert worker.capacity == 4
        assert worker.active_sessions == 2
        assert worker.models_loaded == [
            "faster-whisper-large-v3",
            "faster-whisper-distil-large-v3",
        ]
        assert worker.languages_supported == ["auto"]

    @pytest.mark.asyncio
    async def test_get_worker_not_found(self, registry: WorkerRegistry, mock_redis):
        mock_redis.hgetall.return_value = {}

        worker = await registry.get_worker("nonexistent")

        assert worker is None

    @pytest.mark.asyncio
    async def test_get_workers(self, registry: WorkerRegistry, mock_redis):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": "[]",
                    "languages_supported": "[]",
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "busy",
                    "capacity": "4",
                    "active_sessions": "3",
                    "models_loaded": "[]",
                    "languages_supported": "[]",
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        workers = await registry.get_workers()

        assert len(workers) == 2
        endpoints = {w.endpoint for w in workers}
        assert "ws://localhost:9000" in endpoints
        assert "ws://localhost:9001" in endpoints

    @pytest.mark.asyncio
    async def test_get_available_workers_filters_by_capacity(
        self, registry: WorkerRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "4",  # Full
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",  # Available
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available_workers(
            "faster-whisper-large-v3", "auto"
        )

        assert len(available) == 1
        assert available[0].endpoint == "ws://localhost:9001"

    @pytest.mark.asyncio
    async def test_get_available_workers_filters_by_model(
        self, registry: WorkerRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": '["faster-whisper-distil-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # Request specific model
        available = await registry.get_available_workers(
            "faster-whisper-distil-large-v3", "auto"
        )

        assert len(available) == 1
        assert available[0].endpoint == "ws://localhost:9001"

    @pytest.mark.asyncio
    async def test_get_available_workers_sorted_by_capacity(
        self, registry: WorkerRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2", "worker-3"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "3",  # 1 available
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "1",  # 3 available (most)
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-3" in key:
                return {
                    "endpoint": "ws://localhost:9002",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",  # 2 available
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available_workers(
            "faster-whisper-large-v3", "auto"
        )

        # Should be sorted by available capacity descending
        assert len(available) == 3
        assert available[0].endpoint == "ws://localhost:9001"  # 3 available
        assert available[1].endpoint == "ws://localhost:9002"  # 2 available
        assert available[2].endpoint == "ws://localhost:9000"  # 1 available

    @pytest.mark.asyncio
    async def test_mark_worker_offline(self, registry: WorkerRegistry, mock_redis):
        await registry.mark_worker_offline("worker-1")

        mock_redis.hset.assert_called_once_with(
            f"{WORKER_KEY_PREFIX}worker-1", "status", "offline"
        )

    @pytest.mark.asyncio
    async def test_get_available_workers_model_none_returns_all(
        self, registry: WorkerRegistry, mock_redis
    ):
        """model=None (auto) should return all available workers regardless of models_loaded."""
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": '["parakeet-rnnt-0.6b"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": '["faster-whisper-large-v3"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # model=None should return ALL available workers
        available = await registry.get_available_workers(None, "auto")

        assert len(available) == 2

    @pytest.mark.asyncio
    async def test_get_available_workers_empty_string_model_no_match(
        self, registry: WorkerRegistry, mock_redis
    ):
        """Empty string model is treated as literal match (no workers have '' in models_loaded)."""
        mock_redis.smembers.return_value = {"worker-1"}

        mock_redis.hgetall.return_value = {
            "endpoint": "ws://localhost:9000",
            "status": "ready",
            "capacity": "4",
            "active_sessions": "2",
            "models_loaded": '["parakeet-rnnt-0.6b"]',
            "languages_supported": '["auto"]',
        }

        # Empty string is a literal value, not None
        # The gateway converts empty string to None before calling the registry
        # This test verifies the registry requires exact model match for non-None
        available = await registry.get_available_workers("", "auto")

        # "" is not in models_loaded, so no workers match
        assert len(available) == 0


class TestSessionAllocator:
    """Tests for SessionAllocator class."""

    @pytest.fixture
    def mock_redis(self):
        mock = AsyncMock()
        # Default hincrby returns new value
        mock.hincrby.return_value = 1
        return mock

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock(spec=WorkerRegistry)

    @pytest.fixture
    def allocator(self, mock_redis, mock_registry) -> SessionAllocator:
        return SessionAllocator(mock_redis, mock_registry)

    @pytest.mark.asyncio
    async def test_acquire_worker_success(
        self, allocator: SessionAllocator, mock_redis, mock_registry
    ):
        # Setup available workers
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=2,
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        mock_registry.get_available_workers.return_value = [worker]
        mock_redis.hincrby.return_value = 3  # New active session count

        result = await allocator.acquire_worker(
            language="en",
            model=None,
            client_ip="192.168.1.100",
        )

        assert result is not None
        assert result.worker_id == "worker-1"
        assert result.endpoint == "ws://localhost:9000"
        assert result.session_id.startswith("sess_")
        mock_registry.get_available_workers.assert_called_once_with(None, "en")
        mock_redis.hincrby.assert_called()

    @pytest.mark.asyncio
    async def test_acquire_worker_no_capacity(
        self, allocator: SessionAllocator, mock_registry
    ):
        mock_registry.get_available_workers.return_value = []

        result = await allocator.acquire_worker(
            language="en",
            model=None,
            client_ip="192.168.1.100",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_worker_rollback_on_race(
        self, allocator: SessionAllocator, mock_redis, mock_registry
    ):
        # Setup worker that will exceed capacity after increment
        worker = WorkerState(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            status="ready",
            capacity=4,
            active_sessions=4,  # Already at capacity
            models_loaded=["faster-whisper-large-v3"],
            languages_supported=["auto"],
            engine="whisper",
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        mock_registry.get_available_workers.return_value = [worker]
        mock_redis.hincrby.return_value = 5  # Exceeds capacity of 4

        result = await allocator.acquire_worker(
            language="en",
            model=None,
            client_ip="192.168.1.100",
        )

        # Should rollback and return None
        assert result is None
        # Should have called hincrby twice (increment then decrement)
        assert mock_redis.hincrby.call_count == 2

    @pytest.mark.asyncio
    async def test_release_worker_success(
        self, allocator: SessionAllocator, mock_redis
    ):
        mock_redis.hgetall.return_value = {
            "worker_id": "worker-1",
            "status": "active",
            "language": "en",
            "model": "faster-whisper-large-v3",
            "client_ip": "192.168.1.100",
            "started_at": "2024-01-15T10:30:00+00:00",
        }

        result = await allocator.release_worker("sess_abc123")

        assert result is not None
        assert result.session_id == "sess_abc123"
        assert result.worker_id == "worker-1"
        assert result.status == "ended"
        mock_redis.hincrby.assert_called()  # Decremented active_sessions

    @pytest.mark.asyncio
    async def test_release_worker_not_found(
        self, allocator: SessionAllocator, mock_redis
    ):
        mock_redis.hgetall.return_value = {}

        result = await allocator.release_worker("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_session(self, allocator: SessionAllocator, mock_redis):
        mock_redis.hgetall.return_value = {
            "worker_id": "worker-1",
            "status": "active",
            "language": "es",
            "model": "faster-whisper-large-v3",
            "client_ip": "10.0.0.5",
            "started_at": "2024-01-15T10:30:00+00:00",
        }

        result = await allocator.get_session("sess_xyz")

        assert result is not None
        assert result.session_id == "sess_xyz"
        assert result.worker_id == "worker-1"
        assert result.language == "es"
        assert result.model == "faster-whisper-large-v3"


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_create_session_state(self):
        state = SessionState(
            session_id="sess_abc123",
            worker_id="worker-1",
            status="active",
            language="en",
            model="faster-whisper-large-v3",
            client_ip="192.168.1.100",
            started_at=datetime.now(UTC),
        )

        assert state.session_id == "sess_abc123"
        assert state.worker_id == "worker-1"
        assert state.status == "active"
        assert state.language == "en"
        assert state.model == "faster-whisper-large-v3"
        assert state.client_ip == "192.168.1.100"


class TestWorkerAllocation:
    """Tests for WorkerAllocation dataclass."""

    def test_create_allocation(self):
        allocation = WorkerAllocation(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            session_id="sess_abc123",
            engine="whisper",
        )

        assert allocation.worker_id == "worker-1"
        assert allocation.engine == "whisper"
        assert allocation.endpoint == "ws://localhost:9000"
        assert allocation.session_id == "sess_abc123"


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock(spec=WorkerRegistry)

    @pytest.fixture
    def health_monitor(self, mock_redis, mock_registry) -> HealthMonitor:
        return HealthMonitor(mock_redis, mock_registry)

    @pytest.mark.asyncio
    async def test_reconcile_no_active_sessions(
        self, health_monitor: HealthMonitor, mock_redis
    ):
        """No cleanup needed when there are no active sessions."""
        mock_redis.smembers.return_value = set()

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 0
        mock_redis.smembers.assert_called_once_with(ACTIVE_SESSIONS_KEY)

    @pytest.mark.asyncio
    async def test_reconcile_all_sessions_alive(
        self, health_monitor: HealthMonitor, mock_redis
    ):
        """No cleanup when all sessions have valid keys."""
        mock_redis.smembers.return_value = {"sess_abc123", "sess_def456"}
        mock_redis.exists.return_value = True  # All session keys exist

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 0
        assert mock_redis.exists.call_count == 2

    @pytest.mark.asyncio
    @patch("dalston.session_router.health.dalston.metrics")
    async def test_reconcile_orphaned_session_cleanup(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Orphaned session is properly cleaned up."""
        orphaned_session = "sess_orphaned"
        worker_id = "worker-1"

        # Setup: session is in active set but key expired
        mock_redis.smembers.side_effect = [
            {orphaned_session},  # First call: ACTIVE_SESSIONS_KEY
            {worker_id},  # Second call: WORKER_SET_KEY
        ]
        mock_redis.exists.return_value = False  # Session key expired
        mock_redis.sismember.return_value = True  # Session in worker's set
        mock_redis.hincrby.return_value = 1  # New count after decrement

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Verify session key was checked
        mock_redis.exists.assert_called_once_with(
            f"{SESSION_KEY_PREFIX}{orphaned_session}"
        )

        # Verify worker was found and counter decremented
        mock_redis.hincrby.assert_called_once_with(
            f"{WORKER_KEY_PREFIX}{worker_id}", "active_sessions", -1
        )

        # Verify session removed from worker's set
        mock_redis.srem.assert_any_call(
            f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}",
            orphaned_session,
        )

        # Verify session removed from active sessions
        mock_redis.srem.assert_any_call(ACTIVE_SESSIONS_KEY, orphaned_session)

        # Verify metrics updated
        mock_metrics.set_session_router_sessions_active.assert_called_once_with(
            worker_id, 1
        )

    @pytest.mark.asyncio
    @patch("dalston.session_router.health.dalston.metrics")
    async def test_reconcile_negative_counter_clamped(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Counter is clamped to zero if it goes negative."""
        orphaned_session = "sess_orphaned"
        worker_id = "worker-1"

        mock_redis.smembers.side_effect = [
            {orphaned_session},
            {worker_id},
        ]
        mock_redis.exists.return_value = False
        mock_redis.sismember.return_value = True
        mock_redis.hincrby.return_value = -1  # Went negative

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Verify counter was reset to 0
        mock_redis.hset.assert_called_once_with(
            f"{WORKER_KEY_PREFIX}{worker_id}", "active_sessions", 0
        )

        # Metrics should show 0
        mock_metrics.set_session_router_sessions_active.assert_called_once_with(
            worker_id, 0
        )

    @pytest.mark.asyncio
    @patch("dalston.session_router.health.dalston.metrics")
    async def test_reconcile_multiple_orphaned_sessions(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Multiple orphaned sessions are all cleaned up."""
        orphaned_sessions = {"sess_1", "sess_2", "sess_3"}
        worker_id = "worker-1"

        # Track calls to smembers
        smembers_calls = [
            orphaned_sessions,  # ACTIVE_SESSIONS_KEY
            {worker_id},  # WORKER_SET_KEY for sess_1
            {worker_id},  # WORKER_SET_KEY for sess_2
            {worker_id},  # WORKER_SET_KEY for sess_3
        ]
        mock_redis.smembers.side_effect = smembers_calls
        mock_redis.exists.return_value = False  # All expired
        mock_redis.sismember.return_value = True
        mock_redis.hincrby.return_value = 0

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 3
        assert mock_redis.srem.call_count == 6  # 3 from worker set + 3 from active set

    @pytest.mark.asyncio
    @patch("dalston.session_router.health.dalston.metrics")
    async def test_reconcile_session_not_in_any_worker(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Orphaned session not found in any worker set is still cleaned."""
        orphaned_session = "sess_orphaned"

        mock_redis.smembers.side_effect = [
            {orphaned_session},  # ACTIVE_SESSIONS_KEY
            {"worker-1", "worker-2"},  # WORKER_SET_KEY
        ]
        mock_redis.exists.return_value = False
        mock_redis.sismember.return_value = False  # Not in any worker's set

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Session should still be removed from active sessions
        mock_redis.srem.assert_called_once_with(ACTIVE_SESSIONS_KEY, orphaned_session)

        # No counter decrement since session wasn't found in any worker
        mock_redis.hincrby.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_mixed_live_and_orphaned(
        self, health_monitor: HealthMonitor, mock_redis
    ):
        """Only orphaned sessions are cleaned, live ones remain."""
        live_session = "sess_live"
        orphaned_session = "sess_orphaned"

        mock_redis.smembers.side_effect = [
            {live_session, orphaned_session},
            {"worker-1"},  # For orphaned session lookup
        ]

        # Live session exists, orphaned does not
        async def mock_exists(key):
            return live_session in key

        mock_redis.exists.side_effect = mock_exists
        mock_redis.sismember.return_value = False

        with patch("dalston.session_router.health.dalston.metrics"):
            cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Only orphaned session should be removed
        mock_redis.srem.assert_called_once_with(ACTIVE_SESSIONS_KEY, orphaned_session)
