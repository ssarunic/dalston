"""Unit tests for Redis Streams helper module.

Tests the streams.py module that provides Redis Streams abstractions
for durable task queues.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ResponseError

from dalston.common.streams import (
    CONSUMER_GROUP,
    PendingTask,
    StreamMessage,
    _parse_message,
    _stream_key,
    ack_task,
    add_task,
    claim_stale_tasks,
    claim_tasks_by_id,
    discover_streams,
    ensure_stream_group,
    get_pending,
    get_stream_info,
    read_task,
)


class TestStreamKey:
    """Tests for stream key building."""

    def test_stream_key(self):
        """Test building stream key from stage."""
        assert _stream_key("transcribe") == "dalston:stream:transcribe"
        assert _stream_key("align") == "dalston:stream:align"
        assert _stream_key("diarize") == "dalston:stream:diarize"


class TestParseMessage:
    """Tests for message parsing."""

    def test_parse_message_full(self):
        """Test parsing a complete message."""
        now = datetime.now(UTC)
        timeout = now + timedelta(seconds=600)

        fields = {
            "task_id": "task-123",
            "job_id": "job-456",
            "enqueued_at": now.isoformat(),
            "timeout_at": timeout.isoformat(),
        }

        msg = _parse_message("1234567890-0", fields, delivery_count=2)

        assert msg.id == "1234567890-0"
        assert msg.task_id == "task-123"
        assert msg.job_id == "job-456"
        assert msg.delivery_count == 2

    def test_parse_message_missing_fields(self):
        """Test parsing message with missing fields uses defaults."""
        fields = {}

        msg = _parse_message("1234567890-0", fields, delivery_count=1)

        assert msg.id == "1234567890-0"
        assert msg.task_id == ""
        assert msg.job_id == ""
        assert msg.delivery_count == 1


class TestEnsureStreamGroup:
    """Tests for ensure_stream_group function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_creates_group(self, mock_redis):
        """Test creating consumer group."""
        mock_redis.xgroup_create = AsyncMock()

        await ensure_stream_group(mock_redis, "transcribe")

        mock_redis.xgroup_create.assert_called_once_with(
            "dalston:stream:transcribe",
            CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )

    @pytest.mark.asyncio
    async def test_ignores_existing_group(self, mock_redis):
        """Test that BUSYGROUP error is ignored (idempotent)."""
        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )

        # Should not raise
        await ensure_stream_group(mock_redis, "transcribe")

    @pytest.mark.asyncio
    async def test_raises_other_errors(self, mock_redis):
        """Test that other errors are raised."""
        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("Some other error")
        )

        with pytest.raises(ResponseError):
            await ensure_stream_group(mock_redis, "transcribe")


class TestAddTask:
    """Tests for add_task function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock = AsyncMock()
        mock.xgroup_create = AsyncMock()
        mock.xadd = AsyncMock(return_value="1234567890-0")
        return mock

    @pytest.mark.asyncio
    async def test_adds_task_to_stream(self, mock_redis):
        """Test adding a task to the stream."""
        msg_id = await add_task(
            mock_redis,
            stage="transcribe",
            task_id="task-123",
            job_id="job-456",
            timeout_s=600,
        )

        assert msg_id == "1234567890-0"
        mock_redis.xadd.assert_called_once()

        # Check the fields
        call_args = mock_redis.xadd.call_args
        stream_key = call_args[0][0]
        fields = call_args[0][1]

        assert stream_key == "dalston:stream:transcribe"
        assert fields["task_id"] == "task-123"
        assert fields["job_id"] == "job-456"
        assert "enqueued_at" in fields
        assert "timeout_at" in fields


class TestReadTask:
    """Tests for read_task function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock = AsyncMock()
        mock.xgroup_create = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_reads_task_from_stream(self, mock_redis):
        """Test reading a task from the stream."""
        now = datetime.now(UTC)
        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                [
                    "dalston:stream:transcribe",
                    [
                        [
                            "1234567890-0",
                            {
                                "task_id": "task-123",
                                "job_id": "job-456",
                                "enqueued_at": now.isoformat(),
                                "timeout_at": (
                                    now + timedelta(seconds=600)
                                ).isoformat(),
                            },
                        ]
                    ],
                ]
            ]
        )

        msg = await read_task(mock_redis, "transcribe", consumer="engine-1")

        assert msg is not None
        assert msg.task_id == "task-123"
        assert msg.job_id == "job-456"
        assert msg.delivery_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, mock_redis):
        """Test returning None when no task available."""
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        msg = await read_task(mock_redis, "transcribe", consumer="engine-1")

        assert msg is None

    @pytest.mark.asyncio
    async def test_handles_nogroup_error(self, mock_redis):
        """Test handling NOGROUP error by creating group."""
        mock_redis.xreadgroup = AsyncMock(
            side_effect=ResponseError("NOGROUP No such consumer group")
        )

        msg = await read_task(mock_redis, "transcribe", consumer="engine-1")

        assert msg is None
        mock_redis.xgroup_create.assert_called()


