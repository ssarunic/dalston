"""Integration tests for DELETE /v1/audio/transcriptions/{job_id} endpoint."""

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
from dalston.gateway.services.jobs import JobsService

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


class TestDeleteTranscription:
    """Tests for DELETE /v1/audio/transcriptions/{job_id}."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock(spec=Settings)
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.fixture
    def mock_security_manager(self):
        from dalston.gateway.security.manager import SecurityManager

        return MagicMock(spec=SecurityManager)

    @pytest.fixture
    def mock_principal(self):
        from dalston.gateway.security.principal import Principal

        principal = MagicMock(spec=Principal)
        principal.tenant_id = TENANT_ID
        principal.id = UUID("12345678-1234-1234-1234-123456789abc")
        principal.is_admin = False
        return principal

    @pytest.fixture
    def mock_audit_service(self):
        from dalston.common.audit import AuditService

        return AsyncMock(spec=AuditService)

    @pytest.fixture
    def app(
        self,
        mock_jobs_service,
        mock_db,
        mock_settings,
        mock_security_manager,
        mock_principal,
        mock_audit_service,
    ):
        from dalston.gateway.dependencies import (
            check_request_rate_limit,
            get_audit_service,
            get_db,
            get_jobs_service,
            get_principal,
            get_security_manager,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")

        api_key = _make_api_key()
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[require_auth] = lambda: api_key
        app.dependency_overrides[get_security_manager] = lambda: mock_security_manager
        app.dependency_overrides[get_principal] = lambda: mock_principal
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[check_request_rate_limit] = lambda: None

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @patch("dalston.gateway.dependencies.StorageService")
    def test_delete_completed_job_returns_204(
        self, mock_storage_cls, client, mock_jobs_service
    ):
        """Test that deleting a completed job returns 204."""
        job = _make_job(JobStatus.COMPLETED.value)
        mock_jobs_service.delete_job_authorized.return_value = job

        mock_storage = AsyncMock()
        mock_storage_cls.return_value = mock_storage

        response = client.delete(f"/v1/audio/transcriptions/{JOB_ID}")

        assert response.status_code == 204
        mock_jobs_service.delete_job_authorized.assert_awaited_once()
        mock_storage.delete_job_artifacts.assert_awaited_once_with(JOB_ID)

    def test_delete_nonexistent_job_returns_404(self, client, mock_jobs_service):
        """Test that deleting a nonexistent job returns 404."""
        from dalston.gateway.security.exceptions import ResourceNotFoundError

        mock_jobs_service.delete_job_authorized.side_effect = ResourceNotFoundError(
            "job", str(JOB_ID)
        )

        response = client.delete(f"/v1/audio/transcriptions/{JOB_ID}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_delete_running_job_returns_409(self, client, mock_jobs_service):
        """Test that deleting a running job returns 409."""
        mock_jobs_service.delete_job_authorized.side_effect = ValueError(
            "Cannot delete job in 'running' state. "
            "Only completed, failed, or cancelled jobs can be deleted."
        )

        response = client.delete(f"/v1/audio/transcriptions/{JOB_ID}")

        assert response.status_code == 409
        assert "running" in response.json()["detail"]

    @patch("dalston.gateway.dependencies.StorageService")
    def test_delete_succeeds_even_if_s3_cleanup_fails(
        self, mock_storage_cls, client, mock_jobs_service
    ):
        """Test that deletion returns 204 even if S3 cleanup fails."""
        job = _make_job(JobStatus.COMPLETED.value)
        mock_jobs_service.delete_job_authorized.return_value = job

        mock_storage = AsyncMock()
        mock_storage.delete_job_artifacts.side_effect = Exception("S3 error")
        mock_storage_cls.return_value = mock_storage

        response = client.delete(f"/v1/audio/transcriptions/{JOB_ID}")

        # DB record deleted, S3 failure is best-effort
        assert response.status_code == 204


class TestDeleteTranscriptionAuthorization:
    """Tests for authorization on DELETE endpoint."""

    def test_delete_requires_jobs_write_scope(self):
        """Test that DELETE requires jobs:write scope.

        Authorization is now enforced via SecurityManager in the service layer.
        The service's delete_job_authorized raises AuthorizationError when the
        principal lacks JOB_DELETE_OWN permission.
        """
        from dalston.common.audit import AuditService
        from dalston.gateway.dependencies import (
            check_request_rate_limit,
            get_audit_service,
            get_db,
            get_jobs_service,
            get_principal,
            get_security_manager,
            get_settings,
            get_storage_service,
            require_auth,
        )
        from dalston.gateway.middleware.security_error_handler import (
            SecurityErrorHandlerMiddleware,
        )
        from dalston.gateway.security.exceptions import AuthorizationError
        from dalston.gateway.security.manager import SecurityManager
        from dalston.gateway.security.permissions import Permission
        from dalston.gateway.security.principal import Principal

        api_key = _make_api_key(scopes=[Scope.JOBS_READ])  # No write scope

        # Create mock principal and security manager
        mock_principal = MagicMock(spec=Principal)
        mock_principal.tenant_id = TENANT_ID
        mock_principal.id = UUID("12345678-1234-1234-1234-123456789abc")
        mock_principal.is_admin = False

        mock_security_manager = MagicMock(spec=SecurityManager)

        # Configure the mock jobs service to raise AuthorizationError
        mock_jobs_service = AsyncMock(spec=JobsService)
        mock_jobs_service.delete_job_authorized.side_effect = AuthorizationError(
            message="Access denied: requires permission job:delete_own",
            required_permission=Permission.JOB_DELETE_OWN.value,
        )

        app = FastAPI()
        # Add middleware to convert security exceptions to HTTP responses
        app.add_middleware(SecurityErrorHandlerMiddleware)
        app.include_router(transcription_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: MagicMock(spec=Settings)
        app.dependency_overrides[get_storage_service] = lambda: AsyncMock()
        app.dependency_overrides[require_auth] = lambda: api_key
        app.dependency_overrides[get_security_manager] = lambda: mock_security_manager
        app.dependency_overrides[get_principal] = lambda: mock_principal
        app.dependency_overrides[get_audit_service] = lambda: AsyncMock(
            spec=AuditService
        )
        app.dependency_overrides[check_request_rate_limit] = lambda: None

        client = TestClient(app, raise_server_exceptions=False)
        response = client.delete(f"/v1/audio/transcriptions/{JOB_ID}")

        assert response.status_code == 403
