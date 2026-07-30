"""Unit tests for the M95 autoscaler block on /api/console/nodes.

Covers the load-bearing degradation rules: the shapes marker (not snapshot
presence) decides which shapes exist; a corrupt or newer-schema snapshot
degrades that one shape, never the whole response; the earliest wait
deadline comes from the waiting-tasks set filtered by shape engines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.common.registry import (
    AUTOSCALE_SHAPES_KEY,
    AUTOSCALE_TICK_KEY_PREFIX,
)
from dalston.common.streams_types import WAITING_ENGINE_TASKS_KEY
from dalston.gateway.api.console import NodesResponse, get_nodes

NOW = datetime(2026, 7, 30, 12, 4, 0, tzinfo=UTC)


def _tick_snapshot(shape: str = "nemo+pyannote", **overrides) -> dict:
    snap = {
        "schema_version": 1,
        "shape": shape,
        "ts": NOW.isoformat(),
        "action": "none",
        "applied": "at desired capacity",
        "desired": 2,
        "live": 2,
        "pending": 0,
        "spot_live": 2,
        "on_demand_live": 0,
        "max_backlog": 41,
        "reason": "at desired capacity",
        "per_engine": {
            "nemo": {"lag": 41, "in_flight": 0},
            "pyannote-4.0": {"lag": 0, "in_flight": 0},
        },
        "idle_since_s": None,
        "policy": {
            "tasks_per_instance": 20,
            "min_instances": 0,
            "max_instances": 5,
            "scale_down_after_s": 2100,
            "overrides_applied": [],
            "override_error": None,
        },
        "blocked": None,
    }
    snap.update(overrides)
    return snap


class _FakePipeline:
    """Collects hmget calls; execute() returns canned per-task rows."""

    def __init__(self, rows_by_key: dict[str, list]):
        self._rows_by_key = rows_by_key
        self._keys: list[str] = []

    def hmget(self, key: str, *fields) -> None:
        self._keys.append(key)

    async def execute(self) -> list[list]:
        return [self._rows_by_key.get(k, [None, None]) for k in self._keys]


def _async_iter(items: list[str]):
    async def gen():
        for item in items:
            yield item

    return gen()


def _make_redis(
    *,
    shapes_marker: dict[str, str] | None = None,
    ticks: dict[str, str] | None = None,
    waiting_task_ids: set[str] | None = None,
    task_rows: dict[str, list] | None = None,
) -> AsyncMock:
    redis = AsyncMock()
    marker = shapes_marker or {}
    tick_map = ticks or {}

    async def smembers(key: str):
        if key == WAITING_ENGINE_TASKS_KEY:
            return waiting_task_ids or set()
        return set()  # unified instance set — no engine nodes in these tests

    async def hgetall(key: str):
        if key == AUTOSCALE_SHAPES_KEY:
            return marker
        return {}

    async def get(key: str):
        if key.startswith(AUTOSCALE_TICK_KEY_PREFIX):
            return tick_map.get(key.removeprefix(AUTOSCALE_TICK_KEY_PREFIX))
        return None

    redis.smembers = AsyncMock(side_effect=smembers)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.get = AsyncMock(side_effect=get)
    redis.scan_iter = MagicMock(return_value=_async_iter([]))
    redis.pipeline = MagicMock(return_value=_FakePipeline(task_rows or {}))
    return redis


@pytest.mark.asyncio
class TestAutoscalerBlock:
    async def _call(self, redis: AsyncMock) -> NodesResponse:
        sm = MagicMock()
        sm.require_permission = MagicMock()
        with (
            patch(
                "dalston.gateway.api.console.get_security_manager",
                return_value=sm,
            ),
            patch(
                "dalston.gateway.api.console.datetime",
                wraps=datetime,
            ) as mock_dt,
        ):
            mock_dt.now = MagicMock(return_value=NOW)
            return await get_nodes(principal=MagicMock(), redis=redis)

    async def test_empty_marker_returns_empty_list(self):
        result = await self._call(_make_redis())
        assert result.autoscaler == []

    async def test_fresh_snapshot_builds_full_view(self):
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": NOW.isoformat()},
            ticks={"nemo+pyannote": json.dumps(_tick_snapshot())},
        )
        result = await self._call(redis)

        assert len(result.autoscaler) == 1
        view = result.autoscaler[0]
        assert view.shape == "nemo+pyannote"
        assert view.stale is False
        assert view.desired == 2
        assert view.live == 2
        assert view.max_backlog == 41
        assert view.policy is not None
        assert view.policy.tasks_per_instance == 20
        assert view.blocked is None

    async def test_expired_snapshot_marked_stale_with_null_fields(self):
        last_tick = NOW - timedelta(hours=2)
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": last_tick.isoformat()},
            ticks={},  # tick key expired
        )
        result = await self._call(redis)

        view = result.autoscaler[0]
        assert view.stale is True
        assert view.last_tick_at == last_tick
        assert view.desired is None
        assert view.policy is None

    async def test_old_ts_marks_stale(self):
        old = (NOW - timedelta(minutes=10)).isoformat()
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": old},
            ticks={"nemo+pyannote": json.dumps(_tick_snapshot(ts=old))},
        )
        result = await self._call(redis)
        assert result.autoscaler[0].stale is True

    async def test_corrupt_snapshot_degrades_one_shape_only(self):
        redis = _make_redis(
            shapes_marker={
                "bad-shape": NOW.isoformat(),
                "nemo+pyannote": NOW.isoformat(),
            },
            ticks={
                "bad-shape": "{not json",
                "nemo+pyannote": json.dumps(_tick_snapshot()),
            },
        )
        result = await self._call(redis)

        assert len(result.autoscaler) == 2
        by_shape = {v.shape: v for v in result.autoscaler}
        assert by_shape["bad-shape"].stale is True
        assert by_shape["bad-shape"].desired is None
        assert by_shape["nemo+pyannote"].stale is False
        assert by_shape["nemo+pyannote"].desired == 2

    async def test_unknown_schema_version_degrades_shape(self):
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": NOW.isoformat()},
            ticks={
                "nemo+pyannote": json.dumps(_tick_snapshot(schema_version=99)),
            },
        )
        result = await self._call(redis)
        view = result.autoscaler[0]
        assert view.stale is True
        assert view.desired is None

    async def test_blocked_state_parsed(self):
        blocked = {
            "kind": "spot_quota",
            "since": (NOW - timedelta(minutes=6)).isoformat(),
            "consecutive_ticks": 6,
            "detail": "MaxSpotInstanceCountExceeded",
        }
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": NOW.isoformat()},
            ticks={"nemo+pyannote": json.dumps(_tick_snapshot(blocked=blocked))},
        )
        result = await self._call(redis)
        view = result.autoscaler[0]
        assert view.blocked is not None
        assert view.blocked.kind == "spot_quota"
        assert view.blocked.consecutive_ticks == 6

    async def test_earliest_wait_deadline_filters_by_shape_engines(self):
        deadline_in = (NOW + timedelta(minutes=18)).isoformat()
        deadline_other = (NOW + timedelta(minutes=2)).isoformat()
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": NOW.isoformat()},
            ticks={"nemo+pyannote": json.dumps(_tick_snapshot())},
            waiting_task_ids={"t1", "t2"},
            task_rows={
                "dalston:task:t1": ["nemo", deadline_in],
                # earlier deadline, but for an engine outside the shape
                "dalston:task:t2": ["some-other-engine", deadline_other],
            },
        )
        result = await self._call(redis)
        view = result.autoscaler[0]
        assert view.earliest_wait_deadline_at is not None
        assert view.earliest_wait_deadline_at.isoformat() == deadline_in

    async def test_no_waiting_tasks_leaves_deadline_null(self):
        redis = _make_redis(
            shapes_marker={"nemo+pyannote": NOW.isoformat()},
            ticks={"nemo+pyannote": json.dumps(_tick_snapshot())},
        )
        result = await self._call(redis)
        assert result.autoscaler[0].earliest_wait_deadline_at is None
