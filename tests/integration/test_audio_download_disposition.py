"""Route tests for the ``download`` flag on audio URL endpoints.

With ``download=true`` the endpoints must sign a ``Content-Disposition:
attachment`` header into the presigned URL. Without it the URL stays inline so
the same object can be streamed by the console's audio player.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.config import Settings
from dalston.config import get_settings as load_settings
from dalston.db.session import get_db as session_get_db
from dalston.gateway.api.v1 import realtime_sessions as realtime_sessions_module
from dalston.gateway.api.v1.realtime_sessions import router as realtime_sessions_router
from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
JOB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SESSION_ID = "sess_1234567890"
SIGNED_URL = "https://test-bucket.s3.amazonaws.com/signed"


def _make_api_key() -> APIKey:
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123def456",
        prefix="dk_abc1234",
        name="Test Key",
        tenant_id=TENANT_ID,
        scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.REALTIME],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


def _make_principal() -> Principal:
    principal = MagicMock(spec=Principal)
    principal.tenant_id = TENANT_ID
    principal.id = UUID("12345678-1234-1234-1234-123456789abc")
    principal.is_admin = False
    principal.actor_type = "api_key"
    principal.actor_id = str(principal.id)
    return principal


def _make_settings() -> Settings:
    return Settings(
        DALSTON_MODE="distributed",
        DALSTON_S3_BUCKET="test-bucket",
        DALSTON_S3_REGION="us-east-1",
        _env_file=None,
    )


def _make_storage(settings: Settings) -> StorageService:
    """Real StorageService so parse_s3_uri/attachment_disposition run for real.

    Only the S3-touching coroutines are replaced.
    """
    storage = StorageService(settings)
    storage.object_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
    storage.generate_presigned_url = AsyncMock(return_value=SIGNED_URL)  # type: ignore[method-assign]
    storage.generate_presigned_url_from_uri = AsyncMock(return_value=SIGNED_URL)  # type: ignore[method-assign]
    return storage


def _make_job(**overrides) -> MagicMock:
    job = MagicMock()
    job.id = JOB_ID
    job.tenant_id = TENANT_ID
    job.status = "completed"
    job.purged_at = None
    job.pii_redact_audio = True
    job.audio_uri = f"s3://test-bucket/jobs/{JOB_ID}/audio/input.wav"
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _job_app(storage: StorageService, job: MagicMock) -> TestClient:
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
    jobs_service.get_job_authorized.return_value = job
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_jobs_service] = lambda: jobs_service
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_settings] = _make_settings
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[get_principal] = _make_principal
    app.dependency_overrides[require_auth] = _make_api_key
    return TestClient(app)


@pytest.mark.parametrize(
    ("query", "expected_disposition"),
    [
        ("", None),
        ("?download=false", None),
        ("?download=true", f'attachment; filename="{JOB_ID}.wav"'),
    ],
)
def test_job_audio_download_flag_controls_disposition(
    query: str, expected_disposition: str | None
) -> None:
    storage = _make_storage(_make_settings())
    client = _job_app(storage, _make_job())

    response = client.get(f"/v1/audio/transcriptions/{JOB_ID}/audio{query}")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "url": SIGNED_URL,
        "expires_in": 3600,
        "type": "original",
    }
    storage.generate_presigned_url.assert_awaited_once()
    kwargs = storage.generate_presigned_url.await_args.kwargs
    assert kwargs["content_disposition"] == expected_disposition


def test_job_redacted_audio_download_names_file_with_redacted_suffix() -> None:
    storage = _make_storage(_make_settings())
    storage.get_transcript = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "pii_metadata": {
                "redacted_audio_uri": (
                    f"s3://test-bucket/jobs/{JOB_ID}/artifacts/redact/redacted.mp3"
                )
            }
        }
    )
    client = _job_app(storage, _make_job())

    response = client.get(
        f"/v1/audio/transcriptions/{JOB_ID}/audio/redacted?download=true"
    )

    assert response.status_code == 200, response.text
    assert response.json()["type"] == "redacted"
    kwargs = storage.generate_presigned_url.await_args.kwargs
    assert kwargs["content_disposition"] == (
        f'attachment; filename="{JOB_ID}-redacted.mp3"'
    )


def test_job_redacted_audio_inline_has_no_disposition() -> None:
    storage = _make_storage(_make_settings())
    storage.get_transcript = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "pii_metadata": {
                "redacted_audio_uri": (
                    f"s3://test-bucket/jobs/{JOB_ID}/artifacts/redact/redacted.mp3"
                )
            }
        }
    )
    client = _job_app(storage, _make_job())

    response = client.get(f"/v1/audio/transcriptions/{JOB_ID}/audio/redacted")

    assert response.status_code == 200, response.text
    assert (
        storage.generate_presigned_url.await_args.kwargs["content_disposition"] is None
    )


def _session_app(
    monkeypatch, storage: StorageService, audio_uri: str | None
) -> TestClient:
    from dalston.gateway.dependencies import (
        get_db,
        get_principal,
        get_security_manager,
        get_settings,
        get_storage_service,
        require_auth,
    )

    monkeypatch.setenv("DALSTON_MODE", "distributed")
    load_settings.cache_clear()

    session = MagicMock()
    session.id = SESSION_ID
    session.tenant_id = TENANT_ID
    session.audio_uri = audio_uri
    service = MagicMock()
    service.get_session_authorized = AsyncMock(return_value=session)
    monkeypatch.setattr(
        realtime_sessions_module, "RealtimeSessionService", lambda *a, **kw: service
    )

    app = FastAPI()
    app.include_router(realtime_sessions_router, prefix="/v1")
    # The realtime router depends on dalston.db.session.get_db directly, which in
    # distributed mode would try to migrate a real Postgres. Override both names.
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[session_get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_settings] = _make_settings
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[get_principal] = _make_principal
    app.dependency_overrides[require_auth] = _make_api_key
    return TestClient(app)


@pytest.mark.parametrize(
    ("query", "expected_disposition"),
    [
        ("", None),
        ("?download=true", f'attachment; filename="{SESSION_ID}.wav"'),
    ],
)
def test_session_audio_download_flag_controls_disposition(
    monkeypatch, query: str, expected_disposition: str | None
) -> None:
    storage = _make_storage(_make_settings())
    # Realtime audio may live in a different bucket than the batch artifacts.
    client = _session_app(
        monkeypatch, storage, f"s3://rt-bucket/realtime/{SESSION_ID}/audio.wav"
    )

    response = client.get(f"/v1/realtime/sessions/{SESSION_ID}/audio{query}")

    assert response.status_code == 200, response.text
    assert response.json() == {"url": SIGNED_URL, "expires_in": 3600}
    storage.generate_presigned_url_from_uri.assert_awaited_once()
    args, kwargs = storage.generate_presigned_url_from_uri.await_args
    assert args[0] == f"s3://rt-bucket/realtime/{SESSION_ID}/audio.wav"
    assert kwargs["require_expected_bucket"] is False
    assert kwargs["content_disposition"] == expected_disposition


def test_session_audio_download_with_invalid_uri_is_404(monkeypatch) -> None:
    storage = _make_storage(_make_settings())
    client = _session_app(monkeypatch, storage, "not-an-s3-uri")

    response = client.get(f"/v1/realtime/sessions/{SESSION_ID}/audio?download=true")

    assert response.status_code == 404
    storage.generate_presigned_url_from_uri.assert_not_awaited()
