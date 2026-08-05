"""Regression tests for M101 — heartbeats must preserve identifying fields.

Instance keys carry a TTL. The full record is written once by
``register()``; if the key ever lapses, the next heartbeat re-creates it
from scratch with only the fields that heartbeat sends. Anything omitted
is gone permanently, because the heartbeat keeps refreshing the truncated
key.

In production this dropped a realtime worker from the registry entirely
(``engine_id`` missing → ``_mapping_to_record`` returns ``None``), which
made the console report zero workers *and* made the autoscaler count the
instance as pending until the boot-timeout reaper terminated it.

These tests reproduce the exact sequence: register → key deleted →
heartbeat → the record must still be valid and correctly typed.
"""

from __future__ import annotations

import inspect

import pytest

from dalston.common.registry import (
    UNIFIED_INSTANCE_KEY_PREFIX,
    EngineRecord,
    UnifiedEngineRegistry,
    UnifiedRegistryWriter,
    _mapping_to_record,
)


def _realtime_record() -> EngineRecord:
    return EngineRecord(
        instance="nemo-rt-i-deadbeef",
        engine_id="nemo",
        stage="transcribe",
        status="ready",
        interfaces=["realtime"],
        capacity=2,
        endpoint="ws://100.79.21.83:9000",
    )


def _batch_record() -> EngineRecord:
    return EngineRecord(
        instance="nemo-i-deadbeef",
        engine_id="nemo",
        stage="transcribe",
        status="idle",
        interfaces=["batch"],
        capacity=1,
        endpoint="",
    )


