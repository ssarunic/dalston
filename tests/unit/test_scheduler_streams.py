"""Unit tests for scheduler Redis Streams integration (M33).

Tests that queue_task uses add_task from streams module.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from dalston.common.models import Task, TaskStatus
from dalston.common.streams_types import WAITING_ENGINE_TASKS_KEY
from dalston.orchestrator.scheduler import queue_task, remove_task_from_queue


class MockSettings:
    """Mock settings for testing."""

    s3_bucket = "test-bucket"
    s3_endpoint = "http://localhost:9000"
    s3_access_key = "test-key"
    s3_secret_key = "test-secret"
    engine_unavailable_behavior = "fail_fast"
    engine_wait_timeout_seconds = 300


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.sadd = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def mock_registry():
    """Create mock engine registry."""
    registry = MagicMock()
    registry.is_engine_available = AsyncMock(return_value=True)
    registry.get_engine = AsyncMock(return_value=None)
    return registry


@pytest.fixture
def mock_catalog():
    """Create mock engine catalog."""
    catalog = MagicMock()
    catalog.validate_language_support = MagicMock(return_value=None)
    catalog.get_engine = MagicMock(return_value=None)
    return catalog


@pytest.fixture
def sample_task():
    """Create a sample task."""
    return Task(
        id=uuid4(),
        job_id=uuid4(),
        stage="transcribe",
        engine_id="whisper-cpu",
        status=TaskStatus.READY,
        input_uri="s3://bucket/audio.wav",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config={"language": "en"},
    )


class TestQueueTaskWithStreams:
    """Tests for queue_task using Redis Streams."""

    @pytest.mark.asyncio
    async def test_uses_add_task_instead_of_lpush(
        self, mock_redis, mock_registry, mock_catalog, sample_task
    ):
        """Test that queue_task uses add_task from streams module."""
        with (
            patch(
                "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
            ) as mock_add_task,
            patch(
                "dalston.orchestrator.scheduler.write_task_input",
                new_callable=AsyncMock,
            ),
        ):
            mock_add_task.return_value = "1234567890-0"

            await queue_task(
                redis=mock_redis,
                task=sample_task,
                settings=MockSettings(),
                registry=mock_registry,
                catalog=mock_catalog,
            )

            # Verify add_task was called with correct arguments
            mock_add_task.assert_called_once()
            call_args = mock_add_task.call_args

            assert call_args[0][0] == mock_redis  # redis client
            assert call_args[1]["stage"] == sample_task.engine_id
            assert call_args[1]["task_id"] == str(sample_task.id)
            assert call_args[1]["job_id"] == str(sample_task.job_id)
            assert "timeout_s" in call_args[1]

    @pytest.mark.asyncio
    async def test_does_not_use_lpush(
        self, mock_redis, mock_registry, mock_catalog, sample_task
    ):
        """Test that queue_task no longer uses lpush."""
        with (
            patch(
                "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
            ) as mock_add_task,
            patch(
                "dalston.orchestrator.scheduler.write_task_input",
                new_callable=AsyncMock,
            ),
        ):
            mock_add_task.return_value = "1234567890-0"

            await queue_task(
                redis=mock_redis,
                task=sample_task,
                settings=MockSettings(),
                registry=mock_registry,
                catalog=mock_catalog,
            )

            # Verify lpush was NOT called
            mock_redis.lpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_channel_stage_queues_to_base_stream(
        self, mock_redis, mock_registry, mock_catalog
    ):
        """Per-channel task stages should still queue to engine-specific streams."""
        per_channel_task = Task(
            id=uuid4(),
            job_id=uuid4(),
            stage="transcribe_ch1",
            engine_id="whisper-cpu",
            status=TaskStatus.READY,
            input_uri="s3://bucket/audio.wav",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            config={"language": "en", "channel": 1},
        )

        with (
            patch(
                "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
            ) as mock_add_task,
            patch(
                "dalston.orchestrator.scheduler.write_task_input",
                new_callable=AsyncMock,
            ),
        ):
            mock_add_task.return_value = "1234567890-0"

            await queue_task(
                redis=mock_redis,
                task=per_channel_task,
                settings=MockSettings(),
                registry=mock_registry,
                catalog=mock_catalog,
            )

            mock_add_task.assert_called_once()
            call_args = mock_add_task.call_args
            assert call_args[1]["stage"] == per_channel_task.engine_id

    @pytest.mark.asyncio
    async def test_uses_add_task_once_when_idempotency_key_provided(
        self, mock_redis, mock_registry, mock_catalog, sample_task
    ):
        """Queue uses deduplicated enqueue when idempotency key is provided."""
        with (
            patch(
                "dalston.orchestrator.scheduler.add_task_once", new_callable=AsyncMock
            ) as mock_add_task_once,
            patch("dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock),
            patch(
                "dalston.orchestrator.scheduler.write_task_input",
                new_callable=AsyncMock,
            ),
        ):
            mock_add_task_once.return_value = "1234567890-0"

            await queue_task(
                redis=mock_redis,
                task=sample_task,
                settings=MockSettings(),
                registry=mock_registry,
                catalog=mock_catalog,
                enqueue_idempotency_key="dalston:test:retry:key",
            )

            mock_add_task_once.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_mode_publishes_engine_needed_and_tracks_waiting_task(
        self, mock_redis, mock_registry, mock_catalog, sample_task
    ):
        """Wait mode should publish scaler signal and track waiting task IDs."""
        mock_registry.is_engine_available = AsyncMock(return_value=False)

        settings = MockSettings()
        settings.engine_unavailable_behavior = "wait"
        settings.engine_wait_timeout_seconds = 120

        with (
            patch(
                "dalston.orchestrator.scheduler.add_task", new_callable=AsyncMock
            ) as mock_add_task,
            patch(
                "dalston.orchestrator.scheduler.publish_engine_needed",
                new_callable=AsyncMock,
            ) as mock_publish_engine_needed,
            patch(
                "dalston.orchestrator.scheduler.write_task_input",
                new_callable=AsyncMock,
            ),
        ):
            mock_add_task.return_value = "1234567890-0"

            await queue_task(
                redis=mock_redis,
                task=sample_task,
                settings=settings,
                registry=mock_registry,
                catalog=mock_catalog,
            )

            mock_publish_engine_needed.assert_called_once()
            mock_add_task.assert_called_once()
            assert mock_add_task.call_args[1]["stage"] == sample_task.engine_id
            mock_redis.sadd.assert_called_once_with(
                WAITING_ENGINE_TASKS_KEY, str(sample_task.id)
            )


class TestRemoveTaskFromQueue:
    """Tests for deprecated remove_task_from_queue."""

    @pytest.mark.asyncio
    async def test_returns_false_with_streams(self, mock_redis):
        """Test that remove_task_from_queue returns False (no-op with streams)."""
        task_id = uuid4()
        result = await remove_task_from_queue(mock_redis, task_id, "engine-1")

        # Always returns False with streams
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_call_lrem(self, mock_redis):
        """Test that remove_task_from_queue does not call lrem."""
        task_id = uuid4()
        await remove_task_from_queue(mock_redis, task_id, "engine-1")

        # lrem should not be called
        mock_redis.lrem.assert_not_called()
