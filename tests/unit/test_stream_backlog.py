"""M105: ``get_stream_backlog`` — the one reader shared by the selector's
external-engine capacity check and the console queue board."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dalston.common.registry import UnifiedEngineRegistry, get_stream_backlog


def _redis(groups=None, error: Exception | None = None, xrange=None) -> AsyncMock:
    r = AsyncMock()
    if error is not None:
        r.xinfo_groups.side_effect = error
    else:
        r.xinfo_groups.return_value = groups or []
    r.xrange.return_value = xrange or []
    return r


@pytest.mark.asyncio
async def test_missing_stream_reads_as_zero():
    r = _redis(error=Exception("ERR no such key"))
    assert await get_stream_backlog(r, "dalston:stream:x") == 0


@pytest.mark.asyncio
async def test_other_redis_errors_read_as_zero():
    r = _redis(error=Exception("connection reset"))
    assert await get_stream_backlog(r, "dalston:stream:x") == 0


@pytest.mark.asyncio
async def test_lag_plus_pending():
    r = _redis(
        [{"name": "engines", "lag": 7, "pending": 1, "last-delivered-id": "1-0"}]
    )
    assert await get_stream_backlog(r, "dalston:stream:x") == 8


@pytest.mark.asyncio
async def test_pending_excluded_for_queue_board():
    r = _redis([{"name": b"engines", "lag": b"7", "pending": b"1"}])
    assert await get_stream_backlog(r, "dalston:stream:x", include_pending=False) == 7


@pytest.mark.asyncio
async def test_other_groups_are_ignored():
    r = _redis([{"name": "orchestrators", "lag": 99, "pending": 99}])
    assert await get_stream_backlog(r, "dalston:stream:x") == 0


@pytest.mark.asyncio
async def test_nil_lag_counts_surviving_entries_after_last_delivered():
    r = _redis(
        [{"name": "engines", "lag": None, "pending": 2, "last-delivered-id": "5-0"}],
        xrange=[("6-0", {}), ("7-0", {}), ("8-0", {})],
    )
    assert await get_stream_backlog(r, "dalston:stream:x") == 5
    r.xrange.assert_awaited_once_with(
        "dalston:stream:x", min="(5-0", max="+", count=500
    )


@pytest.mark.asyncio
async def test_registry_method_includes_pending():
    r = _redis([{"name": "engines", "lag": 3, "pending": 1}])
    registry = UnifiedEngineRegistry(r)
    assert await registry.get_stream_backlog("dalston:stream:x") == 4


@pytest.mark.asyncio
async def test_heartbeat_carries_stream_name_so_recreated_keys_keep_it():
    r = AsyncMock()
    registry = UnifiedEngineRegistry(r)
    await registry.heartbeat(
        "fluidaudio-mac-tx", status="idle", stream_name="dalston:stream:fluidaudio"
    )
    mapping = r.hset.await_args.kwargs["mapping"]
    assert mapping["stream_name"] == "dalston:stream:fluidaudio"
