"""Unit tests for unified engine registry (M64).

Tests for EngineRecord, UnifiedEngineRegistry (async server-side),
and UnifiedRegistryWriter (sync client-side).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.common.registry import (
    HEARTBEAT_TIMEOUT_SECONDS,
    HEARTBEAT_TTL,
    UNIFIED_INSTANCE_KEY_PREFIX,
    UNIFIED_INSTANCE_SET_KEY,
    UNIFIED_RUNTIME_SET_PREFIX,
    UNIFIED_STAGE_SET_PREFIX,
    EngineRecord,
    UnifiedEngineRegistry,
    UnifiedRegistryWriter,
    _mapping_to_record,
    _record_to_mapping,
)
from dalston.engine_sdk.types import EngineCapabilities

# ---------------------------------------------------------------------------
# EngineRecord tests
# ---------------------------------------------------------------------------


class TestEngineRecord:
    """Tests for EngineRecord dataclass."""

    def _make_record(self, **overrides) -> EngineRecord:
        defaults = {
            "instance": "faster-whisper-abc123",
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": ["batch"],
            "capacity": 4,
            "active_batch": 1,
            "active_realtime": 0,
            "last_heartbeat": datetime.now(UTC),
        }
        defaults.update(overrides)
        return EngineRecord(**defaults)

    def test_available_capacity(self):
        record = self._make_record(capacity=4, active_batch=1, active_realtime=1)
        assert record.available_capacity == 2

    def test_available_capacity_clamped_to_zero(self):
        record = self._make_record(capacity=2, active_batch=3, active_realtime=1)
        assert record.available_capacity == 0

    def test_is_available_idle(self):
        record = self._make_record(status="idle", capacity=4, active_batch=0)
        assert record.is_available is True

    def test_is_available_processing(self):
        record = self._make_record(status="processing", capacity=4, active_batch=1)
        assert record.is_available is True

    def test_is_available_ready(self):
        record = self._make_record(status="ready", interfaces=["realtime"])
        assert record.is_available is True

    def test_not_available_offline(self):
        record = self._make_record(status="offline")
        assert record.is_available is False

    def test_not_available_draining(self):
        record = self._make_record(status="draining")
        assert record.is_available is False

    def test_not_available_stale_heartbeat(self):
        old = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS + 10)
        record = self._make_record(status="idle", last_heartbeat=old)
        assert record.is_available is False

    def test_not_available_at_capacity(self):
        record = self._make_record(capacity=2, active_batch=2)
        assert record.is_available is False

    def test_is_healthy_fresh_heartbeat(self):
        record = self._make_record(status="idle")
        assert record.is_healthy is True

    def test_is_healthy_stale_heartbeat(self):
        old = datetime.now(UTC) - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS + 10)
        record = self._make_record(status="idle", last_heartbeat=old)
        assert record.is_healthy is False

    def test_is_healthy_offline(self):
        record = self._make_record(status="offline")
        assert record.is_healthy is False

    def test_supports_language(self):
        record = self._make_record(languages=["en", "es", "fr"])
        assert record.supports_language("en") is True
        assert record.supports_language("EN") is True
        assert record.supports_language("de") is False

    def test_supports_language_none_means_all(self):
        record = self._make_record(languages=None)
        assert record.supports_language("en") is True
        assert record.supports_language("zh") is True

    def test_supports_interface(self):
        record = self._make_record(interfaces=["batch", "realtime"])
        assert record.supports_interface("batch") is True
        assert record.supports_interface("realtime") is True

    def test_does_not_support_interface(self):
        record = self._make_record(interfaces=["batch"])
        assert record.supports_interface("realtime") is False


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------


class TestRecordSerialization:
    """Tests for record-to-mapping and mapping-to-record round-trip."""

    def test_round_trip_batch(self):
        now = datetime.now(UTC)
        caps = EngineCapabilities(
            runtime="faster-whisper",
            version="1.0",
            stages=["transcribe"],
            languages=["en"],
            supports_word_timestamps=True,
        )
        original = EngineRecord(
            instance="fw-abc123",
            runtime="faster-whisper",
            stage="transcribe",
            status="idle",
            interfaces=["batch"],
            capacity=4,
            active_batch=1,
            active_realtime=0,
            stream_name="dalston:stream:faster-whisper",
            last_heartbeat=now,
            registered_at=now,
            capabilities=caps,
            loaded_model="Systran/faster-whisper-large-v3",
            execution_profile="container",
        )

        mapping = _record_to_mapping(original)
        restored = _mapping_to_record("fw-abc123", mapping)

        assert restored is not None
        assert restored.instance == original.instance
        assert restored.runtime == original.runtime
        assert restored.stage == original.stage
        assert restored.status == original.status
        assert restored.interfaces == original.interfaces
        assert restored.capacity == original.capacity
        assert restored.active_batch == original.active_batch
        assert restored.stream_name == original.stream_name
        assert restored.loaded_model == original.loaded_model
        assert restored.capabilities is not None
        assert restored.capabilities.supports_word_timestamps is True

    def test_round_trip_realtime(self):
        now = datetime.now(UTC)
        original = EngineRecord(
            instance="rt-whisper-xyz789",
            runtime="faster-whisper",
            stage="transcribe",
            status="ready",
            interfaces=["realtime"],
            capacity=2,
            active_batch=0,
            active_realtime=1,
            endpoint="ws://localhost:9000",
            models_loaded=["Systran/faster-whisper-large-v3"],
            languages=["en", "auto"],
            gpu_memory_used="4.2GB",
            gpu_memory_total="8.0GB",
            last_heartbeat=now,
            registered_at=now,
        )

        mapping = _record_to_mapping(original)
        restored = _mapping_to_record("rt-whisper-xyz789", mapping)

        assert restored is not None
        assert restored.interfaces == ["realtime"]
        assert restored.endpoint == "ws://localhost:9000"
        assert restored.models_loaded == ["Systran/faster-whisper-large-v3"]
        assert restored.languages == ["en", "auto"]
        assert restored.gpu_memory_used == "4.2GB"

    def test_mapping_to_record_missing_runtime(self):
        """Missing runtime quarantines the instance."""
        result = _mapping_to_record("bad-inst", {"stage": "transcribe"})
        assert result is None

    def test_mapping_to_record_invalid_json(self):
        """Invalid JSON in list fields degrades gracefully."""
        result = _mapping_to_record(
            "inst-1",
            {
                "runtime": "test",
                "interfaces": "not-json",
                "models_loaded": "{bad}",
                "languages": "also-bad",
            },
        )
        assert result is not None
        assert result.interfaces == ["batch"]  # fallback
        assert result.models_loaded is None
        assert result.languages is None


# ---------------------------------------------------------------------------
# UnifiedEngineRegistry (async, server-side) tests
# ---------------------------------------------------------------------------


class TestUnifiedEngineRegistry:
    """Tests for UnifiedEngineRegistry async server-side registry."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def registry(self, mock_redis) -> UnifiedEngineRegistry:
        return UnifiedEngineRegistry(mock_redis)

    @pytest.mark.asyncio
    async def test_register(self, registry, mock_redis):
        record = EngineRecord(
            instance="fw-abc123",
            runtime="faster-whisper",
            stage="transcribe",
            status="idle",
            interfaces=["batch"],
        )

        await registry.register(record)

        # Should write hash, set TTL, and add to index sets
        mock_redis.hset.assert_called_once()
        mock_redis.expire.assert_called_once_with(
            f"{UNIFIED_INSTANCE_KEY_PREFIX}fw-abc123", HEARTBEAT_TTL
        )
        assert mock_redis.sadd.call_count == 3
        sadd_calls = [call.args for call in mock_redis.sadd.call_args_list]
        assert (UNIFIED_INSTANCE_SET_KEY, "fw-abc123") in sadd_calls
        assert (
            f"{UNIFIED_RUNTIME_SET_PREFIX}faster-whisper",
            "fw-abc123",
        ) in sadd_calls
        assert (f"{UNIFIED_STAGE_SET_PREFIX}transcribe", "fw-abc123") in sadd_calls

    @pytest.mark.asyncio
    async def test_heartbeat(self, registry, mock_redis):
        await registry.heartbeat(
            "fw-abc123",
            status="processing",
            active_batch=1,
            loaded_model="model-v3",
        )

        mock_redis.hset.assert_called_once()
        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert mapping["status"] == "processing"
        assert mapping["active_batch"] == "1"
        assert mapping["loaded_model"] == "model-v3"
        assert "last_heartbeat" in mapping
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_partial_update(self, registry, mock_redis):
        """Heartbeat with only status should not set other fields."""
        await registry.heartbeat("fw-abc123", status="idle")

        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert "status" in mapping
        assert "last_heartbeat" in mapping
        assert "active_batch" not in mapping
        assert "loaded_model" not in mapping

    @pytest.mark.asyncio
    async def test_deregister(self, registry, mock_redis):
        mock_redis.hmget.return_value = ["faster-whisper", "transcribe"]

        await registry.deregister("fw-abc123")

        mock_redis.delete.assert_called_once_with(
            f"{UNIFIED_INSTANCE_KEY_PREFIX}fw-abc123"
        )
        mock_redis.srem.assert_any_call(UNIFIED_INSTANCE_SET_KEY, "fw-abc123")
        mock_redis.srem.assert_any_call(
            f"{UNIFIED_RUNTIME_SET_PREFIX}faster-whisper", "fw-abc123"
        )
        mock_redis.srem.assert_any_call(
            f"{UNIFIED_STAGE_SET_PREFIX}transcribe", "fw-abc123"
        )

    @pytest.mark.asyncio
    async def test_get_by_instance_found(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.hgetall.return_value = {
            "instance": "fw-abc123",
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "capacity": "4",
            "active_batch": "1",
            "active_realtime": "0",
            "last_heartbeat": now,
            "registered_at": now,
        }

        record = await registry.get_by_instance("fw-abc123")

        assert record is not None
        assert record.instance == "fw-abc123"
        assert record.runtime == "faster-whisper"
        assert record.capacity == 4
        assert record.active_batch == 1

    @pytest.mark.asyncio
    async def test_get_by_instance_not_found(self, registry, mock_redis):
        mock_redis.hgetall.return_value = {}

        record = await registry.get_by_instance("nonexistent")

        assert record is None

    @pytest.mark.asyncio
    async def test_get_all(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"fw-1", "fw-2"}

        async def mock_hgetall(key):
            if "fw-1" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "last_heartbeat": now,
                }
            elif "fw-2" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "processing",
                    "interfaces": '["batch"]',
                    "last_heartbeat": now,
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        records = await registry.get_all()

        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_get_by_runtime(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"fw-1"}
        mock_redis.hgetall.return_value = {
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "last_heartbeat": now,
        }

        records = await registry.get_by_runtime("faster-whisper")

        assert len(records) == 1
        assert records[0].runtime == "faster-whisper"
        mock_redis.smembers.assert_called_with(
            f"{UNIFIED_RUNTIME_SET_PREFIX}faster-whisper"
        )

    @pytest.mark.asyncio
    async def test_get_by_stage(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"fw-1"}
        mock_redis.hgetall.return_value = {
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "last_heartbeat": now,
        }

        records = await registry.get_by_stage("transcribe")

        assert len(records) == 1
        mock_redis.smembers.assert_called_with(f"{UNIFIED_STAGE_SET_PREFIX}transcribe")

    @pytest.mark.asyncio
    async def test_get_available_filters_by_interface(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"batch-1", "rt-1"}

        async def mock_hgetall(key):
            if "batch-1" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "capacity": "4",
                    "active_batch": "0",
                    "active_realtime": "0",
                    "last_heartbeat": now,
                }
            elif "rt-1" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "ready",
                    "interfaces": '["realtime"]',
                    "capacity": "2",
                    "active_batch": "0",
                    "active_realtime": "0",
                    "last_heartbeat": now,
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # Only realtime
        rt_available = await registry.get_available(
            stage="transcribe", interface="realtime"
        )
        assert len(rt_available) == 1
        assert rt_available[0].instance == "rt-1"

        # Only batch
        batch_available = await registry.get_available(
            stage="transcribe", interface="batch"
        )
        assert len(batch_available) == 1
        assert batch_available[0].instance == "batch-1"

    @pytest.mark.asyncio
    async def test_get_available_filters_by_language(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"en-only", "multilingual"}

        async def mock_hgetall(key):
            if "en-only" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "capacity": "4",
                    "active_batch": "0",
                    "active_realtime": "0",
                    "languages": '["en"]',
                    "last_heartbeat": now,
                }
            elif "multilingual" in key:
                return {
                    "runtime": "nemo",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "capacity": "4",
                    "active_batch": "0",
                    "active_realtime": "0",
                    "languages": '["en", "es", "fr"]',
                    "last_heartbeat": now,
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        # French - only multilingual matches
        fr_available = await registry.get_available(stage="transcribe", language="fr")
        assert len(fr_available) == 1
        assert fr_available[0].instance == "multilingual"

        # English - both match
        en_available = await registry.get_available(stage="transcribe", language="en")
        assert len(en_available) == 2

    @pytest.mark.asyncio
    async def test_get_available_sorted_by_capacity(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"low-cap", "high-cap"}

        async def mock_hgetall(key):
            if "low-cap" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "capacity": "4",
                    "active_batch": "3",
                    "active_realtime": "0",
                    "last_heartbeat": now,
                }
            elif "high-cap" in key:
                return {
                    "runtime": "faster-whisper",
                    "stage": "transcribe",
                    "status": "idle",
                    "interfaces": '["batch"]',
                    "capacity": "4",
                    "active_batch": "0",
                    "active_realtime": "0",
                    "last_heartbeat": now,
                }
            return {}

        mock_redis.hgetall.side_effect = mock_hgetall

        available = await registry.get_available(stage="transcribe")
        assert len(available) == 2
        assert available[0].instance == "high-cap"  # More capacity first

    @pytest.mark.asyncio
    async def test_get_engine(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"fw-1"}
        mock_redis.hgetall.return_value = {
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "capacity": "4",
            "active_batch": "0",
            "active_realtime": "0",
            "last_heartbeat": now,
        }

        record = await registry.get_engine("faster-whisper")

        assert record is not None
        assert record.runtime == "faster-whisper"

    @pytest.mark.asyncio
    async def test_get_engine_not_found(self, registry, mock_redis):
        mock_redis.smembers.return_value = set()

        record = await registry.get_engine("nonexistent")

        assert record is None

    @pytest.mark.asyncio
    async def test_is_engine_available_true(self, registry, mock_redis):
        now = datetime.now(UTC).isoformat()
        mock_redis.smembers.return_value = {"fw-1"}
        mock_redis.hgetall.return_value = {
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "last_heartbeat": now,
        }

        assert await registry.is_engine_available("faster-whisper") is True

    @pytest.mark.asyncio
    async def test_is_engine_available_stale(self, registry, mock_redis):
        old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        mock_redis.smembers.return_value = {"fw-1"}
        mock_redis.hgetall.return_value = {
            "runtime": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "last_heartbeat": old,
        }

        assert await registry.is_engine_available("faster-whisper") is False

    @pytest.mark.asyncio
    async def test_mark_instance_offline(self, registry, mock_redis):
        await registry.mark_instance_offline("fw-abc123")

        mock_redis.hset.assert_called_once_with(
            f"{UNIFIED_INSTANCE_KEY_PREFIX}fw-abc123", "status", "offline"
        )


