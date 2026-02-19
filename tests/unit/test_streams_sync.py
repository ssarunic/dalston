"""Unit tests for synchronous Redis Streams helper module.

Tests the streams_sync.py module used by the engine SDK.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from redis.exceptions import ResponseError

from dalston.common.streams_sync import (
    CONSUMER_GROUP,
    JOB_CANCELLED_KEY_PREFIX,
    STALE_THRESHOLD_MS,
    PendingTask,
    StreamMessage,
    _parse_message,
    _stream_key,
    ack_task,
    claim_stale_from_dead_engines,
    claim_tasks_by_id,
    ensure_stream_group,
    get_pending,
    is_engine_alive,
    is_job_cancelled,
    read_task,
)


class TestStreamKey:
    """Tests for stream key building."""

    def test_stream_key(self):
        """Test building stream key from stage."""
        assert _stream_key("transcribe") == "dalston:stream:transcribe"
        assert _stream_key("align") == "dalston:stream:align"


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


class TestEnsureStreamGroup:
    """Tests for ensure_stream_group function."""

    def test_creates_group(self):
        """Test creating consumer group."""
        mock_redis = MagicMock()

        ensure_stream_group(mock_redis, "transcribe")

        mock_redis.xgroup_create.assert_called_once_with(
            "dalston:stream:transcribe",
            CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )

    def test_ignores_existing_group(self):
        """Test that BUSYGROUP error is ignored."""
        mock_redis = MagicMock()
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )

        # Should not raise
        ensure_stream_group(mock_redis, "transcribe")


class TestReadTask:
    """Tests for read_task function."""

    def test_reads_task_from_stream(self):
        """Test reading a task from the stream."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)

        mock_redis.xreadgroup.return_value = [
            [
                "dalston:stream:transcribe",
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
            ]
        ]

        msg = read_task(mock_redis, "transcribe", consumer="engine-1")

        assert msg is not None
        assert msg.task_id == "task-123"
        assert msg.delivery_count == 1

    def test_returns_none_on_timeout(self):
        """Test returning None when no task available."""
        mock_redis = MagicMock()
        mock_redis.xreadgroup.return_value = None

        msg = read_task(mock_redis, "transcribe", consumer="engine-1")

        assert msg is None


