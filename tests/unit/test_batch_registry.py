"""Unit tests for batch engine registry.

Tests for both client-side (engine_sdk) and server-side (orchestrator) registry.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.engine_sdk.registry import (
    ENGINE_INSTANCES_PREFIX,
    ENGINE_KEY_PREFIX,
    ENGINE_SET_KEY,
    BatchEngineInfo,
)
from dalston.engine_sdk.registry import (
    BatchEngineRegistry as ClientRegistry,
)
from dalston.orchestrator.exceptions import EngineUnavailableError
from dalston.orchestrator.registry import (
    ENGINE_INSTANCES_PREFIX as SERVER_ENGINE_INSTANCES_PREFIX,
)
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
            instance_id="faster-whisper-abc123def456",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
        )

        assert info.engine_id == "faster-whisper"
        assert info.instance_id == "faster-whisper-abc123def456"
        assert info.stage == "transcribe"
        assert info.stream_name == "dalston:stream:faster-whisper"


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
            instance_id="faster-whisper-abc123def456",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
        )

        registry.register(info)

        # Check hset was called with correct key (using instance_id)
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == f"{ENGINE_KEY_PREFIX}faster-whisper-abc123def456"
        mapping = call_args[1]["mapping"]
        assert mapping["engine_id"] == "faster-whisper"
        assert mapping["instance_id"] == "faster-whisper-abc123def456"
        assert mapping["stage"] == "transcribe"
        assert mapping["stream_name"] == "dalston:stream:faster-whisper"
        assert mapping["status"] == "idle"

        # Check TTL was set
        mock_redis.expire.assert_called_once()

        # Check added to both sets: logical engine_id and per-engine instance set
        assert mock_redis.sadd.call_count == 2
        sadd_calls = mock_redis.sadd.call_args_list
        # First call: add logical engine_id to main set
        assert sadd_calls[0][0] == (ENGINE_SET_KEY, "faster-whisper")
        # Second call: add instance_id to per-engine instance set
        assert sadd_calls[1][0] == (
            f"{ENGINE_INSTANCES_PREFIX}faster-whisper",
            "faster-whisper-abc123def456",
        )

    def test_heartbeat(self, registry, mock_redis):
        """Test heartbeat update."""
        # Simulate key exists (normal heartbeat path)
        mock_redis.hget.return_value = "faster-whisper"

        registry.heartbeat(
            instance_id="faster-whisper-abc123def456",
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
        mock_redis.hget.return_value = "faster-whisper"

        registry.heartbeat(
            instance_id="faster-whisper-abc123def456",
            status="idle",
            current_task=None,
        )

        call_args = mock_redis.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "idle"
        assert mapping["current_task"] == ""

    def test_unregister(self, registry, mock_redis):
        """Test engine unregistration."""
        # First register so we have stored info
        info = BatchEngineInfo(
            engine_id="faster-whisper",
            instance_id="faster-whisper-abc123def456",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
        )
        registry._registered_engines[info.instance_id] = info

        registry.unregister("faster-whisper-abc123def456")

        # Should remove from per-engine instance set
        mock_redis.srem.assert_called_once_with(
            f"{ENGINE_INSTANCES_PREFIX}faster-whisper",
            "faster-whisper-abc123def456",
        )
        mock_redis.delete.assert_called_once_with(
            f"{ENGINE_KEY_PREFIX}faster-whisper-abc123def456"
        )

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
            instance_id="faster-whisper-abc123",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
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
            instance_id="faster-whisper-abc123",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
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
            instance_id="faster-whisper-abc123",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
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
        now = datetime.now(UTC).isoformat()

        # First call: get logical engine_ids from main set
        # Then calls to get instance sets, then hgetall for each instance
        def smembers_side_effect(key):
            if key == ENGINE_SET_KEY:
                return {"faster-whisper", "whisperx"}
            elif key == f"{SERVER_ENGINE_INSTANCES_PREFIX}faster-whisper":
                return {"faster-whisper-abc123"}
            elif key == f"{SERVER_ENGINE_INSTANCES_PREFIX}whisperx":
                return {"whisperx-def456"}
            return set()

        mock_redis.smembers.side_effect = smembers_side_effect
        mock_redis.hgetall.side_effect = [
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-abc123",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "whisperx",
                "instance_id": "whisperx-def456",
                "stage": "align",
                "stream_name": "dalston:stream:whisperx",
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

        # get_engine queries instance set first, then hgetall for instance
        mock_redis.smembers.return_value = {"faster-whisper-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "instance_id": "faster-whisper-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        engine = await registry.get_engine("faster-whisper")

        assert engine is not None
        assert engine.engine_id == "faster-whisper"
        assert engine.instance_id == "faster-whisper-abc123"
        assert engine.stage == "transcribe"
        assert engine.status == "idle"
        assert engine.current_task is None

    @pytest.mark.asyncio
    async def test_get_engine_not_found(self, registry, mock_redis):
        """Test get_engine when engine not found (no instances)."""
        mock_redis.smembers.return_value = set()

        engine = await registry.get_engine("nonexistent")

        assert engine is None

    @pytest.mark.asyncio
    async def test_get_engines_for_stage(self, registry, mock_redis):
        """Test get_engines_for_stage filtering."""
        now = datetime.now(UTC).isoformat()

        def smembers_side_effect(key):
            if key == ENGINE_SET_KEY:
                return {"faster-whisper", "whisperx", "pyannote"}
            elif key == f"{SERVER_ENGINE_INSTANCES_PREFIX}faster-whisper":
                return {"faster-whisper-abc123"}
            elif key == f"{SERVER_ENGINE_INSTANCES_PREFIX}whisperx":
                return {"whisperx-def456"}
            elif key == f"{SERVER_ENGINE_INSTANCES_PREFIX}pyannote":
                return {"pyannote-ghi789"}
            return set()

        mock_redis.smembers.side_effect = smembers_side_effect
        mock_redis.hgetall.side_effect = [
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-abc123",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "whisperx",
                "instance_id": "whisperx-def456",
                "stage": "align",
                "stream_name": "dalston:stream:whisperx",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "pyannote",
                "instance_id": "pyannote-ghi789",
                "stage": "diarize",
                "stream_name": "dalston:stream:pyannote",
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
        """Test is_engine_available when engine has healthy instance."""
        now = datetime.now(UTC).isoformat()

        mock_redis.smembers.return_value = {"faster-whisper-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "instance_id": "faster-whisper-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        available = await registry.is_engine_available("faster-whisper")

        assert available is True

    @pytest.mark.asyncio
    async def test_is_engine_available_not_registered(self, registry, mock_redis):
        """Test is_engine_available when engine has no instances."""
        mock_redis.smembers.return_value = set()

        available = await registry.is_engine_available("nonexistent")

        assert available is False

    @pytest.mark.asyncio
    async def test_is_engine_available_stale(self, registry, mock_redis):
        """Test is_engine_available when all instances have stale heartbeat."""
        old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()

        mock_redis.smembers.return_value = {"faster-whisper-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "faster-whisper",
            "instance_id": "faster-whisper-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:faster-whisper",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": old,
            "registered_at": old,
        }

        available = await registry.is_engine_available("faster-whisper")

        assert available is False

    @pytest.mark.asyncio
    async def test_is_engine_available_one_healthy_instance(self, registry, mock_redis):
        """Test is_engine_available returns True if at least one instance is healthy."""
        now = datetime.now(UTC).isoformat()
        old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()

        mock_redis.smembers.return_value = {
            "faster-whisper-abc123",
            "faster-whisper-def456",
        }
        mock_redis.hgetall.side_effect = [
            # First instance is stale
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-abc123",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": old,
                "registered_at": old,
            },
            # Second instance is healthy
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-def456",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
        ]

        available = await registry.is_engine_available("faster-whisper")

        assert available is True

    @pytest.mark.asyncio
    async def test_mark_instance_offline(self, registry, mock_redis):
        """Test marking specific instance as offline."""
        await registry.mark_instance_offline("faster-whisper-abc123")

        mock_redis.hset.assert_called_once_with(
            f"{ENGINE_KEY_PREFIX}faster-whisper-abc123",
            "status",
            "offline",
        )

    @pytest.mark.asyncio
    async def test_mark_engine_offline(self, registry, mock_redis):
        """Test marking all instances of an engine as offline."""
        now = datetime.now(UTC).isoformat()

        mock_redis.smembers.return_value = {
            "faster-whisper-abc123",
            "faster-whisper-def456",
        }
        mock_redis.hgetall.side_effect = [
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-abc123",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
            {
                "engine_id": "faster-whisper",
                "instance_id": "faster-whisper-def456",
                "stage": "transcribe",
                "stream_name": "dalston:stream:faster-whisper",
                "status": "idle",
                "current_task": "",
                "last_heartbeat": now,
                "registered_at": now,
            },
        ]

        await registry.mark_engine_offline("faster-whisper")

        assert mock_redis.hset.call_count == 2
        hset_calls = mock_redis.hset.call_args_list
        keys_marked = {call[0][0] for call in hset_calls}
        assert keys_marked == {
            f"{ENGINE_KEY_PREFIX}faster-whisper-abc123",
            f"{ENGINE_KEY_PREFIX}faster-whisper-def456",
        }


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
