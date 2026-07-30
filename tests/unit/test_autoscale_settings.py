"""Unit tests for the M95 autoscale external-inheritance source.

The load-bearing rule: never present a value the autoscaler isn't
actually using. A stale, missing, or unparseable snapshot must report
"unknown", not a code default.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.gateway.services.autoscale_settings import (
    EXTERNAL_INHERITANCE_SOURCES,
    AutoscaleInheritanceSource,
)

KEYS = ["tasks_per_instance", "min_instances", "max_instances", "scale_down_after_s"]


def _snapshot(*, ts: datetime | None = None, **policy_overrides) -> str:
    policy = {
        "tasks_per_instance": 20,
        "min_instances": 0,
        "max_instances": 3,
        "scale_down_after_s": 2100,
        "overrides_applied": [],
        "override_error": None,
    }
    policy.update(policy_overrides)
    return json.dumps(
        {
            "schema_version": 1,
            "shape": "nemo+pyannote",
            "ts": (ts or datetime.now(UTC)).isoformat(),
            "policy": policy,
        }
    )


def _redis(marker: dict[str, str], ticks: dict[str, str]) -> MagicMock:
    r = MagicMock()
    r.hlen = AsyncMock(return_value=len(marker))
    r.hgetall = AsyncMock(return_value=marker)
    r.get = AsyncMock(side_effect=lambda key: ticks.get(key.split(":")[-1]))
    return r


@pytest.mark.asyncio
class TestIsActive:
    async def test_inactive_when_marker_empty(self):
        """Lite / non-autoscaled deployments must not see the tab."""
        source = AutoscaleInheritanceSource()
        assert await source.is_active(_redis({}, {})) is False

    async def test_active_when_marker_present(self):
        source = AutoscaleInheritanceSource()
        r = _redis({"nemo+pyannote": datetime.now(UTC).isoformat()}, {})
        assert await source.is_active(r) is True

    async def test_redis_failure_is_inactive_not_an_error(self):
        source = AutoscaleInheritanceSource()
        r = MagicMock()
        r.hlen = AsyncMock(side_effect=ConnectionError("down"))
        assert await source.is_active(r) is False


@pytest.mark.asyncio
class TestEffectiveValues:
    async def _values(self, marker, ticks):
        source = AutoscaleInheritanceSource()
        return await source.effective_values(_redis(marker, ticks), KEYS)

    async def test_reads_the_policy_echo(self):
        now = datetime.now(UTC)
        values = await self._values(
            {"nemo+pyannote": now.isoformat()},
            {"nemo+pyannote": _snapshot()},
        )
        assert values["max_instances"].known is True
        # the hand-tuned YAML value, not the code default of 5
        assert values["max_instances"].value == 3
        assert values["tasks_per_instance"].value == 20

    async def test_unknown_when_no_snapshot(self):
        now = datetime.now(UTC)
        values = await self._values({"nemo+pyannote": now.isoformat()}, {})
        assert all(not v.known for v in values.values())
        assert all(v.value is None for v in values.values())

    async def test_unknown_when_stale(self):
        """A silent autoscaler must read as unknown, never as a value."""
        old = datetime.now(UTC) - timedelta(minutes=10)
        values = await self._values(
            {"nemo+pyannote": old.isoformat()},
            {"nemo+pyannote": _snapshot(ts=old)},
        )
        assert all(not v.known for v in values.values())

    async def test_unknown_when_marker_empty(self):
        values = await self._values({}, {})
        assert all(not v.known for v in values.values())

    async def test_unknown_on_corrupt_snapshot(self):
        now = datetime.now(UTC)
        values = await self._values(
            {"nemo+pyannote": now.isoformat()}, {"nemo+pyannote": "{not json"}
        )
        assert all(not v.known for v in values.values())

    async def test_unknown_on_future_schema_version(self):
        now = datetime.now(UTC)
        raw = json.loads(_snapshot())
        raw["schema_version"] = 99
        values = await self._values(
            {"nemo+pyannote": now.isoformat()},
            {"nemo+pyannote": json.dumps(raw)},
        )
        assert all(not v.known for v in values.values())

    async def test_override_error_propagated_to_every_key(self):
        now = datetime.now(UTC)
        values = await self._values(
            {"nemo+pyannote": now.isoformat()},
            {"nemo+pyannote": _snapshot(override_error="min_instances > max")},
        )
        assert all(v.override_error == "min_instances > max" for v in values.values())

    async def test_first_fresh_shape_is_representative(self):
        """Overrides are global; a stale first shape falls through to a
        fresh one rather than reporting unknown."""
        now = datetime.now(UTC)
        old = now - timedelta(minutes=10)
        values = await self._values(
            {"aaa-stale": old.isoformat(), "zzz-fresh": now.isoformat()},
            {
                "aaa-stale": _snapshot(ts=old, max_instances=9),
                "zzz-fresh": _snapshot(ts=now, max_instances=3),
            },
        )
        assert values["max_instances"].value == 3


def test_autoscale_source_is_registered():
    assert "autoscale" in EXTERNAL_INHERITANCE_SOURCES
