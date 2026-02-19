"""Unit tests for durable events module.

Tests the Redis Streams-based durable event delivery system.
"""

import json
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ResponseError


class TestEnsureEventsStreamGroup:
    """Tests for ensure_events_stream_group function."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_creates_group(self, mock_redis):
        """Test creating consumer group."""
        from dalston.common.durable_events import ensure_events_stream_group

        mock_redis.xgroup_create = AsyncMock()

        await ensure_events_stream_group(mock_redis)

        mock_redis.xgroup_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_existing_group(self, mock_redis):
        """Test that BUSYGROUP error is ignored."""
        from dalston.common.durable_events import ensure_events_stream_group

        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )

        # Should not raise
        await ensure_events_stream_group(mock_redis)


class TestAddDurableEvent:
    """Tests for add_durable_event function."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xadd = AsyncMock(return_value="1234567890-0")
        return redis

    @pytest.mark.asyncio
    async def test_adds_event_to_stream(self, mock_redis):
        """Test adding an event to the stream."""
        from dalston.common.durable_events import EVENTS_STREAM, add_durable_event

        msg_id = await add_durable_event(
            mock_redis,
            "job.created",
            {"job_id": "test-job-123"},
        )

        assert msg_id == "1234567890-0"
        mock_redis.xadd.assert_called_once()

        # Check stream key
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == EVENTS_STREAM

    @pytest.mark.asyncio
    async def test_event_contains_type_and_timestamp(self, mock_redis):
        """Test that events have type and timestamp fields."""
        from dalston.common.durable_events import add_durable_event

        await add_durable_event(
            mock_redis,
            "task.completed",
            {"task_id": "task-456"},
        )

        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]

        assert event_data["type"] == "task.completed"
        assert "timestamp" in event_data
        assert "payload" in event_data

        # Payload should be JSON
        payload = json.loads(event_data["payload"])
        assert payload["task_id"] == "task-456"


class TestReadPendingEvents:
    """Tests for read_pending_events function."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_reads_pending_events(self, mock_redis):
        """Test reading pending events."""
        from dalston.common.durable_events import read_pending_events

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                [
                    "dalston:events:stream",
                    [
                        [
                            "1234567890-0",
                            {
                                "type": "job.created",
                                "timestamp": "2024-01-01T00:00:00+00:00",
                                "payload": '{"job_id": "test-123"}',
                            },
                        ]
                    ],
                ]
            ]
        )

        events = await read_pending_events(mock_redis, "consumer-1")

        assert len(events) == 1
        assert events[0]["id"] == "1234567890-0"
        assert events[0]["type"] == "job.created"
        assert events[0]["job_id"] == "test-123"

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_pending(self, mock_redis):
        """Test returning empty list when no pending events."""
        from dalston.common.durable_events import read_pending_events

        mock_redis.xreadgroup = AsyncMock(return_value=None)

        events = await read_pending_events(mock_redis, "consumer-1")

        assert events == []


class TestReadNewEvents:
    """Tests for read_new_events function."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_reads_new_events(self, mock_redis):
        """Test reading new events."""
        from dalston.common.durable_events import read_new_events

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                [
                    "dalston:events:stream",
                    [
                        [
                            "1234567890-0",
                            {
                                "type": "task.completed",
                                "timestamp": "2024-01-01T00:00:00+00:00",
                                "payload": '{"task_id": "task-456"}',
                            },
                        ]
                    ],
                ]
            ]
        )

        events = await read_new_events(mock_redis, "consumer-1")

        assert len(events) == 1
        assert events[0]["type"] == "task.completed"

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self, mock_redis):
        """Test returning empty list on timeout."""
        from dalston.common.durable_events import read_new_events

        mock_redis.xreadgroup = AsyncMock(return_value=None)

        events = await read_new_events(mock_redis, "consumer-1")

        assert events == []


class TestAckEvent:
    """Tests for ack_event function."""

    @pytest.mark.asyncio
    async def test_acknowledges_event(self):
        """Test acknowledging an event."""
        from dalston.common.durable_events import (
            EVENTS_CONSUMER_GROUP,
            EVENTS_STREAM,
            ack_event,
        )

        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()

        await ack_event(mock_redis, "1234567890-0")

        mock_redis.xack.assert_called_once_with(
            EVENTS_STREAM,
            EVENTS_CONSUMER_GROUP,
            "1234567890-0",
        )


class TestGetStreamInfo:
    """Tests for get_stream_info function."""

    @pytest.mark.asyncio
    async def test_gets_stream_info(self):
        """Test getting stream information."""
        from dalston.common.durable_events import get_stream_info

        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(return_value=100)
        mock_redis.xpending = AsyncMock(
            return_value={
                "pending": 5,
                "consumers": [{"name": "orch-1", "pending": 5}],
            }
        )

        info = await get_stream_info(mock_redis)

        assert info["stream_length"] == 100
        assert info["pending_count"] == 5
        assert len(info["consumers"]) == 1


class TestPublishEventDurability:
    """Tests for publish_event dual-write behavior."""

    @pytest.mark.asyncio
    async def test_critical_event_writes_to_stream(self):
        """Test that critical events are written to durable stream."""
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234567890-0")

        await publish_event(
            mock_redis,
            "job.created",  # Critical event
            {"job_id": "test-123"},
        )

        # Should publish to pub/sub
        mock_redis.publish.assert_called_once()
        # Should also write to stream
        mock_redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_critical_event_skips_stream(self):
        """Test that non-critical events skip durable stream."""
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock()

        await publish_event(
            mock_redis,
            "some.other.event",  # Not in DURABLE_EVENT_TYPES
            {"data": "test"},
        )

        # Should publish to pub/sub
        mock_redis.publish.assert_called_once()
        # Should NOT write to stream
        mock_redis.xadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_durable_override_true(self):
        """Test forcing durability with durable=True."""
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1234567890-0")

        await publish_event(
            mock_redis,
            "custom.event",
            {"data": "test"},
            durable=True,  # Force durability
        )

        mock_redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_durable_override_false(self):
        """Test disabling durability with durable=False."""
        from dalston.common.events import publish_event

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xadd = AsyncMock()

        await publish_event(
            mock_redis,
            "job.created",  # Normally durable
            {"job_id": "test"},
            durable=False,  # Disable durability
        )

        mock_redis.xadd.assert_not_called()
