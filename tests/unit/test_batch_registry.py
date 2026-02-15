"""Unit tests for batch engine registry.

Tests for both client-side (engine_sdk) and server-side (orchestrator) registry.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.engine_sdk.registry import (
    ENGINE_KEY_PREFIX,
    ENGINE_SET_KEY,
    BatchEngineInfo,
)
from dalston.engine_sdk.registry import (
    BatchEngineRegistry as ClientRegistry,
)
from dalston.orchestrator.exceptions import EngineUnavailableError
from dalston.orchestrator.registry import (
    HEARTBEAT_TIMEOUT_SECONDS,
    BatchEngineState,
)
from dalston.orchestrator.registry import (
    BatchEngineRegistry as ServerRegistry,
)


class TestBatchEngineInfo:
    """Tests for BatchEngineInfo dataclass."""

    def test_create_info(self):
        """Test creating engine info."""
        info = BatchEngineInfo(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
        )

        assert info.engine_id == "faster-whisper"
        assert info.stage == "transcribe"
        assert info.queue_name == "dalston:queue:faster-whisper"


class TestClientRegistry:
    """Tests for client-side BatchEngineRegistry (engine_sdk)."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        mock = MagicMock()
        mock.hset = MagicMock()
        mock.expire = MagicMock()
        mock.sadd = MagicMock()
        mock.srem = MagicMock()
        mock.delete = MagicMock()
        mock.close = MagicMock()
        return mock

    @pytest.fixture
    def registry(self, mock_redis):
        """Create registry with mock Redis."""
        reg = ClientRegistry("redis://localhost:6379")
        reg._redis = mock_redis
        return reg

    def test_register(self, registry, mock_redis):
        """Test engine registration."""
        info = BatchEngineInfo(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
        )

        registry.register(info)

        # Check hset was called with correct key and fields
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == f"{ENGINE_KEY_PREFIX}faster-whisper"
        mapping = call_args[1]["mapping"]
        assert mapping["engine_id"] == "faster-whisper"
        assert mapping["stage"] == "transcribe"
        assert mapping["queue_name"] == "dalston:queue:faster-whisper"
        assert mapping["status"] == "idle"

        # Check TTL was set
        mock_redis.expire.assert_called_once()

        # Check added to set
        mock_redis.sadd.assert_called_once_with(ENGINE_SET_KEY, "faster-whisper")

    def test_heartbeat(self, registry, mock_redis):
        """Test heartbeat update."""
        registry.heartbeat(
            engine_id="faster-whisper",
            status="processing",
            current_task="task-123",
        )

        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "processing"
        assert mapping["current_task"] == "task-123"

        # Check TTL was refreshed
        mock_redis.expire.assert_called_once()

    def test_heartbeat_idle(self, registry, mock_redis):
        """Test heartbeat when idle (no current task)."""
        registry.heartbeat(
            engine_id="faster-whisper",
            status="idle",
            current_task=None,
        )

        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "idle"
        assert mapping["current_task"] == ""

    def test_unregister(self, registry, mock_redis):
        """Test engine unregistration."""
        registry.unregister("faster-whisper")

        mock_redis.srem.assert_called_once_with(ENGINE_SET_KEY, "faster-whisper")
        mock_redis.delete.assert_called_once_with(f"{ENGINE_KEY_PREFIX}faster-whisper")

    def test_close(self, registry, mock_redis):
        """Test closing the registry."""
        registry.close()

        mock_redis.close.assert_called_once()
        assert registry._redis is None


class TestBatchEngineState:
    """Tests for BatchEngineState dataclass."""

    def test_is_available_fresh_heartbeat(self):
        """Test engine is available with fresh heartbeat."""
        now = datetime.now(UTC)
        state = BatchEngineState(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
            status="idle",
            current_task=None,
            last_heartbeat=now,
            registered_at=now,
        )

        assert state.is_available is True

    def test_is_available_stale_heartbeat(self):
        """Test engine is unavailable with stale heartbeat."""
        now = datetime.now(UTC)
        old = now - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS + 10)
        state = BatchEngineState(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
            status="idle",
            current_task=None,
            last_heartbeat=old,
            registered_at=old,
        )

        assert state.is_available is False

    def test_is_available_offline_status(self):
        """Test engine is unavailable when status is offline."""
        now = datetime.now(UTC)
        state = BatchEngineState(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
            status="offline",
            current_task=None,
            last_heartbeat=now,
            registered_at=now,
        )

        assert state.is_available is False


