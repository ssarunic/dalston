"""Unit tests for console stream backlog helpers."""

from unittest.mock import AsyncMock

import pytest

from dalston.gateway.api.console import _get_stream_backlog


@pytest.mark.asyncio
async def test_get_stream_backlog_reads_consumer_group_lag():
    """Backlog should come from the engines consumer group lag."""
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        return_value=[
            {"name": "other-group", "lag": "99"},
            {"name": "engines", "lag": "7"},
        ]
    )

    backlog = await _get_stream_backlog(redis, "dalston:stream:faster-whisper")

    assert backlog == 7


@pytest.mark.asyncio
async def test_get_stream_backlog_handles_bytes_and_missing_group():
    """Helper should decode byte fields and return zero when group is missing."""
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(return_value=[{"name": b"engines", "lag": b"3"}])

    assert await _get_stream_backlog(redis, "dalston:stream:faster-whisper") == 3

    redis.xinfo_groups = AsyncMock(return_value=[{"name": "not-engines", "lag": "11"}])
    assert await _get_stream_backlog(redis, "dalston:stream:faster-whisper") == 0