class _FakeRedis:
    """Minimal hash store standing in for Redis, sync and async alike."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    # -- shared -------------------------------------------------------
    def _hset(self, key: str, mapping: dict[str, str]) -> None:
        # Real HSET merges into the existing hash and never removes fields.
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def expire_key(self, key: str) -> None:
        """Simulate the TTL lapsing — the key vanishes entirely."""
        self.hashes.pop(key, None)
        self.ttls.pop(key, None)

    # -- sync surface -------------------------------------------------
    def hset(self, key, mapping=None, **_):  # noqa: ANN001
        self._hset(key, mapping or {})
        return 1

    def expire(self, key, ttl):  # noqa: ANN001
        self.ttls[key] = ttl
        return True

    def sadd(self, key, *members):  # noqa: ANN001
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    # -- async surface ------------------------------------------------
    async def ahset(self, key, mapping=None, **_):  # noqa: ANN001
        self._hset(key, mapping or {})
        return 1

    async def aexpire(self, key, ttl):  # noqa: ANN001
        self.ttls[key] = ttl
        return True

    async def asadd(self, key, *members):  # noqa: ANN001
        self.sets.setdefault(key, set()).update(members)
        return len(members)


class _AsyncFakeRedis:
    """Async-shaped view over :class:`_FakeRedis`."""

    def __init__(self, backing: _FakeRedis) -> None:
        self._b = backing

    async def hset(self, key, mapping=None, **kw):  # noqa: ANN001
        return await self._b.ahset(key, mapping, **kw)

    async def expire(self, key, ttl):  # noqa: ANN001
        return await self._b.aexpire(key, ttl)

    async def sadd(self, key, *members):  # noqa: ANN001
        return await self._b.asadd(key, *members)


def _key(instance: str) -> str:
    return f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"


class TestAsyncHeartbeatSurvivesExpiry:
    """The realtime path — this is the one that broke in production."""

    @pytest.mark.asyncio
    async def test_recreated_key_still_resolves_to_a_record(self) -> None:
        backing = _FakeRedis()
        reg = UnifiedEngineRegistry(_AsyncFakeRedis(backing))
        rec = _realtime_record()

        await reg.register(rec)
        backing.expire_key(_key(rec.instance))  # TTL lapses

        await reg.heartbeat(
            rec.instance,
            status="ready",
            active_realtime=0,
            engine_id=rec.engine_id,
            stage=rec.stage,
            interfaces=list(rec.interfaces),
            endpoint=rec.endpoint,
            capacity=rec.capacity,
        )

        mapping = backing.hashes[_key(rec.instance)]
        restored = _mapping_to_record(rec.instance, mapping)

        # Before M101 this was None — the record was dropped outright.
        assert restored is not None, "record dropped after key re-creation"
        assert restored.engine_id == "nemo"

    @pytest.mark.asyncio
    async def test_recreated_key_stays_realtime_not_defaulted_to_batch(self) -> None:
        """Restoring engine_id alone is not enough.

        ``_mapping_to_record`` defaults a missing ``interfaces`` to
        ``["batch"]``, so without it a realtime engine returns mis-typed
        and stays invisible to ``list_workers()``.
        """
        backing = _FakeRedis()
        reg = UnifiedEngineRegistry(_AsyncFakeRedis(backing))
        rec = _realtime_record()

        await reg.register(rec)
        backing.expire_key(_key(rec.instance))

        await reg.heartbeat(
            rec.instance,
            engine_id=rec.engine_id,
            stage=rec.stage,
            interfaces=list(rec.interfaces),
            endpoint=rec.endpoint,
            capacity=rec.capacity,
        )

        restored = _mapping_to_record(rec.instance, backing.hashes[_key(rec.instance)])
        assert restored is not None
        assert restored.interfaces == ["realtime"]
        assert restored.supports_interface("realtime") is True

    @pytest.mark.asyncio
    async def test_endpoint_and_capacity_survive_so_allocation_still_works(
        self,
    ) -> None:
        backing = _FakeRedis()
        reg = UnifiedEngineRegistry(_AsyncFakeRedis(backing))
        rec = _realtime_record()

        await reg.register(rec)
        backing.expire_key(_key(rec.instance))
        await reg.heartbeat(
            rec.instance,
            engine_id=rec.engine_id,
            stage=rec.stage,
            interfaces=list(rec.interfaces),
            endpoint=rec.endpoint,
            capacity=rec.capacity,
        )

        restored = _mapping_to_record(rec.instance, backing.hashes[_key(rec.instance)])
        assert restored is not None
        assert restored.endpoint == "ws://100.79.21.83:9000"
        assert restored.capacity == 2

    @pytest.mark.asyncio
    async def test_instance_is_always_written(self) -> None:
        backing = _FakeRedis()
        reg = UnifiedEngineRegistry(_AsyncFakeRedis(backing))

        await reg.heartbeat("nemo-rt-i-deadbeef", status="ready")

        assert backing.hashes[_key("nemo-rt-i-deadbeef")]["instance"] == (
            "nemo-rt-i-deadbeef"
        )


class TestSyncHeartbeatSurvivesExpiry:
    """The batch path — already guarded for engine_id, now for interfaces."""

    def test_recreated_key_still_resolves(self) -> None:
        backing = _FakeRedis()
        writer = UnifiedRegistryWriter("redis://unused")
        writer._redis = backing  # type: ignore[attr-defined]
        rec = _batch_record()

        writer.register(rec)
        backing.expire_key(_key(rec.instance))
        writer.heartbeat(
            rec.instance,
            status="idle",
            engine_id=rec.engine_id,
            stage=rec.stage,
            interfaces=list(rec.interfaces),
        )

        restored = _mapping_to_record(rec.instance, backing.hashes[_key(rec.instance)])
        assert restored is not None
        assert restored.interfaces == ["batch"]

    def test_unified_engine_keeps_both_interfaces(self) -> None:
        """A ["batch", "realtime"] engine must not lose its realtime half."""
        backing = _FakeRedis()
        writer = UnifiedRegistryWriter("redis://unused")
        writer._redis = backing  # type: ignore[attr-defined]
        rec = EngineRecord(
            instance="unified-i-deadbeef",
            engine_id="whisper",
            stage="transcribe",
            status="idle",
            interfaces=["batch", "realtime"],
            capacity=1,
            endpoint="ws://100.1.2.3:9000",
        )

        writer.register(rec)
        backing.expire_key(_key(rec.instance))
        writer.heartbeat(
            rec.instance,
            engine_id=rec.engine_id,
            stage=rec.stage,
            interfaces=list(rec.interfaces),
        )

        restored = _mapping_to_record(rec.instance, backing.hashes[_key(rec.instance)])
        assert restored is not None
        assert restored.supports_interface("realtime") is True
        assert restored.supports_interface("batch") is True


class TestWriterParity:
    """The two writers drifted apart once; stop it happening again."""

    _IDENTIFYING = {
        "engine_id",
        "stage",
        "interfaces",
        "endpoint",
        "capacity",
    }

    def test_both_heartbeats_accept_the_identifying_fields(self) -> None:
        async_params = set(
            inspect.signature(UnifiedEngineRegistry.heartbeat).parameters
        )
        sync_params = set(inspect.signature(UnifiedRegistryWriter.heartbeat).parameters)

        missing_async = self._IDENTIFYING - async_params
        missing_sync = self._IDENTIFYING - sync_params

        assert not missing_async, f"async heartbeat missing: {sorted(missing_async)}"
        assert not missing_sync, f"sync heartbeat missing: {sorted(missing_sync)}"

    def test_signatures_do_not_diverge(self) -> None:
        async_params = set(
            inspect.signature(UnifiedEngineRegistry.heartbeat).parameters
        )
        sync_params = set(inspect.signature(UnifiedRegistryWriter.heartbeat).parameters)

        # `self` is common; compare the rest.
        assert async_params - {"self"} == sync_params - {"self"}, (
            "heartbeat signatures have diverged — a field written by one "
            "writer but not the other will be lost on key re-creation"
        )