class TestClaimStaleTasks:
    """Tests for claim_stale_tasks function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_claims_stale_tasks(self, mock_redis):
        """Test claiming stale tasks."""
        now = datetime.now(UTC)
        mock_redis.xautoclaim = AsyncMock(
            return_value=[
                "0-0",  # next start id
                [
                    [
                        "1234567890-0",
                        {
                            "task_id": "task-123",
                            "job_id": "job-456",
                            "enqueued_at": now.isoformat(),
                            "timeout_at": (now + timedelta(seconds=600)).isoformat(),
                        },
                    ]
                ],
                [],  # deleted ids
            ]
        )
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234567890-0",
                    "consumer": "engine-1",
                    "time_since_delivered": 700000,
                    "times_delivered": 2,
                }
            ]
        )

        messages = await claim_stale_tasks(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            min_idle_ms=600000,
            count=1,
        )

        assert len(messages) == 1
        assert messages[0].task_id == "task-123"
        assert messages[0].delivery_count == 2

    @pytest.mark.asyncio
    async def test_returns_empty_on_nogroup(self, mock_redis):
        """Test returning empty list when group doesn't exist."""
        mock_redis.xautoclaim = AsyncMock(
            side_effect=ResponseError("NOGROUP No such consumer group")
        )

        messages = await claim_stale_tasks(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            min_idle_ms=600000,
        )

        assert messages == []


class TestClaimTasksById:
    """Tests for claim_tasks_by_id function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_claims_specific_tasks(self, mock_redis):
        """Test claiming specific tasks by ID."""
        now = datetime.now(UTC)
        mock_redis.xclaim = AsyncMock(
            return_value=[
                [
                    "1234567890-0",
                    {
                        "task_id": "task-123",
                        "job_id": "job-456",
                        "enqueued_at": now.isoformat(),
                        "timeout_at": (now + timedelta(seconds=600)).isoformat(),
                    },
                ]
            ]
        )
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234567890-0",
                    "consumer": "engine-2",
                    "time_since_delivered": 0,
                    "times_delivered": 3,
                }
            ]
        )

        messages = await claim_tasks_by_id(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            message_ids=["1234567890-0"],
        )

        assert len(messages) == 1
        assert messages[0].task_id == "task-123"

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_list(self, mock_redis):
        """Test returning empty list when no IDs provided."""
        messages = await claim_tasks_by_id(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            message_ids=[],
        )

        assert messages == []


class TestAckTask:
    """Tests for ack_task function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_acknowledges_task(self, mock_redis):
        """Test acknowledging a task."""
        mock_redis.xack = AsyncMock()

        await ack_task(mock_redis, "transcribe", "1234567890-0")

        mock_redis.xack.assert_called_once_with(
            "dalston:stream:transcribe",
            CONSUMER_GROUP,
            "1234567890-0",
        )


