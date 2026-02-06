"""Integration tests for task observability API endpoints.

Tests the task-level observability endpoints:
- GET /v1/audio/transcriptions/{job_id} (with stages)
- GET /v1/audio/transcriptions/{job_id}/tasks
- GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.common.models import JobStatus
from dalston.gateway.api.v1 import tasks as tasks_module
from dalston.gateway.api.v1 import transcription as transcription_module
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService


def _create_mock_task(
    task_id: UUID,
    job_id: UUID,
    stage: str,
    engine_id: str,
    status: str = "completed",
    dependencies: list[UUID] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    required: bool = True,
    retries: int = 0,
    error: str | None = None,
):
    """Create a mock TaskModel object."""
    task = MagicMock()
    task.id = task_id
    task.job_id = job_id
    task.stage = stage
    task.engine_id = engine_id
    task.status = status
    task.dependencies = dependencies or []
    task.started_at = started_at
    task.completed_at = completed_at
    task.required = required
    task.retries = retries
    task.error = error
    task.input_uri = f"s3://bucket/jobs/{job_id}/tasks/{task_id}/input.json"
    task.output_uri = f"s3://bucket/jobs/{job_id}/tasks/{task_id}/output.json"
    return task


def _create_mock_job(
    job_id: UUID,
    tenant_id: UUID,
    status: str = "completed",
    tasks: list | None = None,
):
    """Create a mock JobModel object."""
    job = MagicMock()
    job.id = job_id
    job.tenant_id = tenant_id
    job.status = status
    job.created_at = datetime.now(UTC)
    job.started_at = datetime.now(UTC)
    job.completed_at = datetime.now(UTC) + timedelta(seconds=10)
    job.error = None
    job.tasks = tasks or []
    return job


class TestJobStatusWithStages:
    """Tests for GET /v1/audio/transcriptions/{job_id} with stages array."""

    @pytest.fixture
    def mock_jobs_service(self):
        service = AsyncMock(spec=JobsService)
        # Add the _topological_sort_tasks method from real service
        real_service = JobsService()
        service._topological_sort_tasks = real_service._topological_sort_tasks
        return service

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        settings.s3_endpoint_url = "http://localhost:9000"
        settings.s3_access_key_id = "test"
        settings.s3_secret_access_key = "test"
        return settings

    @pytest.fixture
    def mock_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_db, mock_settings, mock_api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(transcription_module.router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_job_status_includes_stages_array(
        self, client, mock_jobs_service, mock_api_key, monkeypatch
    ):
        """Test that job status includes stages array when job has tasks."""
        job_id = uuid4()
        tenant_id = mock_api_key.tenant_id
        prepare_id = uuid4()
        transcribe_id = uuid4()

        now = datetime.now(UTC)
        prepare_task = _create_mock_task(
            task_id=prepare_id,
            job_id=job_id,
            stage="prepare",
            engine_id="audio-prepare",
            status="completed",
            dependencies=[],
            started_at=now,
            completed_at=now + timedelta(seconds=1),
        )
        transcribe_task = _create_mock_task(
            task_id=transcribe_id,
            job_id=job_id,
            stage="transcribe",
            engine_id="faster-whisper",
            status="completed",
            dependencies=[prepare_id],
            started_at=now + timedelta(seconds=1),
            completed_at=now + timedelta(seconds=9),
        )

        job = _create_mock_job(
            job_id=job_id,
            tenant_id=tenant_id,
            status="completed",
            tasks=[prepare_task, transcribe_task],
        )

        mock_jobs_service.get_job_with_tasks.return_value = job

        # Mock storage to return transcript
        async def mock_get_transcript(self, job_id):
            return {"text": "Hello", "segments": []}

        monkeypatch.setattr(StorageService, "get_transcript", mock_get_transcript)

        response = client.get(f"/v1/audio/transcriptions/{job_id}")

        assert response.status_code == 200
        data = response.json()

        assert "stages" in data
        assert len(data["stages"]) == 2

        # Verify order (topological sort)
        assert data["stages"][0]["stage"] == "prepare"
        assert data["stages"][1]["stage"] == "transcribe"

        # Verify stage fields
        prepare_stage = data["stages"][0]
        assert prepare_stage["engine_id"] == "audio-prepare"
        assert prepare_stage["status"] == "completed"
        assert prepare_stage["required"] is True
        assert prepare_stage["duration_ms"] == 1000

    def test_job_status_stages_null_when_no_tasks(self, client, mock_jobs_service):
        """Test that stages is null when job has no tasks (pending job)."""
        job_id = uuid4()
        job = _create_mock_job(
            job_id=job_id,
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            status="pending",
            tasks=[],
        )

        mock_jobs_service.get_job_with_tasks.return_value = job

        response = client.get(f"/v1/audio/transcriptions/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["stages"] is None

    def test_job_status_failed_stage_shows_error(
        self, client, mock_jobs_service, monkeypatch
    ):
        """Test that failed stages include error message."""
        job_id = uuid4()
        now = datetime.now(UTC)

        failed_task = _create_mock_task(
            task_id=uuid4(),
            job_id=job_id,
            stage="diarize",
            engine_id="pyannote-3.1",
            status="failed",
            required=False,
            started_at=now,
            completed_at=now + timedelta(seconds=3),
            retries=2,
            error="Too many speakers detected (>20)",
        )

        job = _create_mock_job(
            job_id=job_id,
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            status="completed",
            tasks=[failed_task],
        )

        mock_jobs_service.get_job_with_tasks.return_value = job

        # Mock storage to return transcript
        async def mock_get_transcript(self, job_id):
            return {"text": "Hello", "segments": []}

        monkeypatch.setattr(StorageService, "get_transcript", mock_get_transcript)

        response = client.get(f"/v1/audio/transcriptions/{job_id}")

        assert response.status_code == 200
        data = response.json()

        assert len(data["stages"]) == 1
        stage = data["stages"][0]
        assert stage["status"] == "failed"
        assert stage["error"] == "Too many speakers detected (>20)"
        assert stage["retries"] == 2
        assert stage["required"] is False


class TestTaskListEndpoint:
    """Tests for GET /v1/audio/transcriptions/{job_id}/tasks endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        service = AsyncMock(spec=JobsService)
        real_service = JobsService()
        service._topological_sort_tasks = real_service._topological_sort_tasks
        return service

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_db, mock_api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(tasks_module.router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_list_tasks_returns_all_tasks(self, client, mock_jobs_service):
        """Test that task list returns all tasks with dependencies."""
        job_id = uuid4()
        prepare_id = uuid4()
        transcribe_id = uuid4()
        merge_id = uuid4()

        prepare = _create_mock_task(
            task_id=prepare_id,
            job_id=job_id,
            stage="prepare",
            engine_id="audio-prepare",
            dependencies=[],
        )
        transcribe = _create_mock_task(
            task_id=transcribe_id,
            job_id=job_id,
            stage="transcribe",
            engine_id="faster-whisper",
            dependencies=[prepare_id],
        )
        merge = _create_mock_task(
            task_id=merge_id,
            job_id=job_id,
            stage="merge",
            engine_id="final-merger",
            dependencies=[transcribe_id],
        )

        mock_job = _create_mock_job(job_id=job_id, tenant_id=uuid4())
        mock_jobs_service.get_job.return_value = mock_job
        mock_jobs_service.get_job_tasks.return_value = [prepare, transcribe, merge]

        response = client.get(f"/v1/audio/transcriptions/{job_id}/tasks")

        assert response.status_code == 200
        data = response.json()

        assert data["job_id"] == str(job_id)
        assert len(data["tasks"]) == 3

        # Verify dependencies are included
        transcribe_data = next(t for t in data["tasks"] if t["stage"] == "transcribe")
        assert str(prepare_id) in transcribe_data["dependencies"]

    def test_list_tasks_job_not_found(self, client, mock_jobs_service):
        """Test 404 response when job not found."""
        job_id = uuid4()
        mock_jobs_service.get_job.return_value = None

        response = client.get(f"/v1/audio/transcriptions/{job_id}/tasks")

        assert response.status_code == 404

    def test_list_tasks_per_channel_stages(self, client, mock_jobs_service):
        """Test that per-channel tasks are returned with channel suffix."""
        job_id = uuid4()
        prepare_id = uuid4()

        prepare = _create_mock_task(
            task_id=prepare_id,
            job_id=job_id,
            stage="prepare",
            engine_id="audio-prepare",
            dependencies=[],
        )
        trans_ch0 = _create_mock_task(
            task_id=uuid4(),
            job_id=job_id,
            stage="transcribe_ch0",
            engine_id="faster-whisper",
            dependencies=[prepare_id],
        )
        trans_ch1 = _create_mock_task(
            task_id=uuid4(),
            job_id=job_id,
            stage="transcribe_ch1",
            engine_id="faster-whisper",
            dependencies=[prepare_id],
        )

        mock_job = _create_mock_job(job_id=job_id, tenant_id=uuid4())
        mock_jobs_service.get_job.return_value = mock_job
        mock_jobs_service.get_job_tasks.return_value = [prepare, trans_ch0, trans_ch1]

        response = client.get(f"/v1/audio/transcriptions/{job_id}/tasks")

        assert response.status_code == 200
        data = response.json()

        stages = [t["stage"] for t in data["tasks"]]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages


class TestTaskArtifactsEndpoint:
    """Tests for GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        settings.s3_endpoint_url = "http://localhost:9000"
        settings.s3_access_key_id = "test"
        settings.s3_secret_access_key = "test"
        return settings

    @pytest.fixture
    def mock_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_db, mock_settings, mock_api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(tasks_module.router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_get_artifacts_completed_task(
        self, client, mock_jobs_service, mock_settings, monkeypatch
    ):
        """Test getting artifacts for a completed task."""
        job_id = uuid4()
        task_id = uuid4()

        task = _create_mock_task(
            task_id=task_id,
            job_id=job_id,
            stage="transcribe",
            engine_id="faster-whisper",
            status="completed",
        )

        mock_jobs_service.get_task.return_value = task

        # Mock storage service methods
        mock_input = {
            "audio_uri": "s3://bucket/audio.wav",
            "config": {"model": "large-v3"},
        }
        mock_output = {
            "text": "Hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
        }

        async def mock_get_task_input(self, job_id, task_id):
            return mock_input

        async def mock_get_task_output(self, job_id, task_id):
            return mock_output

        monkeypatch.setattr(
            StorageService, "get_task_input", mock_get_task_input
        )
        monkeypatch.setattr(
            StorageService, "get_task_output", mock_get_task_output
        )

        response = client.get(
            f"/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts"
        )

        assert response.status_code == 200
        data = response.json()

        assert data["task_id"] == str(task_id)
        assert data["job_id"] == str(job_id)
        assert data["stage"] == "transcribe"
        assert data["status"] == "completed"
        assert data["input"]["config"]["model"] == "large-v3"
        assert data["output"]["text"] == "Hello world"

    def test_get_artifacts_failed_task_no_output(
        self, client, mock_jobs_service, monkeypatch
    ):
        """Test getting artifacts for a failed task (input present, output null)."""
        job_id = uuid4()
        task_id = uuid4()

        task = _create_mock_task(
            task_id=task_id,
            job_id=job_id,
            stage="diarize",
            engine_id="pyannote-3.1",
            status="failed",
            error="Too many speakers",
        )

        mock_jobs_service.get_task.return_value = task

        mock_input = {"config": {"num_speakers": None}}

        async def mock_get_task_input(self, job_id, task_id):
            return mock_input

        async def mock_get_task_output(self, job_id, task_id):
            return None  # No output for failed task

        monkeypatch.setattr(StorageService, "get_task_input", mock_get_task_input)
        monkeypatch.setattr(StorageService, "get_task_output", mock_get_task_output)

        response = client.get(
            f"/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts"
        )

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "failed"
        assert data["input"] is not None
        assert data["output"] is None

    def test_get_artifacts_pending_task_returns_400(self, client, mock_jobs_service):
        """Test 400 response for pending task (no artifacts yet)."""
        job_id = uuid4()
        task_id = uuid4()

        task = _create_mock_task(
            task_id=task_id,
            job_id=job_id,
            stage="transcribe",
            engine_id="faster-whisper",
            status="pending",  # Not started
        )

        mock_jobs_service.get_task.return_value = task

        response = client.get(
            f"/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts"
        )

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "no_artifacts"

    def test_get_artifacts_job_not_found(self, client, mock_jobs_service):
        """Test 404 response when job not found."""
        job_id = uuid4()
        task_id = uuid4()

        mock_jobs_service.get_task.return_value = None
        mock_jobs_service.get_job.return_value = None

        response = client.get(
            f"/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts"
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "job_not_found"

    def test_get_artifacts_task_not_found(self, client, mock_jobs_service):
        """Test 404 response when task not found."""
        job_id = uuid4()
        task_id = uuid4()

        mock_jobs_service.get_task.return_value = None
        # Job exists but task doesn't
        mock_job = _create_mock_job(job_id=job_id, tenant_id=uuid4())
        mock_jobs_service.get_job.return_value = mock_job

        response = client.get(
            f"/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts"
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "task_not_found"


class TestTaskObservabilityAuthorization:
    """Tests for authorization on task observability endpoints."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    def test_task_list_requires_jobs_read_scope(self, mock_jobs_service, mock_db):
        """Test that task list requires jobs:read scope."""
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        # API key without jobs:read scope
        api_key_no_read = APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="No Read Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.REALTIME],  # No jobs:read
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        app = FastAPI()
        app.include_router(tasks_module.router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: api_key_no_read

        client = TestClient(app)
        response = client.get(f"/v1/audio/transcriptions/{uuid4()}/tasks")

        assert response.status_code == 403

    def test_artifacts_requires_jobs_read_scope(self, mock_jobs_service, mock_db):
        """Test that artifacts endpoint requires jobs:read scope."""
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        api_key_no_read = APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="No Read Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.REALTIME],  # No jobs:read
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        app = FastAPI()
        app.include_router(tasks_module.router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: api_key_no_read

        client = TestClient(app)
        response = client.get(
            f"/v1/audio/transcriptions/{uuid4()}/tasks/{uuid4()}/artifacts"
        )

        assert response.status_code == 403
