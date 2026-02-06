"""Unit tests for the task RUNNING state transition.

Verifies that:
- handle_task_started sets task status to RUNNING and records started_at
- EngineRunner publishes a task.started event when picking up a task
"""

import json
from datetime import datetime
from unittest.mock import MagicMock
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
        db = MagicMock()

        async def mock_get(model_cls, pk):
            if pk == mock_task.id:
                return mock_task
            return None

        async def mock_commit():
            pass

        db.get = mock_get
        db.commit = mock_commit
        return db

    @pytest.mark.asyncio
    async def test_sets_status_to_running(self, task_id, mock_task, mock_db):
        from dalston.orchestrator.handlers import handle_task_started

        await handle_task_started(task_id, mock_db)

        assert mock_task.status == TaskStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_sets_started_at(self, task_id, mock_task, mock_db):
        from dalston.orchestrator.handlers import handle_task_started

        await handle_task_started(task_id, mock_db)

        assert mock_task.started_at is not None
        assert isinstance(mock_task.started_at, datetime)

    @pytest.mark.asyncio
    async def test_handles_missing_task(self, mock_db):
        """handle_task_started does not raise for a nonexistent task."""
        from dalston.orchestrator.handlers import handle_task_started

        unknown_id = uuid4()
        await handle_task_started(unknown_id, mock_db)  # should not raise


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
