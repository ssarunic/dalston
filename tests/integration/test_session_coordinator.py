"""Integration tests for SessionCoordinator (M66).

Tests cover the full session lifecycle and recovery edge cases described in
M66 T1 (Integration Safety Net):

- acquire → keepalive → release (happy path)
- capacity saturation and rejection
- concurrent allocate race with rollback
- orphan session cleanup after TTL expiry
- offline worker detection and pub/sub fan-out
- coordinator start/stop lifecycle

These tests verify behavioural parity with the legacy SessionRouter by
exercising the same Redis-backed operations through the SessionCoordinator
interface.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from dalston.common.registry import EngineRecord
from dalston.orchestrator.realtime_registry import (
    ACTIVE_SESSIONS_KEY,
    EVENTS_CHANNEL,
    INSTANCE_KEY_PREFIX,
    INSTANCE_SESSIONS_SUFFIX,
)
from dalston.orchestrator.session_allocator import SessionState, WorkerAllocation
from dalston.orchestrator.session_coordinator import (
    CapacityInfo,
    ParityMonitor,
    SessionCoordinator,
    WorkerStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(
    instance: str = "worker-1",
    endpoint: str = "ws://localhost:9000",
    capacity: int = 4,
    active_sessions: int = 0,
    status: str = "ready",
    runtime: str = "faster-whisper",
    models_loaded: list[str] | None = None,
) -> EngineRecord:
    return EngineRecord(
        instance=instance,
        runtime=runtime,
        stage="transcribe",
        interfaces=["realtime"],
        endpoint=endpoint,
        status=status,
        capacity=capacity,
        active_realtime=active_sessions,
        models_loaded=models_loaded or ["Systran/faster-whisper-large-v3"],
        languages=["auto"],
        gpu_memory_used="2GB",
        gpu_memory_total="8GB",
        last_heartbeat=datetime.now(UTC),
        registered_at=datetime.now(UTC),
    )


def _make_coordinator() -> tuple[SessionCoordinator, AsyncMock]:
    """Return a started coordinator with fully-mocked internals.

    The returned AsyncMock is the Redis client so tests can set up return
    values and assert calls without a real Redis connection.
    """
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    redis_mock = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"
    coordinator._redis = redis_mock
    return coordinator, redis_mock


# ---------------------------------------------------------------------------
# Coordinator lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_start_stop() -> None:
    """start() initialises internals; stop() clears them and closes Redis."""
    with patch("redis.asyncio.from_url") as mock_from_url:
        mock_redis = AsyncMock()
        mock_from_url.return_value = mock_redis

        coordinator = SessionCoordinator(redis_url="redis://localhost:6379")

        assert not coordinator.is_running

        with patch(
            "dalston.orchestrator.session_coordinator.HealthMonitor"
        ) as mock_health_cls:
            mock_health = AsyncMock()
            mock_health_cls.return_value = mock_health

            await coordinator.start()

        assert coordinator.is_running
        mock_health.start.assert_called_once()

        await coordinator.stop()

        assert not coordinator.is_running
        mock_redis.close.assert_called_once()
        mock_health.stop.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_raises_if_not_started() -> None:
    """Methods raise RuntimeError when called before start()."""
    coordinator = SessionCoordinator(redis_url="redis://localhost:6379")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.acquire_worker(language="en", model=None, client_ip="x")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.release_worker("sess_1")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.get_session("sess_1")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.extend_session_ttl("sess_1")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.list_workers()

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.get_worker("worker-1")

    with pytest.raises(RuntimeError, match="not started"):
        await coordinator.get_capacity()


# ---------------------------------------------------------------------------
# Acquire → release (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_and_release_session() -> None:
    """Full acquire → release lifecycle updates Redis correctly."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis_url = "redis://localhost:6379"

    redis_mock = AsyncMock()
    mock_registry = AsyncMock()
    mock_allocator = AsyncMock()
    mock_health = AsyncMock()

    coordinator._redis = redis_mock
    coordinator._registry = mock_registry
    coordinator._allocator = mock_allocator
    coordinator._health = mock_health

    expected_allocation = WorkerAllocation(
        instance="worker-1",
        endpoint="ws://localhost:9000",
        session_id="sess_abc123",
        runtime="faster-whisper",
    )
    mock_allocator.acquire_worker.return_value = expected_allocation

    allocation = await coordinator.acquire_worker(
        language="en",
        model="Systran/faster-whisper-large-v3",
        client_ip="127.0.0.1",
    )

    assert allocation is not None
    assert allocation.session_id == "sess_abc123"
    assert allocation.instance == "worker-1"
    mock_allocator.acquire_worker.assert_called_once_with(
        language="en",
        model="Systran/faster-whisper-large-v3",
        client_ip="127.0.0.1",
        runtime=None,
        valid_runtimes=None,
    )

    expected_state = SessionState(
        session_id="sess_abc123",
        instance="worker-1",
        status="ended",
        language="en",
        model="Systran/faster-whisper-large-v3",
        client_ip="127.0.0.1",
        started_at=datetime.now(UTC),
    )
    mock_allocator.release_worker.return_value = expected_state

    state = await coordinator.release_worker("sess_abc123")

    assert state is not None
    assert state.status == "ended"
    mock_allocator.release_worker.assert_called_once_with("sess_abc123")


