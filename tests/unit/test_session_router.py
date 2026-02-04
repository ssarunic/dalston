"""Unit tests for session_router module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from dalston.session_router.allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.session_router.registry import (
    WORKER_KEY_PREFIX,
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            models_loaded=["fast"],
            languages_supported=["auto"],
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
            "models_loaded": '["fast", "accurate"]',
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
        assert worker.models_loaded == ["fast", "accurate"]
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
                    "models_loaded": '["fast"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",  # Available
                    "models_loaded": '["fast"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available_workers("fast", "auto")

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
                    "models_loaded": '["fast"]',  # Only fast
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",
                    "models_loaded": '["accurate"]',  # Only accurate
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # Request accurate model
        available = await registry.get_available_workers("accurate", "auto")

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
                    "models_loaded": '["fast"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-2" in key:
                return {
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "1",  # 3 available (most)
                    "models_loaded": '["fast"]',
                    "languages_supported": '["auto"]',
                }
            elif "worker-3" in key:
                return {
                    "endpoint": "ws://localhost:9002",
                    "status": "ready",
                    "capacity": "4",
                    "active_sessions": "2",  # 2 available
                    "models_loaded": '["fast"]',
                    "languages_supported": '["auto"]',
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available_workers("fast", "auto")

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
            models_loaded=["fast"],
            languages_supported=["auto"],
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        mock_registry.get_available_workers.return_value = [worker]
        mock_redis.hincrby.return_value = 3  # New active session count

        result = await allocator.acquire_worker(
            language="en",
            model="fast",
            client_ip="192.168.1.100",
        )

        assert result is not None
        assert result.worker_id == "worker-1"
        assert result.endpoint == "ws://localhost:9000"
        assert result.session_id.startswith("sess_")
        mock_registry.get_available_workers.assert_called_once_with("fast", "en")
        mock_redis.hincrby.assert_called()

    @pytest.mark.asyncio
    async def test_acquire_worker_no_capacity(
        self, allocator: SessionAllocator, mock_registry
    ):
        mock_registry.get_available_workers.return_value = []

        result = await allocator.acquire_worker(
            language="en",
            model="fast",
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
            models_loaded=["fast"],
            languages_supported=["auto"],
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        mock_registry.get_available_workers.return_value = [worker]
        mock_redis.hincrby.return_value = 5  # Exceeds capacity of 4

        result = await allocator.acquire_worker(
            language="en",
            model="fast",
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
            "model": "fast",
            "client_ip": "192.168.1.100",
            "started_at": "2024-01-15T10:30:00+00:00",
            "enhance_on_end": "false",
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
            "model": "accurate",
            "client_ip": "10.0.0.5",
            "started_at": "2024-01-15T10:30:00+00:00",
            "enhance_on_end": "true",
        }

        result = await allocator.get_session("sess_xyz")

        assert result is not None
        assert result.session_id == "sess_xyz"
        assert result.worker_id == "worker-1"
        assert result.language == "es"
        assert result.model == "accurate"
        assert result.enhance_on_end is True


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_create_session_state(self):
        state = SessionState(
            session_id="sess_abc123",
            worker_id="worker-1",
            status="active",
            language="en",
            model="fast",
            client_ip="192.168.1.100",
            started_at=datetime.now(UTC),
            enhance_on_end=True,
        )

        assert state.session_id == "sess_abc123"
        assert state.worker_id == "worker-1"
        assert state.status == "active"
        assert state.language == "en"
        assert state.model == "fast"
        assert state.client_ip == "192.168.1.100"
        assert state.enhance_on_end is True


class TestWorkerAllocation:
    """Tests for WorkerAllocation dataclass."""

    def test_create_allocation(self):
        allocation = WorkerAllocation(
            worker_id="worker-1",
            endpoint="ws://localhost:9000",
            session_id="sess_abc123",
        )

        assert allocation.worker_id == "worker-1"
        assert allocation.endpoint == "ws://localhost:9000"
        assert allocation.session_id == "sess_abc123"
