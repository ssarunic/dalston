"""Integration test: lite mode job creation emits audit records (M57.1 Issue #7)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope

_JOB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class TestLiteJobAuditRecord:
    """Verify that the lite mode job-creation path emits a job.created audit record."""

    @pytest.fixture
    def mock_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123",
            prefix="dk_test",
            name="Test Key",
            tenant_id=_TENANT_ID,
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def mock_settings(self, tmp_path):
        settings = MagicMock()
        settings.runtime_mode = "lite"
        settings.lite_artifacts_dir = tmp_path / "artifacts"
        settings.retention_default_days = 30
        settings.s3_bucket = "test-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = None
        settings.default_model = "faster-whisper-base"
        return settings

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_jobs_service(self):
        service = AsyncMock()
        mock_job = MagicMock()
        mock_job.id = _JOB_ID
        mock_job.status = "pending"
        mock_job.created_at = datetime.now(UTC)
        mock_job.display_name = "test.wav"
        mock_job.completed_at = None
        service.create_job.return_value = mock_job
        return service

    @pytest.fixture
    def mock_audit_service(self):
        return AsyncMock()

    @pytest.fixture
    def mock_rate_limiter(self):
        limiter = AsyncMock()
        allow = MagicMock(allowed=True, limit=100, remaining=99, reset_seconds=60)
        limiter.check_request_rate.return_value = allow
        limiter.check_concurrent_jobs.return_value = allow
        return limiter

    @pytest.fixture
    def mock_ingestion_service(self):
        svc = AsyncMock()
        ingested = MagicMock()
        ingested.content = b"RIFF"
        ingested.filename = "test.wav"
        ingested.content_type = "audio/wav"
        ingested.file_size = 4
        ingested.audio_url = None
        ingested.metadata = MagicMock(
            format="wav",
            duration=1.0,
            sample_rate=16000,
            channels=1,
            bit_depth=16,
        )
        svc.ingest.return_value = ingested
        return svc

    @pytest.fixture
    def mock_pipeline(self, tmp_path):
        pipeline = AsyncMock()
        transcript_path = tmp_path / "artifacts" / "transcript.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline.run_job.return_value = {
            "job_id": str(_JOB_ID),
            "transcript_uri": f"file://{transcript_path}",
        }
        return pipeline

    @pytest.fixture
    def app(
        self,
        mock_api_key,
        mock_settings,
        mock_db,
        mock_jobs_service,
        mock_audit_service,
        mock_rate_limiter,
        mock_ingestion_service,
    ):
        from dalston.gateway.dependencies import (
            get_audit_service,
            get_db,
            get_ingestion_service,
            get_jobs_service,
            get_principal_with_job_rate_limit,
            get_rate_limiter,
            get_settings,
            require_auth,
        )
        from dalston.gateway.security.manager import get_security_manager
        from dalston.gateway.security.principal import Principal

        principal = Principal.from_api_key(mock_api_key)

        security_manager = MagicMock()
        security_manager.require_permission = MagicMock()  # allow all

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion_service
        app.dependency_overrides[require_auth] = lambda: mock_api_key
        app.dependency_overrides[get_principal_with_job_rate_limit] = lambda: principal
        app.dependency_overrides[get_security_manager] = lambda: security_manager
        return app

    def test_lite_job_creation_emits_job_created_audit_record(
        self,
        app,
        mock_audit_service,
        mock_jobs_service,
        mock_pipeline,
        tmp_path,
    ):
        """A lite job submission must emit audit action=job.created before pipeline runs."""
        mock_job = mock_jobs_service.create_job.return_value
        mock_job.status = "completed"
        mock_job.completed_at = datetime.now(UTC)

        from unittest.mock import patch

        with patch(
            "dalston.orchestrator.lite_main.build_pipeline",
            return_value=mock_pipeline,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/audio/transcriptions",
                    files={"file": ("test.wav", BytesIO(b"RIFF"), "audio/wav")},
                )

        assert response.status_code in (200, 201), response.text
        mock_audit_service.log_job_created.assert_awaited_once()
        call_kwargs = mock_audit_service.log_job_created.call_args.kwargs
        assert call_kwargs["job_id"] == _JOB_ID
        assert call_kwargs["tenant_id"] == _TENANT_ID

    def test_lite_job_pipeline_failure_still_has_audit_record(
        self,
        app,
        mock_audit_service,
        mock_jobs_service,
        tmp_path,
    ):
        """Audit record is created even when the pipeline raises an exception.

        Since log_job_created is called before pipeline execution, the audit
        record is persisted regardless of whether the pipeline succeeds or fails.
        """
        mock_job = mock_jobs_service.create_job.return_value

        failing_pipeline = AsyncMock()
        failing_pipeline.run_job.side_effect = RuntimeError("engine blew up")

        from unittest.mock import patch

        with patch(
            "dalston.orchestrator.lite_main.build_pipeline",
            return_value=failing_pipeline,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/audio/transcriptions",
                    files={"file": ("test.wav", BytesIO(b"RIFF"), "audio/wav")},
                )

        # Job creation failed but audit was already written
        assert response.status_code == 500
        mock_audit_service.log_job_created.assert_awaited_once()
        call_kwargs = mock_audit_service.log_job_created.call_args.kwargs
        assert call_kwargs["job_id"] == mock_job.id