# ---------------------------------------------------------------------------
# Capacity saturation and rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_returns_none_when_no_capacity() -> None:
    """acquire_worker returns None when all workers are at capacity."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._registry = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_allocator = AsyncMock()
    mock_allocator.acquire_worker.return_value = None
    coordinator._allocator = mock_allocator

    result = await coordinator.acquire_worker(
        language="en",
        model=None,
        client_ip="10.0.0.1",
    )

    assert result is None


@pytest.mark.asyncio
async def test_get_capacity_aggregates_worker_pool() -> None:
    """get_capacity() correctly sums capacity and active sessions."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_all.return_value = [
        _make_worker("w1", capacity=4, active_sessions=2, status="busy"),
        _make_worker("w2", capacity=4, active_sessions=0, status="ready"),
        _make_worker("w3", capacity=4, active_sessions=4, status="busy"),
    ]
    coordinator._registry = mock_registry

    info = await coordinator.get_capacity()

    assert info.total_capacity == 12
    assert info.used_capacity == 6
    assert info.available_capacity == 6
    assert info.worker_count == 3
    assert info.ready_workers == 3  # busy + ready both count as "ready_workers"


# ---------------------------------------------------------------------------
# Concurrent allocate race with rollback (via allocator delegation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_rollback_on_race() -> None:
    """When the allocator rolls back a race, acquire returns None."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._registry = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    # Allocator simulates full capacity on every worker after rollback
    mock_allocator = AsyncMock()
    mock_allocator.acquire_worker.return_value = None
    coordinator._allocator = mock_allocator

    result = await coordinator.acquire_worker(
        language="auto",
        model=None,
        client_ip="192.168.0.1",
    )

    assert result is None
    mock_allocator.acquire_worker.assert_called_once()


# ---------------------------------------------------------------------------
# Orphan session cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("dalston.orchestrator.session_health.dalston.metrics")
async def test_orphan_reconciliation(mock_metrics) -> None:
    """Health monitor reconciles orphaned sessions via the coordinator."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis_url = "redis://localhost:6379"

    redis_mock = AsyncMock()
    instance = "worker-1"
    orphaned_session = "sess_orphaned"

    # Session in active set, but key has expired
    redis_mock.smembers.side_effect = [
        {orphaned_session},  # ACTIVE_SESSIONS_KEY
        {instance},  # INSTANCE_SET_KEY
    ]
    redis_mock.exists.return_value = False  # key expired
    redis_mock.sismember.return_value = True
    redis_mock.hincrby.return_value = 1

    coordinator._redis = redis_mock

    mock_registry = AsyncMock()
    coordinator._registry = mock_registry
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()

    # Instantiate a real HealthMonitor to test the reconciliation logic
    from dalston.orchestrator.session_health import HealthMonitor

    health = HealthMonitor(redis_mock, mock_registry)
    cleaned = await health.reconcile_orphaned_sessions()

    assert cleaned == 1

    redis_mock.hincrby.assert_called_once_with(
        f"{INSTANCE_KEY_PREFIX}{instance}", "active_sessions", -1
    )
    redis_mock.srem.assert_any_call(
        f"{INSTANCE_KEY_PREFIX}{instance}{INSTANCE_SESSIONS_SUFFIX}",
        orphaned_session,
    )
    redis_mock.srem.assert_any_call(ACTIVE_SESSIONS_KEY, orphaned_session)