class TestClaimTasksById:
    """Tests for claim_tasks_by_id function."""

    def test_claims_specific_tasks(self):
        """Test claiming specific tasks by ID."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)

        mock_redis.xclaim.return_value = [
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
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "1234567890-0",
                "consumer": "engine-2",
                "time_since_delivered": 0,
                "times_delivered": 3,
            }
        ]

        messages = claim_tasks_by_id(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            message_ids=["1234567890-0"],
        )

        assert len(messages) == 1
        assert messages[0].task_id == "task-123"

    def test_returns_empty_for_empty_list(self):
        """Test returning empty list when no IDs provided."""
        mock_redis = MagicMock()

        messages = claim_tasks_by_id(
            mock_redis,
            "transcribe",
            consumer="engine-2",
            message_ids=[],
        )

        assert messages == []


class TestAckTask:
    """Tests for ack_task function."""

    def test_acknowledges_task(self):
        """Test acknowledging a task."""
        mock_redis = MagicMock()

        ack_task(mock_redis, "transcribe", "1234567890-0")

        mock_redis.xack.assert_called_once_with(
            "dalston:stream:transcribe",
            CONSUMER_GROUP,
            "1234567890-0",
        )


class TestGetPending:
    """Tests for get_pending function."""

    def test_gets_pending_tasks(self):
        """Test getting all pending tasks."""
        mock_redis = MagicMock()
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "1234567890-0",
                "consumer": "engine-1",
                "time_since_delivered": 60000,
                "times_delivered": 1,
            },
        ]
        mock_redis.xrange.return_value = [["1234567890-0", {"task_id": "task-123"}]]

        pending = get_pending(mock_redis, "transcribe")

        assert len(pending) == 1
        assert pending[0].task_id == "task-123"
        assert pending[0].consumer == "engine-1"


class TestIsEngineAlive:
    """Tests for is_engine_alive function."""

    def test_engine_alive_with_fresh_heartbeat(self):
        """Test engine is alive with recent heartbeat."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)
        mock_redis.hgetall.return_value = {
            "status": "idle",
            "last_heartbeat": now.isoformat(),
        }

        result = is_engine_alive(mock_redis, "engine-1")

        assert result is True

    def test_engine_dead_with_stale_heartbeat(self):
        """Test engine is dead with old heartbeat."""
        mock_redis = MagicMock()
        old = datetime.now(UTC) - timedelta(seconds=120)
        mock_redis.hgetall.return_value = {
            "status": "idle",
            "last_heartbeat": old.isoformat(),
        }

        result = is_engine_alive(mock_redis, "engine-1")

        assert result is False

    def test_engine_dead_when_not_found(self):
        """Test engine is dead when not in registry."""
        mock_redis = MagicMock()
        mock_redis.hgetall.return_value = {}

        result = is_engine_alive(mock_redis, "engine-1")

        assert result is False

    def test_engine_dead_when_offline_status(self):
        """Test engine is dead when status is offline."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)
        mock_redis.hgetall.return_value = {
            "status": "offline",
            "last_heartbeat": now.isoformat(),
        }

        result = is_engine_alive(mock_redis, "engine-1")

        assert result is False


class TestClaimStaleFromDeadEngines:
    """Tests for claim_stale_from_dead_engines function."""

    def test_claims_from_dead_engine(self):
        """Test claiming stale task from dead engine."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)

        # Setup pending task from dead engine
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "1234567890-0",
                "consumer": "dead-engine",
                "time_since_delivered": STALE_THRESHOLD_MS + 1000,
                "times_delivered": 1,
            }
        ]
        mock_redis.xrange.return_value = [["1234567890-0", {"task_id": "task-123"}]]
        # Dead engine - no heartbeat data
        mock_redis.hgetall.return_value = {}

        # Claim returns the message
        mock_redis.xclaim.return_value = [
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

        messages = claim_stale_from_dead_engines(
            mock_redis,
            "transcribe",
            consumer="engine-2",
        )

        assert len(messages) == 1
        assert messages[0].task_id == "task-123"

    def test_does_not_claim_from_alive_engine(self):
        """Test not claiming from engine that's still alive."""
        mock_redis = MagicMock()
        now = datetime.now(UTC)

        # Setup pending task from alive engine
        mock_redis.xpending_range.return_value = [
            {
                "message_id": "1234567890-0",
                "consumer": "alive-engine",
                "time_since_delivered": STALE_THRESHOLD_MS + 1000,
                "times_delivered": 1,
            }
        ]
        mock_redis.xrange.return_value = [["1234567890-0", {"task_id": "task-123"}]]
        # Alive engine - fresh heartbeat
        mock_redis.hgetall.return_value = {
            "status": "processing",
            "last_heartbeat": now.isoformat(),
        }

        messages = claim_stale_from_dead_engines(
            mock_redis,
            "transcribe",
            consumer="engine-2",
        )

        assert len(messages) == 0
        mock_redis.xclaim.assert_not_called()


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

    def test_pending_task(self):
        """Test PendingTask dataclass."""
        task = PendingTask(
            message_id="1234567890-0",
            task_id="task-123",
            consumer="engine-1",
            idle_ms=60000,
            delivery_count=2,
        )

        assert task.consumer == "engine-1"
        assert task.delivery_count == 2


class TestJobCancellation:
    """Tests for job cancellation functions."""

    def test_is_job_cancelled_when_cancelled(self):
        """Test checking a cancelled job."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1

        result = is_job_cancelled(mock_redis, "job-123")

        assert result is True
        mock_redis.exists.assert_called_once_with(f"{JOB_CANCELLED_KEY_PREFIX}job-123")

    def test_is_job_cancelled_when_not_cancelled(self):
        """Test checking a non-cancelled job."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        result = is_job_cancelled(mock_redis, "job-456")

        assert result is False
        mock_redis.exists.assert_called_once_with(f"{JOB_CANCELLED_KEY_PREFIX}job-456")