class TestGetPending:
    """Tests for get_pending function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_gets_pending_tasks(self, mock_redis):
        """Test getting all pending tasks."""
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234567890-0",
                    "consumer": "engine-1",
                    "time_since_delivered": 60000,
                    "times_delivered": 1,
                },
                {
                    "message_id": "1234567891-0",
                    "consumer": "engine-2",
                    "time_since_delivered": 120000,
                    "times_delivered": 2,
                },
            ]
        )
        mock_redis.xrange = AsyncMock(
            side_effect=[
                [["1234567890-0", {"task_id": "task-123"}]],
                [["1234567891-0", {"task_id": "task-456"}]],
            ]
        )

        pending = await get_pending(mock_redis, "transcribe")

        assert len(pending) == 2
        assert pending[0].task_id == "task-123"
        assert pending[0].consumer == "engine-1"
        assert pending[0].idle_ms == 60000
        assert pending[0].delivery_count == 1
        assert pending[1].task_id == "task-456"
        assert pending[1].consumer == "engine-2"

    @pytest.mark.asyncio
    async def test_returns_empty_on_nogroup(self, mock_redis):
        """Test returning empty list when group doesn't exist."""
        mock_redis.xpending_range = AsyncMock(
            side_effect=ResponseError("NOGROUP No such consumer group")
        )

        pending = await get_pending(mock_redis, "transcribe")

        assert pending == []


class TestDiscoverStreams:
    """Tests for discover_streams function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_discovers_all_streams(self, mock_redis):
        """Test discovering all task streams."""
        mock_redis.scan = AsyncMock(
            side_effect=[
                (
                    100,
                    ["dalston:stream:transcribe", "dalston:stream:align"],
                ),
                (0, ["dalston:stream:diarize"]),
            ]
        )

        streams = await discover_streams(mock_redis)

        assert len(streams) == 3
        assert "dalston:stream:transcribe" in streams
        assert "dalston:stream:align" in streams
        assert "dalston:stream:diarize" in streams

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_streams(self, mock_redis):
        """Test returning empty list when no streams exist."""
        mock_redis.scan = AsyncMock(return_value=(0, []))

        streams = await discover_streams(mock_redis)

        assert streams == []


class TestGetStreamInfo:
    """Tests for get_stream_info function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_gets_stream_info(self, mock_redis):
        """Test getting stream statistics."""
        mock_redis.xlen = AsyncMock(return_value=10)
        mock_redis.xpending = AsyncMock(
            return_value={
                "pending": 3,
                "consumers": [
                    {"name": "engine-1", "pending": 2},
                    {"name": "engine-2", "pending": 1},
                ],
            }
        )

        info = await get_stream_info(mock_redis, "transcribe")

        assert info["stream_key"] == "dalston:stream:transcribe"
        assert info["stream_length"] == 10
        assert info["pending_count"] == 3
        assert len(info["consumers"]) == 2

    @pytest.mark.asyncio
    async def test_handles_nogroup_error(self, mock_redis):
        """Test handling when group doesn't exist."""
        mock_redis.xlen = AsyncMock(return_value=0)
        mock_redis.xpending = AsyncMock(
            side_effect=ResponseError("NOGROUP No such consumer group")
        )

        info = await get_stream_info(mock_redis, "transcribe")

        assert info["stream_length"] == 0
        assert info["pending_count"] == 0
        assert info["consumers"] == []


class TestDataclasses:
    """Tests for dataclasses."""

    def test_stream_message(self):
        """Test StreamMessage dataclass."""
        now = datetime.now(UTC)
        msg = StreamMessage(
            id="1234567890-0",
            task_id="task-123",
            job_id="job-456",
            enqueued_at=now,
            timeout_at=now + timedelta(seconds=600),
            delivery_count=1,
        )

        assert msg.id == "1234567890-0"
        assert msg.task_id == "task-123"
        assert msg.delivery_count == 1

    def test_pending_task(self):
        """Test PendingTask dataclass."""
        task = PendingTask(
            message_id="1234567890-0",
            task_id="task-123",
            consumer="engine-1",
            idle_ms=60000,
            delivery_count=2,
        )

        assert task.message_id == "1234567890-0"
        assert task.consumer == "engine-1"
        assert task.idle_ms == 60000
        assert task.delivery_count == 2