# ---------------------------------------------------------------------------
# Offline worker detection and pub/sub fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("dalston.orchestrator.session_health.dalston.metrics")
async def test_offline_worker_publishes_events(mock_metrics) -> None:
    """Stale worker is marked offline and offline events are published per session."""
    from datetime import timedelta

    from dalston.orchestrator.session_health import HealthMonitor

    redis_mock = AsyncMock()

    # Registry returns one stale worker (heartbeat >30 s ago)
    stale_worker = _make_worker("worker-stale", status="ready")
    stale_worker = EngineRecord(
        **{
            **stale_worker.__dict__,
            "last_heartbeat": datetime.now(UTC) - timedelta(seconds=60),
        }
    )
    mock_registry = AsyncMock()
    mock_registry.get_all.return_value = [stale_worker]
    mock_registry.mark_instance_offline = AsyncMock()
    redis_mock.smembers.return_value = {"sess_1", "sess_2"}

    health = HealthMonitor(redis_mock, mock_registry)
    await health.check_workers()

    mock_registry.mark_instance_offline.assert_called_once_with("worker-stale")
    assert redis_mock.publish.call_count == 2  # one event per session

    for call in redis_mock.publish.call_args_list:
        channel, payload_str = call[0]
        assert channel == EVENTS_CHANNEL
        payload = json.loads(payload_str)
        assert payload["type"] == "instance.offline"
        assert payload["instance"] == "worker-stale"
        assert payload["session_id"] in {"sess_1", "sess_2"}


# ---------------------------------------------------------------------------
# Worker introspection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_workers_returns_status_objects() -> None:
    """list_workers() converts EngineRecord to WorkerStatus."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_all.return_value = [
        _make_worker("w1", capacity=4, active_sessions=1),
        _make_worker("w2", capacity=4, active_sessions=2),
    ]
    coordinator._registry = mock_registry

    workers = await coordinator.list_workers()

    assert len(workers) == 2
    assert all(isinstance(w, WorkerStatus) for w in workers)
    instances = {w.instance for w in workers}
    assert instances == {"w1", "w2"}


@pytest.mark.asyncio
async def test_get_worker_found() -> None:
    """get_worker() returns WorkerStatus for a known instance."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_by_instance.return_value = _make_worker("w1")
    coordinator._registry = mock_registry

    result = await coordinator.get_worker("w1")

    assert result is not None
    assert isinstance(result, WorkerStatus)
    assert result.instance == "w1"


@pytest.mark.asyncio
async def test_get_worker_not_found() -> None:
    """get_worker() returns None for an unknown instance."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_by_instance.return_value = None
    coordinator._registry = mock_registry

    result = await coordinator.get_worker("nonexistent")

    assert result is None


# ---------------------------------------------------------------------------
# Session TTL extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extend_session_ttl() -> None:
    """extend_session_ttl() delegates to the allocator."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._registry = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    mock_allocator = AsyncMock()
    coordinator._allocator = mock_allocator

    await coordinator.extend_session_ttl("sess_abc", ttl=120)

    mock_allocator.extend_session_ttl.assert_called_once_with("sess_abc", 120)


# ---------------------------------------------------------------------------
# WorkerStatus.from_engine_record
# ---------------------------------------------------------------------------


