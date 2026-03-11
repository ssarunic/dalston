"""Unit tests for session_router module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from dalston.common.registry import (
    UNIFIED_INSTANCE_KEY_PREFIX,
    EngineRecord,
    UnifiedEngineRegistry,
)
from dalston.orchestrator.realtime_registry import (
    ACTIVE_SESSIONS_KEY,
    INSTANCE_KEY_PREFIX,
    INSTANCE_SESSIONS_SUFFIX,
    INSTANCE_SET_KEY,
    SESSION_KEY_PREFIX,
)
from dalston.orchestrator.session_allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.orchestrator.session_health import HealthMonitor


class TestEngineRecord:
    """Tests for EngineRecord dataclass."""

    def test_available_capacity(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="ready",
            capacity=4,
            active_realtime=2,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 2

    def test_available_capacity_at_capacity(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="busy",
            capacity=4,
            active_realtime=4,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 0

    def test_available_capacity_negative_clamped(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="busy",
            capacity=4,
            active_realtime=5,  # Over capacity
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.available_capacity == 0

    def test_is_available_ready_with_capacity(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="ready",
            capacity=4,
            active_realtime=2,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.is_available is True

    def test_is_available_busy_with_capacity(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="busy",
            capacity=4,
            active_realtime=3,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="3GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.is_available is True

    def test_is_not_available_offline(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="offline",
            capacity=4,
            active_realtime=0,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="0GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.is_available is False

    def test_is_not_available_draining(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="draining",
            capacity=4,
            active_realtime=1,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="1GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.is_available is False

    def test_is_not_available_at_capacity(self):
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="busy",
            capacity=4,
            active_realtime=4,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )

        assert worker.is_available is False


class TestUnifiedEngineRegistry:
    """Tests for UnifiedEngineRegistry class."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def registry(self, mock_redis) -> UnifiedEngineRegistry:
        return UnifiedEngineRegistry(mock_redis)

    @pytest.mark.asyncio
    async def test_get_worker_found(self, registry: UnifiedEngineRegistry, mock_redis):
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "stage": "transcribe",
            "interfaces": '["realtime"]',
            "endpoint": "ws://localhost:9000",
            "status": "ready",
            "capacity": "4",
            "active_realtime": "2",
            "models_loaded": '["Systran/faster-whisper-large-v3", "Systran/faster-distil-whisper-large-v3"]',
            "languages": '["auto"]',
            "gpu_memory_used": "2GB",
            "gpu_memory_total": "8GB",
            "last_heartbeat": "2024-01-15T10:30:00+00:00",
            "registered_at": "2024-01-15T10:00:00+00:00",
        }

        worker = await registry.get_by_instance("worker-1")

        assert worker is not None
        assert worker.instance == "worker-1"
        assert worker.endpoint == "ws://localhost:9000"
        assert worker.status == "ready"
        assert worker.capacity == 4
        assert worker.active_realtime == 2
        assert worker.models_loaded == [
            "Systran/faster-whisper-large-v3",
            "Systran/faster-distil-whisper-large-v3",
        ]
        assert worker.languages == ["auto"]

    @pytest.mark.asyncio
    async def test_get_worker_not_found(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        mock_redis.hgetall.return_value = {}

        worker = await registry.get_by_instance("nonexistent")

        assert worker is None

    @pytest.mark.asyncio
    async def test_get_workers(self, registry: UnifiedEngineRegistry, mock_redis):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",
                    "models_loaded": "[]",
                    "languages": "[]",
                }
            elif "worker-2" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9001",
                    "status": "busy",
                    "capacity": "4",
                    "active_realtime": "3",
                    "models_loaded": "[]",
                    "languages": "[]",
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        workers = await registry.get_all()

        assert len(workers) == 2
        endpoints = {w.endpoint for w in workers}
        assert "ws://localhost:9000" in endpoints
        assert "ws://localhost:9001" in endpoints

    @pytest.mark.asyncio
    async def test_get_available_workers_filters_by_capacity(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "4",  # Full
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            elif "worker-2" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",  # Available
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available(
            interface="realtime",
            model="Systran/faster-whisper-large-v3",
            language="auto",
        )

        assert len(available) == 1
        assert available[0].endpoint == "ws://localhost:9001"

    @pytest.mark.asyncio
    async def test_get_available_workers_filters_by_model(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            elif "worker-2" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",
                    "models_loaded": '["Systran/faster-distil-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # Request specific model
        available = await registry.get_available(
            interface="realtime",
            model="Systran/faster-distil-whisper-large-v3",
            language="auto",
        )

        assert len(available) == 1
        assert available[0].endpoint == "ws://localhost:9001"

    @pytest.mark.asyncio
    async def test_get_available_workers_sorted_by_capacity(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        mock_redis.smembers.return_value = {"worker-1", "worker-2", "worker-3"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "3",  # 1 available
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            elif "worker-2" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "1",  # 3 available (most)
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            elif "worker-3" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9002",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",  # 2 available
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available(
            interface="realtime",
            model="Systran/faster-whisper-large-v3",
            language="auto",
        )

        # Should be sorted by available capacity descending
        assert len(available) == 3
        assert available[0].endpoint == "ws://localhost:9001"  # 3 available
        assert available[1].endpoint == "ws://localhost:9002"  # 2 available
        assert available[2].endpoint == "ws://localhost:9000"  # 1 available

    @pytest.mark.asyncio
    async def test_mark_worker_offline(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        await registry.mark_instance_offline("worker-1")

        mock_redis.hset.assert_called_once_with(
            f"{UNIFIED_INSTANCE_KEY_PREFIX}worker-1", "status", "offline"
        )

    @pytest.mark.asyncio
    async def test_get_available_workers_model_none_returns_all(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        """model=None (auto) should return all available workers regardless of models_loaded."""
        mock_redis.smembers.return_value = {"worker-1", "worker-2"}

        async def mock_hgetall(key):
            if "worker-1" in key:
                return {
                    "engine_id": "nemo",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9000",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",
                    "models_loaded": '["parakeet-rnnt-0.6b"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            elif "worker-2" in key:
                return {
                    "engine_id": "faster-whisper",
                    "stage": "transcribe",
                    "interfaces": '["realtime"]',
                    "endpoint": "ws://localhost:9001",
                    "status": "ready",
                    "capacity": "4",
                    "active_realtime": "2",
                    "models_loaded": '["Systran/faster-whisper-large-v3"]',
                    "languages": '["auto"]',
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # model=None should return ALL available workers
        available = await registry.get_available(
            interface="realtime", model=None, language="auto"
        )

        assert len(available) == 2

    @pytest.mark.asyncio
    async def test_get_available_workers_empty_string_model_no_match(
        self, registry: UnifiedEngineRegistry, mock_redis
    ):
        """Empty string model is treated as literal match (no workers have '' in models_loaded)."""
        mock_redis.smembers.return_value = {"worker-1"}

        mock_redis.hgetall.return_value = {
            "engine_id": "nemo",
            "stage": "transcribe",
            "interfaces": '["realtime"]',
            "endpoint": "ws://localhost:9000",
            "status": "ready",
            "capacity": "4",
            "active_realtime": "2",
            "models_loaded": '["parakeet-rnnt-0.6b"]',
            "languages": '["auto"]',
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }

        # Empty string is a literal value, not None
        # The gateway converts empty string to None before calling the registry
        # This test verifies the registry requires exact model match for non-None
        available = await registry.get_available(
            interface="realtime", model="", language="auto"
        )

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
        return AsyncMock(spec=UnifiedEngineRegistry)

    @pytest.fixture
    def allocator(self, mock_redis, mock_registry) -> SessionAllocator:
        return SessionAllocator(mock_redis, mock_registry)

    @pytest.mark.asyncio
    async def test_acquire_worker_success(
        self, allocator: SessionAllocator, mock_redis, mock_registry
    ):
        # Setup available workers
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="ready",
            capacity=4,
            active_realtime=2,
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            endpoint="ws://localhost:9000",
            gpu_memory_used="2GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )
        mock_registry.get_available.return_value = [worker]
        mock_redis.hincrby.return_value = 3  # New active session count

        result = await allocator.acquire_worker(
            language="en",
            model=None,
            client_ip="192.168.1.100",
        )

        assert result is not None
        assert result.instance == "worker-1"
        assert result.endpoint == "ws://localhost:9000"
        assert result.session_id.startswith("sess_")
        mock_registry.get_available.assert_called_once()
        mock_redis.hincrby.assert_called()

    @pytest.mark.asyncio
    async def test_acquire_worker_populates_instance_set_key(
        self, allocator: SessionAllocator, mock_redis, mock_registry
    ):
        """INSTANCE_SET_KEY must be populated so orphan reconciliation can find instances."""
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="ready",
            capacity=4,
            active_realtime=1,
            endpoint="ws://localhost:9000",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )
        mock_registry.get_available.return_value = [worker]
        mock_redis.hincrby.return_value = 2

        result = await allocator.acquire_worker(
            language="en", model=None, client_ip="10.0.0.1"
        )

        assert result is not None
        # Verify instance was added to the instance index set
        sadd_calls = {
            (call.args[0], call.args[1]) for call in mock_redis.sadd.call_args_list
        }
        assert (INSTANCE_SET_KEY, "worker-1") in sadd_calls

    @pytest.mark.asyncio
    async def test_acquire_worker_no_capacity(
        self, allocator: SessionAllocator, mock_registry
    ):
        mock_registry.get_available.return_value = []

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
        worker = EngineRecord(
            instance="worker-1",
            engine_id="faster-whisper",
            stage="transcribe",
            interfaces=["realtime"],
            status="ready",
            capacity=4,
            active_realtime=4,  # Already at capacity
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["auto"],
            endpoint="ws://localhost:9000",
            gpu_memory_used="4GB",
            gpu_memory_total="8GB",
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
        )
        mock_registry.get_available.return_value = [worker]
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
            "instance": "worker-1",
            "status": "active",
            "language": "en",
            "model": "Systran/faster-whisper-large-v3",
            "client_ip": "192.168.1.100",
            "started_at": "2024-01-15T10:30:00+00:00",
        }

        result = await allocator.release_worker("sess_abc123")

        assert result is not None
        assert result.session_id == "sess_abc123"
        assert result.instance == "worker-1"
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
            "instance": "worker-1",
            "status": "active",
            "language": "es",
            "model": "Systran/faster-whisper-large-v3",
            "client_ip": "10.0.0.5",
            "started_at": "2024-01-15T10:30:00+00:00",
        }

        result = await allocator.get_session("sess_xyz")

        assert result is not None
        assert result.session_id == "sess_xyz"
        assert result.instance == "worker-1"
        assert result.language == "es"
        assert result.model == "Systran/faster-whisper-large-v3"


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_create_session_state(self):
        state = SessionState(
            session_id="sess_abc123",
            instance="worker-1",
            status="active",
            language="en",
            model="Systran/faster-whisper-large-v3",
            client_ip="192.168.1.100",
            started_at=datetime.now(UTC),
        )

        assert state.session_id == "sess_abc123"
        assert state.instance == "worker-1"
        assert state.status == "active"
        assert state.language == "en"
        assert state.model == "Systran/faster-whisper-large-v3"
        assert state.client_ip == "192.168.1.100"


class TestWorkerAllocation:
    """Tests for WorkerAllocation dataclass."""

    def test_create_allocation(self):
        allocation = WorkerAllocation(
            instance="worker-1",
            endpoint="ws://localhost:9000",
            session_id="sess_abc123",
            engine_id="faster-whisper",
        )

        assert allocation.instance == "worker-1"
        assert allocation.engine_id == "faster-whisper"
        assert allocation.endpoint == "ws://localhost:9000"
        assert allocation.session_id == "sess_abc123"


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock(spec=UnifiedEngineRegistry)

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
    @patch("dalston.orchestrator.session_health.dalston.metrics")
    async def test_reconcile_orphaned_session_cleanup(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Orphaned session is properly cleaned up."""
        orphaned_session = "sess_orphaned"
        instance = "worker-1"

        # Setup: session is in active set but key expired
        mock_redis.smembers.side_effect = [
            {orphaned_session},  # First call: ACTIVE_SESSIONS_KEY
            {instance},  # Second call: INSTANCE_SET_KEY
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
            f"{INSTANCE_KEY_PREFIX}{instance}", "active_sessions", -1
        )

        # Verify session removed from worker's set
        mock_redis.srem.assert_any_call(
            f"{INSTANCE_KEY_PREFIX}{instance}{INSTANCE_SESSIONS_SUFFIX}",
            orphaned_session,
        )

        # Verify session removed from active sessions
        mock_redis.srem.assert_any_call(ACTIVE_SESSIONS_KEY, orphaned_session)

        # Verify metrics updated
        mock_metrics.set_session_router_sessions_active.assert_called_once_with(
            instance, 1
        )

    @pytest.mark.asyncio
    @patch("dalston.orchestrator.session_health.dalston.metrics")
    async def test_reconcile_negative_counter_clamped(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Counter is clamped to zero if it goes negative."""
        orphaned_session = "sess_orphaned"
        instance = "worker-1"

        mock_redis.smembers.side_effect = [
            {orphaned_session},
            {instance},
        ]
        mock_redis.exists.return_value = False
        mock_redis.sismember.return_value = True
        mock_redis.hincrby.return_value = -1  # Went negative

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Verify counter was reset to 0
        mock_redis.hset.assert_called_once_with(
            f"{INSTANCE_KEY_PREFIX}{instance}", "active_sessions", 0
        )

        # Metrics should show 0
        mock_metrics.set_session_router_sessions_active.assert_called_once_with(
            instance, 0
        )

    @pytest.mark.asyncio
    @patch("dalston.orchestrator.session_health.dalston.metrics")
    async def test_reconcile_multiple_orphaned_sessions(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Multiple orphaned sessions are all cleaned up."""
        orphaned_sessions = {"sess_1", "sess_2", "sess_3"}
        instance = "worker-1"

        # Track calls to smembers
        smembers_calls = [
            orphaned_sessions,  # ACTIVE_SESSIONS_KEY
            {instance},  # INSTANCE_SET_KEY for sess_1
            {instance},  # INSTANCE_SET_KEY for sess_2
            {instance},  # INSTANCE_SET_KEY for sess_3
        ]
        mock_redis.smembers.side_effect = smembers_calls
        mock_redis.exists.return_value = False  # All expired
        mock_redis.sismember.return_value = True
        mock_redis.hincrby.return_value = 0

        cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 3
        assert mock_redis.srem.call_count == 6  # 3 from worker set + 3 from active set

    @pytest.mark.asyncio
    @patch("dalston.orchestrator.session_health.dalston.metrics")
    async def test_reconcile_session_not_in_any_worker(
        self, mock_metrics, health_monitor: HealthMonitor, mock_redis
    ):
        """Orphaned session not found in any worker set is still cleaned."""
        orphaned_session = "sess_orphaned"

        mock_redis.smembers.side_effect = [
            {orphaned_session},  # ACTIVE_SESSIONS_KEY
            {"worker-1", "worker-2"},  # INSTANCE_SET_KEY
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

        with patch("dalston.orchestrator.session_health.dalston.metrics"):
            cleaned = await health_monitor.reconcile_orphaned_sessions()

        assert cleaned == 1

        # Only orphaned session should be removed
        mock_redis.srem.assert_called_once_with(ACTIVE_SESSIONS_KEY, orphaned_session)
