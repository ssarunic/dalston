"""Integration tests for POST /v1/audio/transcriptions/{job_id}/retry endpoint."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.jobs import JobsService, RetryResult

JOB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")


def _make_api_key(scopes: list[Scope] | None = None) -> APIKey:
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123def456",
        prefix="dk_abc1234",
        name="Test Key",
        tenant_id=TENANT_ID,
        scopes=scopes or [Scope.JOBS_READ, Scope.JOBS_WRITE],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


def _make_job(status: str, retry_count: int = 0) -> MagicMock:
    job = MagicMock()
    job.id = JOB_ID
    job.tenant_id = TENANT_ID
    job.status = status
    job.retry_count = retry_count
    job.retention_policy = None
    return job


class TestRetryTranscription:
    """Tests for POST /v1/audio/transcriptions/{job_id}/retry."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock(spec=Settings)
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.fixture
    def mock_audit_service(self):
        return AsyncMock()

    @pytest.fixture
    def app(
        self,
        mock_jobs_service,
        mock_db,
        mock_redis,
        mock_settings,
        mock_audit_service,
    ):
        from dalston.gateway.dependencies import (
            get_audit_service,
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )
        from dalston.gateway.services.rate_limiter import RedisRateLimiter

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")

        api_key = _make_api_key()
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_rate_limiter] = lambda: AsyncMock(
            spec=RedisRateLimiter
        )
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @patch("dalston.gateway.api.v1.transcription.publish_job_created")
    def test_retry_failed_job_returns_200(
        self, mock_publish, client, mock_jobs_service
    ):
        """Test that retrying a failed job returns 200."""
        job = _make_job(JobStatus.PENDING.value, retry_count=1)
        mock_jobs_service.get_job_with_tasks.return_value = _make_job(
            JobStatus.FAILED.value
        )
        mock_jobs_service.retry_job.return_value = RetryResult(
            job=job,
            previous_retry_count=0,
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(JOB_ID)
        assert data["status"] == "pending"
        assert data["retry_count"] == 1
        assert "retry" in data["message"].lower()
        mock_jobs_service.retry_job.assert_awaited_once()
        mock_publish.assert_awaited_once()

    def test_retry_nonexistent_job_returns_404(self, client, mock_jobs_service):
        """Test that retrying a nonexistent job returns 404."""
        mock_jobs_service.get_job_with_tasks.return_value = None

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_retry_running_job_returns_409(self, client, mock_jobs_service):
        """Test that retrying a running job returns 409."""
        mock_jobs_service.get_job_with_tasks.return_value = _make_job(
            JobStatus.RUNNING.value
        )
        mock_jobs_service.retry_job.side_effect = ValueError(
            "Cannot retry job in 'running' state. "
            "Only failed jobs can be retried."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 409
        assert "running" in response.json()["detail"]

    def test_retry_completed_job_returns_409(self, client, mock_jobs_service):
        """Test that retrying a completed job returns 409."""
        mock_jobs_service.get_job_with_tasks.return_value = _make_job(
            JobStatus.COMPLETED.value
        )
        mock_jobs_service.retry_job.side_effect = ValueError(
            "Cannot retry job in 'completed' state. "
            "Only failed jobs can be retried."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 409
        assert "completed" in response.json()["detail"]

    def test_retry_max_retries_exceeded_returns_409(
        self, client, mock_jobs_service
    ):
        """Test that retrying a job at max retry limit returns 409."""
        mock_jobs_service.get_job_with_tasks.return_value = _make_job(
            JobStatus.FAILED.value, retry_count=3
        )
        mock_jobs_service.retry_job.side_effect = ValueError(
            "Job has reached the maximum retry limit (3)."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 409
        assert "maximum retry limit" in response.json()["detail"]

    def test_retry_purged_audio_returns_410(self, client, mock_jobs_service):
        """Test that retrying a job with purged audio returns 410."""
        job = _make_job(JobStatus.FAILED.value)
        retention = MagicMock()
        retention.purged_at = datetime.now(UTC)
        job.retention_policy = retention
        mock_jobs_service.get_job_with_tasks.return_value = job

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 410
        assert "purged" in response.json()["detail"].lower()


class TestRetryTranscriptionAuthorization:
    """Tests for authorization on retry endpoint."""

    def test_retry_requires_jobs_write_scope(self):
        """Test that POST retry requires jobs:write scope."""
        from dalston.gateway.dependencies import (
            get_audit_service,
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )
        from dalston.gateway.services.rate_limiter import RedisRateLimiter

        api_key = _make_api_key(scopes=[Scope.JOBS_READ])  # No write scope

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_redis] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: AsyncMock(
            spec=JobsService
        )
        app.dependency_overrides[get_settings] = lambda: MagicMock(spec=Settings)
        app.dependency_overrides[get_rate_limiter] = lambda: AsyncMock(
            spec=RedisRateLimiter
        )
        app.dependency_overrides[get_audit_service] = lambda: AsyncMock()
        app.dependency_overrides[require_auth] = lambda: api_key

        client = TestClient(app)
        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/retry")

        assert response.status_code == 403