def test_worker_status_from_engine_record() -> None:
    """WorkerStatus.from_engine_record maps all fields correctly."""
    worker = _make_worker(
        instance="w1",
        endpoint="ws://w1:9000",
        capacity=8,
        active_sessions=3,
        status="busy",
        runtime="parakeet",
        models_loaded=["parakeet-tdt-0.6b-v3"],
    )
    status = WorkerStatus.from_engine_record(worker)

    assert status.instance == "w1"
    assert status.endpoint == "ws://w1:9000"
    assert status.capacity == 8
    assert status.active_sessions == 3
    assert status.status == "busy"
    assert status.runtime == "parakeet"
    assert status.models == ["parakeet-tdt-0.6b-v3"]
    assert status.languages == ["auto"]


# ---------------------------------------------------------------------------
# CapacityInfo
# ---------------------------------------------------------------------------


def test_capacity_info_fields() -> None:
    """CapacityInfo is a plain dataclass with expected fields."""
    info = CapacityInfo(
        total_capacity=12,
        used_capacity=5,
        available_capacity=7,
        worker_count=3,
        ready_workers=2,
    )
    assert info.total_capacity == 12
    assert info.used_capacity == 5
    assert info.available_capacity == 7
    assert info.worker_count == 3
    assert info.ready_workers == 2


# ---------------------------------------------------------------------------
# ParityMonitor – non-mutating read-only observer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_monitor_start_stop() -> None:
    """ParityMonitor starts and stops cleanly."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    coordinator._redis = AsyncMock()
    coordinator._registry = AsyncMock()
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()
    coordinator._redis_url = "redis://localhost:6379"

    monitor = ParityMonitor(coordinator)
    assert not monitor.is_running

    await monitor.start()
    assert monitor.is_running

    await monitor.stop()
    assert not monitor.is_running


@pytest.mark.asyncio
async def test_parity_monitor_does_not_mutate_redis() -> None:
    """ParityMonitor only reads Redis — never writes, marks offline, or reconciles."""
    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    redis_mock = AsyncMock()
    coordinator._redis = redis_mock
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_all.return_value = [
        _make_worker("w1", capacity=4, active_sessions=2),
    ]
    coordinator._registry = mock_registry
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()

    redis_mock.smembers.return_value = {"sess_1", "sess_2"}

    monitor = ParityMonitor(coordinator)
    # Trigger a single snapshot directly (not via the background loop)
    await monitor._snapshot()

    # Only read operations: smembers and registry.get_all
    mock_registry.get_all.assert_called_once()
    redis_mock.smembers.assert_called_once()

    # No write operations
    redis_mock.hset.assert_not_called()
    redis_mock.hincrby.assert_not_called()
    redis_mock.srem.assert_not_called()
    redis_mock.sadd.assert_not_called()
    redis_mock.publish.assert_not_called()
    redis_mock.expire.assert_not_called()
    mock_registry.mark_instance_offline.assert_not_called()


@pytest.mark.asyncio
async def test_parity_monitor_tolerates_registry_error() -> None:
    """ParityMonitor continues running even when a snapshot raises."""
    import asyncio

    coordinator = SessionCoordinator.__new__(SessionCoordinator)
    redis_mock = AsyncMock()
    coordinator._redis = redis_mock
    coordinator._redis_url = "redis://localhost:6379"

    mock_registry = AsyncMock()
    mock_registry.get_all.side_effect = Exception("Redis timeout")
    coordinator._registry = mock_registry
    coordinator._allocator = AsyncMock()
    coordinator._health = AsyncMock()

    monitor = ParityMonitor(coordinator)
    monitor.CHECK_INTERVAL = 0.05  # type: ignore[assignment]

    await monitor.start()
    await asyncio.sleep(0.12)  # allow ≥2 loop iterations
    await monitor.stop()

    # Should have attempted multiple snapshots despite errors
    assert mock_registry.get_all.call_count >= 2
