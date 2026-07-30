"""Unit tests for the M95 Postgres -> Redis overrides mirror.

The mirror's contract: only explicit Postgres rows reach the hash (an
unset nullable setting must NOT clobber the hand-tuned control-plane
YAML), the rewrite is atomic, and Redis failures never raise — the 60s
reconcile loop is the backstop.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.common.registry import AUTOSCALE_OVERRIDES_KEY
from dalston.gateway.services.autoscale_overrides_mirror import (
    AutoscaleOverridesMirror,
)


def _make_pipeline() -> MagicMock:
    pipe = MagicMock()
    pipe.delete = MagicMock()
    pipe.hset = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1])
    return pipe


def _make_mirror(overrides: dict, pipe: MagicMock | None = None):
    pipe = pipe or _make_pipeline()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    @asynccontextmanager
    async def session_factory():
        yield MagicMock()

    mirror = AutoscaleOverridesMirror(redis=redis, session_factory=session_factory)
    mirror._service.get_raw_overrides = AsyncMock(return_value=overrides)
    return mirror, pipe


@pytest.mark.asyncio
class TestSyncFromPostgres:
    async def test_only_explicit_rows_are_mirrored(self):
        """Unset knobs must be absent from the hash — the tick would
        otherwise read a code default over the hand-tuned YAML."""
        mirror, pipe = _make_mirror({"tasks_per_instance": 10})

        assert await mirror.sync_once() is True

        pipe.delete.assert_called_once_with(AUTOSCALE_OVERRIDES_KEY)
        pipe.hset.assert_called_once_with(
            AUTOSCALE_OVERRIDES_KEY, mapping={"tasks_per_instance": "10"}
        )
        pipe.execute.assert_awaited_once()

    async def test_values_stored_as_plain_strings(self):
        mirror, pipe = _make_mirror({"min_instances": 0, "max_instances": 3})
        await mirror.sync_once()
        mapping = pipe.hset.call_args[1]["mapping"]
        assert mapping == {"min_instances": "0", "max_instances": "3"}

    async def test_empty_overrides_deletes_without_hset(self):
        """Reset means 'inherit everything' — the hash must go away."""
        mirror, pipe = _make_mirror({})
        assert await mirror.sync_once() is True
        pipe.delete.assert_called_once_with(AUTOSCALE_OVERRIDES_KEY)
        assert not pipe.hset.called
        pipe.execute.assert_awaited_once()

    async def test_rewrite_is_atomic(self):
        mirror, _ = _make_mirror({"tasks_per_instance": 10})
        await mirror.sync_once()
        mirror._redis.pipeline.assert_called_once_with(transaction=True)

    async def test_redis_failure_returns_false_without_raising(self):
        pipe = _make_pipeline()
        pipe.execute = AsyncMock(side_effect=ConnectionError("redis down"))
        mirror, _ = _make_mirror({"tasks_per_instance": 10}, pipe)

        assert await mirror.sync_once() is False

    async def test_db_failure_returns_false_without_raising(self):
        mirror, _ = _make_mirror({})
        mirror._service.get_raw_overrides = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        assert await mirror.sync_once() is False

    async def test_sync_from_session_uses_caller_session(self):
        """PATCH path: reuse the handler's post-commit session."""
        mirror, pipe = _make_mirror({"tasks_per_instance": 10})
        db = MagicMock()

        assert await mirror.sync_from_session(db) is True
        assert mirror._service.get_raw_overrides.call_args[0][0] is db
        pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
class TestReconcileLoopLifecycle:
    async def test_start_syncs_then_stop_cancels_cleanly(self):
        mirror, pipe = _make_mirror({"tasks_per_instance": 10})

        await mirror.start()
        await asyncio.sleep(0)  # let the loop's first sync run
        await mirror.stop()

        assert pipe.execute.await_count >= 1
        assert mirror._task is None

    async def test_stop_without_start_is_safe(self):
        mirror, _ = _make_mirror({})
        await mirror.stop()  # must not raise

    async def test_loop_heals_a_flushed_redis_on_the_next_tick(self):
        """A failed sync must not kill the loop — that is the whole
        reason the reconcile loop exists."""
        pipe = _make_pipeline()
        pipe.execute = AsyncMock(side_effect=[ConnectionError("flushed"), [1, 1]])
        mirror, _ = _make_mirror({"tasks_per_instance": 10}, pipe)

        assert await mirror.sync_once() is False
        assert await mirror.sync_once() is True
