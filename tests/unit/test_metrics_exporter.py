"""Unit tests for stream backlog metrics exporter helpers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import dalston.metrics_exporter as metrics_exporter


@pytest.fixture(autouse=True)
def reset_metrics_exporter_state(monkeypatch):
    """Reset module globals between tests."""
    monkeypatch.setattr(metrics_exporter, "_redis", None)


@pytest.mark.asyncio
async def test_get_stream_backlog_state_reads_engines_group(monkeypatch):
    """Backlog state should come from the configured consumer group."""
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        return_value=[
            {"name": "other-group", "lag": "12", "last-delivered-id": "100-0"},
            {"name": "engines", "lag": "5", "last-delivered-id": "150-0"},
        ]
    )
    monkeypatch.setattr(metrics_exporter, "_redis", redis)

    depth, last_delivered_id = await metrics_exporter._get_stream_backlog_state(
        "dalston:stream:faster-whisper"
    )

    assert depth == 5
    assert last_delivered_id == "150-0"


@pytest.mark.asyncio
async def test_get_oldest_task_age_reads_first_undelivered_message(monkeypatch):
    """Oldest age should use first message after last-delivered-id."""
    redis = AsyncMock()
    enqueued_at = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    redis.xrange = AsyncMock(
        return_value=[("151-0", {"task_id": "task-1", "enqueued_at": enqueued_at})]
    )
    monkeypatch.setattr(metrics_exporter, "_redis", redis)

    age = await metrics_exporter._get_oldest_task_age(
        "dalston:stream:faster-whisper",
        depth=2,
        last_delivered_id="150-0",
    )

    assert 0 < age < 120
    redis.xrange.assert_called_once_with(
        "dalston:stream:faster-whisper",
        min="(150-0",
        max="+",
        count=1,
    )


@pytest.mark.asyncio
async def test_collect_queue_metrics_uses_group_lag_not_stream_length(monkeypatch):
    """Collector should report consumer-group lag and avoid XLEN history counts."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.xinfo_groups = AsyncMock(
        return_value=[{"name": "engines", "lag": 4, "last-delivered-id": "200-0"}]
    )
    redis.xrange = AsyncMock(
        return_value=[
            (
                "201-0",
                {
                    "task_id": "task-1",
                    "enqueued_at": (
                        datetime.now(UTC) - timedelta(seconds=12)
                    ).isoformat(),
                },
            )
        ]
    )
    redis.xlen = AsyncMock(
        side_effect=AssertionError("collect_queue_metrics must not call XLEN")
    )

    monkeypatch.setattr(metrics_exporter, "_redis", redis)
    monkeypatch.setattr(metrics_exporter, "KNOWN_ENGINES", ["faster-whisper"])

    with (
        patch("dalston.metrics.set_redis_connected") as set_connected,
        patch("dalston.metrics.set_queue_depth") as set_depth,
        patch("dalston.metrics.set_queue_oldest_task_age") as set_age,
    ):
        await metrics_exporter.collect_queue_metrics()

    set_connected.assert_called_with(True)
    set_depth.assert_called_once_with("faster-whisper", 4)
    assert set_age.call_count == 1
    redis.xlen.assert_not_called()
