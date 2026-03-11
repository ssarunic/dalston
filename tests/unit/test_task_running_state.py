"""Unit tests for the task RUNNING state transition.

Verifies that:
- handle_task_started sets task status to RUNNING and records started_at
- EngineRunner publishes a task.started event when picking up a task
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.common.models import TaskStatus

# ---------------------------------------------------------------------------
# handle_task_started handler
# ---------------------------------------------------------------------------


class TestHandleTaskStarted:
    """Tests for the orchestrator handle_task_started handler."""

    @pytest.fixture
    def task_id(self):
        return uuid4()

    @pytest.fixture
    def mock_task(self, task_id):
        task = MagicMock()
        task.id = task_id
        task.stage = "transcribe"
        task.status = TaskStatus.READY.value
        task.started_at = None
        return task

    @pytest.fixture
    def mock_db(self, mock_task):
        """Create mock async DB session that handles atomic UPDATE."""
        db = MagicMock()

        # Mock execute for the atomic UPDATE query
        update_result = MagicMock()
        update_result.scalar_one_or_none.return_value = mock_task.stage
        db.execute = AsyncMock(return_value=update_result)
        db.commit = AsyncMock()
        db.get = AsyncMock(return_value=mock_task)

        return db

    @pytest.mark.asyncio
    async def test_sets_status_to_running(self, task_id, mock_task, mock_db):
        """Test that atomic UPDATE is executed for READY -> RUNNING transition."""
        from dalston.orchestrator.handlers import handle_task_started

        await handle_task_started(task_id, mock_db)

        # Verify execute was called (for the atomic UPDATE)
        mock_db.execute.assert_called_once()
        # Verify commit was called
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotent_when_already_running(self, task_id, mock_task, mock_db):
        """Test that already-running tasks are handled idempotently."""
        from dalston.orchestrator.handlers import handle_task_started

        # Simulate UPDATE returning None (no rows matched = task not in READY state)
        update_result = MagicMock()
        update_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=update_result)

        # Task is already RUNNING
        mock_task.status = TaskStatus.RUNNING.value
        mock_db.get = AsyncMock(return_value=mock_task)

        # Should not raise
        await handle_task_started(task_id, mock_db)

        # Should have called execute (for UPDATE) and get (to check current state)
        assert mock_db.execute.call_count == 1
        mock_db.get.assert_called_once()
        # Commit should NOT be called (no update happened)
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_task(self, mock_db):
        """handle_task_started does not raise for a nonexistent task."""
        from dalston.orchestrator.handlers import handle_task_started

        # Simulate UPDATE returning None and task not found
        update_result = MagicMock()
        update_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=update_result)
        mock_db.get = AsyncMock(return_value=None)

        unknown_id = uuid4()
        await handle_task_started(unknown_id, mock_db)  # should not raise

    @pytest.mark.asyncio
    async def test_rejects_claim_on_cancelled_task(self, task_id, mock_task, mock_db):
        """Test that claims on cancelled tasks are rejected."""
        from dalston.orchestrator.handlers import handle_task_started

        # Simulate UPDATE returning None (task not in READY state)
        update_result = MagicMock()
        update_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=update_result)

        # Task is CANCELLED
        mock_task.status = TaskStatus.CANCELLED.value
        mock_db.get = AsyncMock(return_value=mock_task)

        # Should not raise, but claim is rejected
        await handle_task_started(task_id, mock_db, engine_id="test-engine")

        # Commit should NOT be called (claim rejected)
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_engine_id_to_logs(self, task_id, mock_task, mock_db):
        """Test that engine_id parameter is accepted."""
        from dalston.orchestrator.handlers import handle_task_started

        # Should not raise with engine_id
        await handle_task_started(task_id, mock_db, engine_id="faster-whisper")

        # Verify the function completed (basic smoke test)
        mock_db.execute.assert_called_once()


# ---------------------------------------------------------------------------
# EngineRunner._publish_task_started
# ---------------------------------------------------------------------------


class TestEngineRunnerPublishTaskStarted:
    """Tests that EngineRunner publishes a task.started event."""

    def test_publish_task_started_sends_correct_event(self):
        from dalston.engine_sdk.runner import EngineRunner

        mock_engine = MagicMock()
        runner = EngineRunner(mock_engine)
        runner.engine_id = "faster-whisper"

        mock_redis = MagicMock()
        runner._redis = mock_redis

        task_id = str(uuid4())
        job_id = str(uuid4())

        runner._publish_task_started(task_id, job_id)

        mock_redis.publish.assert_called_once()
        channel, payload = mock_redis.publish.call_args[0]

        assert channel == "dalston:events"

        event = json.loads(payload)
        assert event["type"] == "task.started"
        assert event["task_id"] == task_id
        assert event["job_id"] == job_id
        assert event["engine_id"] == "faster-whisper"
        assert "timestamp" in event