# ---------------------------------------------------------------------------
# UnifiedRegistryWriter (sync, client-side) tests
# ---------------------------------------------------------------------------


class TestUnifiedRegistryWriter:
    """Tests for sync registry writer used by batch engines."""

    @pytest.fixture
    def mock_redis(self):
        mock = MagicMock()
        mock.hset = MagicMock()
        mock.expire = MagicMock()
        mock.sadd = MagicMock()
        mock.srem = MagicMock()
        mock.delete = MagicMock()
        mock.hmget = MagicMock(return_value=["faster-whisper", "transcribe"])
        mock.close = MagicMock()
        return mock

    @pytest.fixture
    def writer(self, mock_redis) -> UnifiedRegistryWriter:
        w = UnifiedRegistryWriter("redis://localhost:6379")
        w._redis = mock_redis
        return w

    def test_register(self, writer, mock_redis):
        record = EngineRecord(
            instance="fw-abc123",
            runtime="faster-whisper",
            stage="transcribe",
            status="idle",
            interfaces=["batch"],
            stream_name="dalston:stream:faster-whisper",
        )

        writer.register(record)

        mock_redis.hset.assert_called_once()
        mock_redis.expire.assert_called_once()
        assert mock_redis.sadd.call_count == 3

    def test_heartbeat(self, writer, mock_redis):
        writer.heartbeat(
            "fw-abc123",
            status="processing",
            active_batch=1,
            loaded_model="model-v3",
        )

        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert mapping["status"] == "processing"
        assert mapping["active_batch"] == "1"
        assert mapping["loaded_model"] == "model-v3"
        mock_redis.expire.assert_called_once()

    def test_deregister(self, writer, mock_redis):
        writer.deregister("fw-abc123")

        mock_redis.delete.assert_called_once()
        assert mock_redis.srem.call_count == 3

    def test_close(self, writer, mock_redis):
        writer.close()

        mock_redis.close.assert_called_once()
        assert writer._redis is None
