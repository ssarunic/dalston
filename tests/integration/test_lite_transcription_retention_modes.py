from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.audio_probe import AudioMetadata
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope


def _build_app(
    *,
    mock_db: AsyncMock,
    mock_redis: AsyncMock,
    mock_jobs_service: AsyncMock,
    mock_rate_limiter: AsyncMock,
    mock_audit_service: AsyncMock,
    mock_ingestion_service: AsyncMock,
    principal: Principal,
) -> FastAPI:
    from dalston.gateway.dependencies import (
        get_audit_service,
        get_db,
        get_ingestion_service,
        get_jobs_service,
        get_principal_with_job_rate_limit,
        get_rate_limiter,
        get_redis,
        get_security_manager,
    )

    security_manager = MagicMock()
    security_manager.require_permission = MagicMock()

    app = FastAPI()
    app.include_router(transcription_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
    app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
    app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
    app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion_service
    app.dependency_overrides[get_principal_with_job_rate_limit] = lambda: principal
    app.dependency_overrides[get_security_manager] = lambda: security_manager
    return app


def _make_principal() -> Principal:
    api_key = APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123",
        prefix="dk_test",
        name="Test Key",
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.ADMIN],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )
    return Principal.from_api_key(api_key)


def _make_ingestion_service() -> AsyncMock:
    ingestion = AsyncMock()
    ingested = MagicMock()
    ingested.content = b"RIFF"
    ingested.filename = "test.wav"
    ingested.metadata = AudioMetadata(
        format="wav",
        duration=1.0,
        sample_rate=16000,
        channels=1,
        bit_depth=16,
    )
    ingestion.ingest.return_value = ingested
    return ingestion


def test_lite_retention_zero_default_returns_inline_and_is_ephemeral(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts_root = tmp_path / ".dalston" / "artifacts"
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "0")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(artifacts_root))

    mock_db = AsyncMock()
    mock_redis = AsyncMock()
    mock_jobs_service = AsyncMock()
    mock_rate_limiter = AsyncMock()
    mock_audit_service = AsyncMock()
    mock_ingestion_service = _make_ingestion_service()

    app = _build_app(
        mock_db=mock_db,
        mock_redis=mock_redis,
        mock_jobs_service=mock_jobs_service,
        mock_rate_limiter=mock_rate_limiter,
        mock_audit_service=mock_audit_service,
        mock_ingestion_service=mock_ingestion_service,
        principal=_make_principal(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", BytesIO(b"RIFF"), "audio/wav")},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["text"] == "lite transcript"
    assert payload["retention"]["mode"] == "none"
    mock_jobs_service.create_job.assert_not_awaited()
    mock_audit_service.log_job_created.assert_not_awaited()
    assert not artifacts_root.exists()


def test_lite_retention_positive_persists_artifacts_and_job(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifacts_root = tmp_path / ".dalston" / "artifacts"
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "0")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(artifacts_root))

    mock_db = AsyncMock()
    mock_redis = AsyncMock()
    mock_jobs_service = AsyncMock()
    mock_rate_limiter = AsyncMock()
    mock_audit_service = AsyncMock()
    mock_ingestion_service = _make_ingestion_service()

    job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.status = "pending"
    mock_job.created_at = datetime.now(UTC)
    mock_job.display_name = "test.wav"
    mock_jobs_service.create_job.return_value = mock_job

    app = _build_app(
        mock_db=mock_db,
        mock_redis=mock_redis,
        mock_jobs_service=mock_jobs_service,
        mock_rate_limiter=mock_rate_limiter,
        mock_audit_service=mock_audit_service,
        mock_ingestion_service=mock_ingestion_service,
        principal=_make_principal(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"retention": "30"},
            files={"file": ("test.wav", BytesIO(b"RIFF"), "audio/wav")},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == str(job_id)
    mock_jobs_service.create_job.assert_awaited_once()
    mock_audit_service.log_job_created.assert_awaited_once()

    # retention>0 keeps existing lite persistence behavior.
    audio_file = artifacts_root / "jobs" / str(job_id) / "audio" / "original.wav"
    transcript_file = artifacts_root / "jobs" / str(job_id) / "transcript.json"
    assert audio_file.exists()
    assert transcript_file.exists()
