"""Unit tests for task-level observability feature."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from dalston.gateway.models.responses import (
    StageResponse,
    TaskArtifactResponse,
    TaskListResponse,
    TaskResponse,
)
from dalston.gateway.services.jobs import JobsService


class TestStageResponse:
    """Tests for StageResponse Pydantic model."""

    def test_stage_response_with_all_fields(self):
        """Test StageResponse with all fields populated."""
        now = datetime.now(UTC)
        completed = now + timedelta(seconds=5)

        stage = StageResponse(
            stage="transcribe",
            task_id=UUID("12345678-1234-1234-1234-123456789abc"),
            engine_id="faster-whisper",
            status="completed",
            required=True,
            started_at=now,
            completed_at=completed,
            duration_ms=5000,
            retries=2,
            error=None,
        )

        assert stage.stage == "transcribe"
        assert stage.engine_id == "faster-whisper"
        assert stage.status == "completed"
        assert stage.required is True
        assert stage.duration_ms == 5000
        assert stage.retries == 2
        assert stage.error is None

    def test_stage_response_pending_task(self):
        """Test StageResponse for a pending task."""
        stage = StageResponse(
            stage="diarize",
            task_id=UUID("12345678-1234-1234-1234-123456789abc"),
            engine_id="pyannote-4.0",
            status="pending",
            required=False,
        )

        assert stage.status == "pending"
        assert stage.started_at is None
        assert stage.completed_at is None
        assert stage.duration_ms is None
        assert stage.retries is None

    def test_stage_response_failed_task(self):
        """Test StageResponse for a failed task with error."""
        stage = StageResponse(
            stage="diarize",
            task_id=UUID("12345678-1234-1234-1234-123456789abc"),
            engine_id="pyannote-4.0",
            status="failed",
            required=False,
            error="Too many speakers detected (>20)",
        )

        assert stage.status == "failed"
        assert stage.error == "Too many speakers detected (>20)"


class TestTaskResponse:
    """Tests for TaskResponse Pydantic model."""

    def test_task_response_with_dependencies(self):
        """Test TaskResponse with dependency list."""
        dep_id = UUID("11111111-1111-1111-1111-111111111111")

        task = TaskResponse(
            task_id=UUID("22222222-2222-2222-2222-222222222222"),
            stage="transcribe",
            engine_id="faster-whisper",
            status="completed",
            required=True,
            dependencies=[dep_id],
            duration_ms=8400,
            retries=0,
        )

        assert len(task.dependencies) == 1
        assert task.dependencies[0] == dep_id
        assert task.duration_ms == 8400

    def test_task_response_no_dependencies(self):
        """Test TaskResponse for a task with no dependencies (root task)."""
        task = TaskResponse(
            task_id=UUID("22222222-2222-2222-2222-222222222222"),
            stage="prepare",
            engine_id="audio-prepare",
            status="completed",
            required=True,
            dependencies=[],
        )

        assert task.dependencies == []


class TestTaskListResponse:
    """Tests for TaskListResponse Pydantic model."""

    def test_task_list_response(self):
        """Test TaskListResponse with multiple tasks."""
        job_id = UUID("33333333-3333-3333-3333-333333333333")

        response = TaskListResponse(
            job_id=job_id,
            tasks=[
                TaskResponse(
                    task_id=UUID("11111111-1111-1111-1111-111111111111"),
                    stage="prepare",
                    engine_id="audio-prepare",
                    status="completed",
                    required=True,
                    dependencies=[],
                ),
                TaskResponse(
                    task_id=UUID("22222222-2222-2222-2222-222222222222"),
                    stage="transcribe",
                    engine_id="faster-whisper",
                    status="running",
                    required=True,
                    dependencies=[UUID("11111111-1111-1111-1111-111111111111")],
                ),
            ],
        )

        assert response.job_id == job_id
        assert len(response.tasks) == 2
        assert response.tasks[0].stage == "prepare"
        assert response.tasks[1].stage == "transcribe"


class TestTaskArtifactResponse:
    """Tests for TaskArtifactResponse Pydantic model."""

    def test_artifact_response_completed_task(self):
        """Test TaskArtifactResponse for a completed task."""
        response = TaskArtifactResponse(
            task_id=UUID("11111111-1111-1111-1111-111111111111"),
            job_id=UUID("22222222-2222-2222-2222-222222222222"),
            stage="transcribe",
            engine_id="faster-whisper",
            status="completed",
            input={
                "audio_uri": "s3://bucket/audio.wav",
                "config": {"model": "large-v3"},
            },
            output={
                "text": "Hello world",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
            },
        )

        assert response.input is not None
        assert response.output is not None
        assert response.input["config"]["model"] == "large-v3"
        assert response.output["text"] == "Hello world"

    def test_artifact_response_failed_task(self):
        """Test TaskArtifactResponse for a failed task (no output)."""
        response = TaskArtifactResponse(
            task_id=UUID("11111111-1111-1111-1111-111111111111"),
            job_id=UUID("22222222-2222-2222-2222-222222222222"),
            stage="diarize",
            engine_id="pyannote-4.0",
            status="failed",
            input={"config": {"num_speakers": None}},
            output=None,
        )

        assert response.status == "failed"
        assert response.input is not None
        assert response.output is None


class TestTopologicalSort:
    """Tests for JobsService._topological_sort_tasks method."""

    @pytest.fixture
    def jobs_service(self) -> JobsService:
        return JobsService()

    def _make_mock_task(
        self,
        task_id: UUID,
        stage: str,
        dependencies: list[UUID],
        status: str = "completed",
    ):
        """Create a mock task object."""
        task = MagicMock()
        task.id = task_id
        task.stage = stage
        task.dependencies = dependencies
        task.status = status
        task.engine_id = f"engine-{stage}"
        task.required = True
        task.started_at = None
        task.completed_at = None
        task.retries = 0
        task.error = None
        return task

    def test_empty_tasks(self, jobs_service: JobsService):
        """Test topological sort with empty task list."""
        result = jobs_service._topological_sort_tasks([])
        assert result == []

    def test_single_task(self, jobs_service: JobsService):
        """Test topological sort with single task."""
        task = self._make_mock_task(uuid4(), "prepare", [])
        result = jobs_service._topological_sort_tasks([task])
        assert len(result) == 1
        assert result[0].stage == "prepare"

    def test_linear_pipeline(self, jobs_service: JobsService):
        """Test topological sort with linear dependencies: prepare -> transcribe -> merge."""
        prepare_id = uuid4()
        transcribe_id = uuid4()
        merge_id = uuid4()

        prepare = self._make_mock_task(prepare_id, "prepare", [])
        transcribe = self._make_mock_task(transcribe_id, "transcribe", [prepare_id])
        merge = self._make_mock_task(merge_id, "merge", [transcribe_id])

        # Pass in random order
        tasks = [merge, prepare, transcribe]
        result = jobs_service._topological_sort_tasks(tasks)

        assert len(result) == 3
        assert result[0].stage == "prepare"
        assert result[1].stage == "transcribe"
        assert result[2].stage == "merge"

    def test_parallel_tasks_alphabetical(self, jobs_service: JobsService):
        """Test that parallel tasks are sorted alphabetically."""
        prepare_id = uuid4()

        prepare = self._make_mock_task(prepare_id, "prepare", [])
        # Two parallel tasks that both depend on prepare
        diarize = self._make_mock_task(uuid4(), "diarize", [prepare_id])
        align = self._make_mock_task(uuid4(), "align", [prepare_id])

        tasks = [diarize, prepare, align]
        result = jobs_service._topological_sort_tasks(tasks)

        assert len(result) == 3
        assert result[0].stage == "prepare"
        # align comes before diarize alphabetically
        assert result[1].stage == "align"
        assert result[2].stage == "diarize"

    def test_per_channel_pipeline(self, jobs_service: JobsService):
        """Test topological sort with per-channel parallel tasks."""
        prepare_id = uuid4()
        trans_ch0_id = uuid4()
        trans_ch1_id = uuid4()
        merge_id = uuid4()

        prepare = self._make_mock_task(prepare_id, "prepare", [])
        trans_ch0 = self._make_mock_task(trans_ch0_id, "transcribe_ch0", [prepare_id])
        trans_ch1 = self._make_mock_task(trans_ch1_id, "transcribe_ch1", [prepare_id])
        merge = self._make_mock_task(merge_id, "merge", [trans_ch0_id, trans_ch1_id])

        tasks = [merge, trans_ch1, prepare, trans_ch0]
        result = jobs_service._topological_sort_tasks(tasks)

        assert len(result) == 4
        assert result[0].stage == "prepare"
        # ch0 before ch1 alphabetically
        assert result[1].stage == "transcribe_ch0"
        assert result[2].stage == "transcribe_ch1"
        assert result[3].stage == "merge"

    def test_diamond_dag(self, jobs_service: JobsService):
        r"""Test topological sort with diamond-shaped DAG.

        prepare -> transcribe -> align -> merge
                            \-> diarize -/
        """
        prepare_id = uuid4()
        transcribe_id = uuid4()
        align_id = uuid4()
        diarize_id = uuid4()
        merge_id = uuid4()

        prepare = self._make_mock_task(prepare_id, "prepare", [])
        transcribe = self._make_mock_task(transcribe_id, "transcribe", [prepare_id])
        align = self._make_mock_task(align_id, "align", [transcribe_id])
        diarize = self._make_mock_task(diarize_id, "diarize", [transcribe_id])
        merge = self._make_mock_task(merge_id, "merge", [align_id, diarize_id])

        tasks = [merge, diarize, transcribe, align, prepare]
        result = jobs_service._topological_sort_tasks(tasks)

        assert len(result) == 5
        assert result[0].stage == "prepare"
        assert result[1].stage == "transcribe"
        # align comes before diarize alphabetically
        assert result[2].stage == "align"
        assert result[3].stage == "diarize"
        assert result[4].stage == "merge"


class TestJobsServiceGetTask:
    """Tests for JobsService.get_task method."""

    @pytest.fixture
    def jobs_service(self) -> JobsService:
        return JobsService()

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_task_returns_none_when_job_not_found(
        self, jobs_service: JobsService, mock_db
    ):
        """Test get_task returns None when job doesn't exist."""
        # Mock get_job to return None
        jobs_service.get_job = AsyncMock(return_value=None)

        result = await jobs_service.get_task(
            mock_db,
            job_id=uuid4(),
            task_id=uuid4(),
            tenant_id=uuid4(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_task_returns_none_when_task_not_found(
        self, jobs_service: JobsService, mock_db
    ):
        """Test get_task returns None when task doesn't exist for job."""
        # Mock get_job to return a job
        mock_job = MagicMock()
        jobs_service.get_job = AsyncMock(return_value=mock_job)

        # Mock db.execute to return no task
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await jobs_service.get_task(
            mock_db,
            job_id=uuid4(),
            task_id=uuid4(),
            tenant_id=uuid4(),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_task_returns_task_when_found(
        self, jobs_service: JobsService, mock_db
    ):
        """Test get_task returns the task when found."""
        # Mock get_job to return a job
        mock_job = MagicMock()
        jobs_service.get_job = AsyncMock(return_value=mock_job)

        # Mock db.execute to return a task
        mock_task = MagicMock()
        mock_task.id = uuid4()
        mock_task.stage = "transcribe"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_db.execute.return_value = mock_result

        result = await jobs_service.get_task(
            mock_db,
            job_id=uuid4(),
            task_id=mock_task.id,
            tenant_id=uuid4(),
        )

        assert result is not None
        assert result.stage == "transcribe"