class TestServerRegistry:
    """Tests for server-side BatchEngineRegistry (orchestrator)."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock = AsyncMock()
        return mock

    @pytest.fixture
    def registry(self, mock_redis):
        """Create registry with mock Redis."""
        return ServerRegistry(mock_redis)

    @pytest.mark.asyncio
    async def test_get_engines_empty(self, registry, mock_redis):
        """Test get_engines when no engines registered."""
        mock_redis.smembers.return_value = set()

        engines = await registry.get_engines()

        assert engines == []
        mock_redis.smembers.assert_called_once_with(ENGINE_SET_KEY)

    @pytest.mark.asyncio
    async def test_get_engines(self, registry, mock_redis):
        """Test get_engines with registered engines."""
        mock_redis.smembers.return_value = {"faster-whisper", "whisperx"}
        now = datetime.now(UTC).isoformat()

        mock_redis.hgetall.side_effect = [
            {
                "engine_id": "faster-whisper",
                "stage": "transcribe",
                "queue_name": "dalston:queue:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "whisperx",
                "stage": "align",
                "queue_name": "dalston:queue:whisperx",
                "status": "processing",
                "current_task": "task-456",
                "last_heartbeat": now,
                "registered_at": now,
            },
        ]

        engines = await registry.get_engines()

        assert len(engines) == 2
        engine_ids = {e.engine_id for e in engines}
        assert engine_ids == {"faster-whisper", "whisperx"}

    @pytest.mark.asyncio
    async def test_get_engine(self, registry, mock_redis):
        """Test get_engine for specific engine."""
        now = datetime.now(UTC).isoformat()
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "stage": "transcribe",
            "queue_name": "dalston:queue:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        engine = await registry.get_engine("faster-whisper")

        assert engine is not None
        assert engine.engine_id == "faster-whisper"
        assert engine.stage == "transcribe"
        assert engine.status == "idle"
        assert engine.current_task is None

    @pytest.mark.asyncio
    async def test_get_engine_not_found(self, registry, mock_redis):
        """Test get_engine when engine not found."""
        mock_redis.hgetall.return_value = {}

        engine = await registry.get_engine("nonexistent")

        assert engine is None

    @pytest.mark.asyncio
    async def test_get_engines_for_stage(self, registry, mock_redis):
        """Test get_engines_for_stage filtering."""
        mock_redis.smembers.return_value = {"faster-whisper", "whisperx", "pyannote"}
        now = datetime.now(UTC).isoformat()

        mock_redis.hgetall.side_effect = [
            {
                "engine_id": "faster-whisper",
                "stage": "transcribe",
                "queue_name": "dalston:queue:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "whisperx",
                "stage": "align",
                "queue_name": "dalston:queue:whisperx",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "pyannote",
                "stage": "diarize",
                "queue_name": "dalston:queue:pyannote",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
        ]

        transcribers = await registry.get_engines_for_stage("transcribe")

        assert len(transcribers) == 1
        assert transcribers[0].stage == "transcribe"

    @pytest.mark.asyncio
    async def test_is_engine_available_true(self, registry, mock_redis):
        """Test is_engine_available when engine is available."""
        now = datetime.now(UTC).isoformat()
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "stage": "transcribe",
            "queue_name": "dalston:queue:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        available = await registry.is_engine_available("faster-whisper")

        assert available is True

    @pytest.mark.asyncio
    async def test_is_engine_available_not_registered(self, registry, mock_redis):
        """Test is_engine_available when engine not registered."""
        mock_redis.hgetall.return_value = {}

        available = await registry.is_engine_available("nonexistent")

        assert available is False

    @pytest.mark.asyncio
    async def test_is_engine_available_stale(self, registry, mock_redis):
        """Test is_engine_available when heartbeat is stale."""
        old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "stage": "transcribe",
            "queue_name": "dalston:queue:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": old,
            "registered_at": old,
        }

        available = await registry.is_engine_available("faster-whisper")

        assert available is False

    @pytest.mark.asyncio
    async def test_mark_engine_offline(self, registry, mock_redis):
        """Test marking engine as offline."""
        await registry.mark_engine_offline("faster-whisper")

        mock_redis.hset.assert_called_once_with(
            f"{ENGINE_KEY_PREFIX}faster-whisper",
            "status",
            "offline",
        )


class TestEngineUnavailableError:
    """Tests for EngineUnavailableError exception."""

    def test_exception_attributes(self):
        """Test exception has correct attributes."""
        error = EngineUnavailableError(
            message="Engine 'foo' is not available",
            engine_id="foo",
            stage="transcribe",
        )

        assert str(error) == "Engine 'foo' is not available"
        assert error.engine_id == "foo"
        assert error.stage == "transcribe"
