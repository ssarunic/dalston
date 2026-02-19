"""Integration tests for POST /v1/audio/transcriptions/{job_id}/cancel endpoint."""

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
from dalston.gateway.services.jobs import CancelResult, JobsService
from dalston.gateway.services.rate_limiter import RedisRateLimiter

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


def _make_job(status: str) -> MagicMock:
    job = MagicMock()
    job.id = JOB_ID
    job.tenant_id = TENANT_ID
    job.status = status
    return job


class TestCancelTranscription:
    """Tests for POST /v1/audio/transcriptions/{job_id}/cancel."""

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
    def mock_rate_limiter(self):
        rate_limiter = AsyncMock(spec=RedisRateLimiter)
        rate_limiter.decrement_concurrent_jobs_once.return_value = True
        return rate_limiter

    @pytest.fixture
    def app(
        self, mock_jobs_service, mock_db, mock_redis, mock_settings, mock_rate_limiter
    ):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")

        api_key = _make_api_key()
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @patch("dalston.gateway.api.v1.transcription.publish_job_cancel_requested")
    def test_cancel_pending_job_returns_200(
        self, mock_publish, client, mock_jobs_service
    ):
        """Test that cancelling a pending job returns 200."""
        job = _make_job(JobStatus.PENDING.value)
        mock_jobs_service.cancel_job.return_value = CancelResult(
            job=job,
            status=JobStatus.CANCELLED,
            message="Job cancelled.",
            running_task_count=0,
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(JOB_ID)
        assert data["status"] == "cancelled"
        assert data["message"] == "Job cancelled."
        mock_jobs_service.cancel_job.assert_awaited_once()
        mock_publish.assert_awaited_once()

    @patch("dalston.gateway.api.v1.transcription.publish_job_cancel_requested")
    def test_cancel_immediate_decrements_counter(
        self, mock_publish, client, mock_jobs_service, mock_rate_limiter
    ):
        """Test that immediate cancellation decrements the concurrent job counter."""
        job = _make_job(JobStatus.PENDING.value)
        mock_jobs_service.cancel_job.return_value = CancelResult(
            job=job,
            status=JobStatus.CANCELLED,  # Immediate cancellation
            message="Job cancelled.",
            running_task_count=0,
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 200
        # Should have called the idempotent decrement
        mock_rate_limiter.decrement_concurrent_jobs_once.assert_awaited_once_with(
            JOB_ID, TENANT_ID
        )

    @patch("dalston.gateway.api.v1.transcription.publish_job_cancel_requested")
    def test_cancel_running_job_returns_cancelling(
        self, mock_publish, client, mock_jobs_service
    ):
        """Test that cancelling a running job with active tasks returns cancelling."""
        job = _make_job(JobStatus.RUNNING.value)
        mock_jobs_service.cancel_job.return_value = CancelResult(
            job=job,
            status=JobStatus.CANCELLING,
            message="Cancellation requested. 2 task(s) still running.",
            running_task_count=2,
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelling"
        assert "2 task(s) still running" in data["message"]

    @patch("dalston.gateway.api.v1.transcription.publish_job_cancel_requested")
    def test_cancel_cancelling_does_not_decrement(
        self, mock_publish, client, mock_jobs_service, mock_rate_limiter
    ):
        """Test that CANCELLING state does not decrement counter (orchestrator will)."""
        job = _make_job(JobStatus.RUNNING.value)
        mock_jobs_service.cancel_job.return_value = CancelResult(
            job=job,
            status=JobStatus.CANCELLING,  # Not immediate
            message="Cancellation requested. 2 task(s) still running.",
            running_task_count=2,
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 200
        # Should NOT have called decrement (orchestrator will do it later)
        mock_rate_limiter.decrement_concurrent_jobs_once.assert_not_awaited()

    def test_cancel_nonexistent_job_returns_404(self, client, mock_jobs_service):
        """Test that cancelling a nonexistent job returns 404."""
        mock_jobs_service.cancel_job.return_value = None

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_cancel_completed_job_returns_409(self, client, mock_jobs_service):
        """Test that cancelling a completed job returns 409."""
        mock_jobs_service.cancel_job.side_effect = ValueError(
            "Cannot cancel job in 'completed' state. "
            "Only pending or running jobs can be cancelled."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 409
        assert "completed" in response.json()["detail"]

    def test_cancel_failed_job_returns_409(self, client, mock_jobs_service):
        """Test that cancelling a failed job returns 409."""
        mock_jobs_service.cancel_job.side_effect = ValueError(
            "Cannot cancel job in 'failed' state. "
            "Only pending or running jobs can be cancelled."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 409
        assert "failed" in response.json()["detail"]

    def test_cancel_already_cancelled_job_returns_409(self, client, mock_jobs_service):
        """Test that cancelling an already cancelled job returns 409."""
        mock_jobs_service.cancel_job.side_effect = ValueError(
            "Cannot cancel job in 'cancelled' state. "
            "Only pending or running jobs can be cancelled."
        )

        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 409
        assert "cancelled" in response.json()["detail"]


class TestCancelTranscriptionAuthorization:
    """Tests for authorization on cancel endpoint."""

    def test_cancel_requires_jobs_write_scope(self):
        """Test that POST cancel requires jobs:write scope."""
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        api_key = _make_api_key(scopes=[Scope.JOBS_READ])  # No write scope

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_redis] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: AsyncMock(spec=JobsService)
        app.dependency_overrides[get_settings] = lambda: MagicMock(spec=Settings)
        app.dependency_overrides[get_rate_limiter] = lambda: AsyncMock(
            spec=RedisRateLimiter
        )
        app.dependency_overrides[require_auth] = lambda: api_key

        client = TestClient(app)
        response = client.post(f"/v1/audio/transcriptions/{JOB_ID}/cancel")

        assert response.status_code == 403
