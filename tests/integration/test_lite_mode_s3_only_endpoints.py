"""Integration tests for explicit lite-mode errors on S3-only endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.config import Settings
from dalston.config import get_settings as load_settings
from dalston.gateway.api.v1.realtime_sessions import router as realtime_sessions_router
from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.error_codes import Err
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
JOB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SESSION_ID = "sess_1234567890"


def _make_api_key(scopes: list[Scope] | None = None) -> APIKey:
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123def456",
        prefix="dk_abc1234",
        name="Test Key",
        tenant_id=TENANT_ID,
        scopes=scopes or [Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.REALTIME],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


def _make_lite_settings() -> Settings:
    settings = MagicMock(spec=Settings)
    settings.runtime_mode = "lite"
    settings.s3_bucket = "test-bucket"
    return settings


def _make_principal() -> Principal:
    principal = MagicMock(spec=Principal)
    principal.tenant_id = TENANT_ID
    principal.id = UUID("12345678-1234-1234-1234-123456789abc")
    principal.is_admin = False
    principal.actor_type = "api_key"
    principal.actor_id = str(principal.id)
    return principal


def test_get_job_audio_returns_explicit_lite_mode_error() -> None:
    from dalston.gateway.dependencies import (
        get_db,
        get_jobs_service,
        get_principal,
        get_security_manager,
        get_settings,
        get_storage_service,
        require_auth,
    )

    app = FastAPI()
    app.include_router(transcription_router, prefix="/v1")

    jobs_service = AsyncMock(spec=JobsService)
    storage_service = AsyncMock(spec=StorageService)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_jobs_service] = lambda: jobs_service
    app.dependency_overrides[get_storage_service] = lambda: storage_service
    app.dependency_overrides[get_settings] = _make_lite_settings
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[get_principal] = _make_principal
    app.dependency_overrides[require_auth] = _make_api_key

    client = TestClient(app)
    response = client.get(f"/v1/audio/transcriptions/{JOB_ID}/audio")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "lite_mode_unsupported"
    assert response.json()["detail"]["message"] == Err.DISTRIBUTED_MODE_REQUIRED.format(
        feature="Audio download URLs"
    )
    jobs_service.get_job_authorized.assert_not_awaited()
    storage_service.object_exists.assert_not_awaited()


def test_get_redacted_audio_returns_explicit_lite_mode_error() -> None:
    from dalston.gateway.dependencies import (
        get_db,
        get_jobs_service,
        get_principal,
        get_security_manager,
        get_settings,
        get_storage_service,
        require_auth,
    )

    app = FastAPI()
    app.include_router(transcription_router, prefix="/v1")

    jobs_service = AsyncMock(spec=JobsService)
    storage_service = AsyncMock(spec=StorageService)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_jobs_service] = lambda: jobs_service
    app.dependency_overrides[get_storage_service] = lambda: storage_service
    app.dependency_overrides[get_settings] = _make_lite_settings
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[get_principal] = _make_principal
    app.dependency_overrides[require_auth] = _make_api_key

    client = TestClient(app)
    response = client.get(f"/v1/audio/transcriptions/{JOB_ID}/audio/redacted")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "lite_mode_unsupported"
    assert response.json()["detail"]["message"] == Err.DISTRIBUTED_MODE_REQUIRED.format(
        feature="Redacted audio download URLs"
    )
    jobs_service.get_job_authorized.assert_not_awaited()
    storage_service.get_transcript.assert_not_awaited()


def test_get_realtime_session_audio_returns_explicit_lite_mode_error(
    monkeypatch,
) -> None:
    from dalston.gateway.dependencies import (
        get_db,
        get_principal,
        get_security_manager,
        get_settings,
        get_storage_service,
        require_auth,
    )

    app = FastAPI()
    app.include_router(realtime_sessions_router, prefix="/v1")

    monkeypatch.setenv("DALSTON_MODE", "lite")
    load_settings.cache_clear()

    storage_service = AsyncMock(spec=StorageService)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_storage_service] = lambda: storage_service
    app.dependency_overrides[get_settings] = _make_lite_settings
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[get_principal] = _make_principal
    app.dependency_overrides[require_auth] = _make_api_key

    client = TestClient(app)
    response = client.get(f"/v1/realtime/sessions/{SESSION_ID}/audio")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "lite_mode_unsupported"
    assert response.json()["detail"]["message"] == Err.DISTRIBUTED_MODE_REQUIRED.format(
        feature="Realtime audio download URLs"
    )
    storage_service.generate_presigned_url_from_uri.assert_not_awaited()
