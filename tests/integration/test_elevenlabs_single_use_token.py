"""Integration tests for ElevenLabs single-use token endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.single_use_token import router as single_use_token_router
from dalston.gateway.services.auth import (
    DEFAULT_EXPIRES_AT,
    APIKey,
    Scope,
    SessionToken,
)


def _build_api_key(scopes: list[Scope]) -> APIKey:
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123def456",
        prefix="dk_test12",
        name="Test Key",
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        scopes=scopes,
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


def _build_session_token() -> SessionToken:
    return SessionToken(
        token_hash="def456abc789",
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        parent_key_id=UUID("12345678-1234-1234-1234-123456789abc"),
        scopes=[Scope.REALTIME],
        expires_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_auth_service():
    service = AsyncMock()
    session_token = _build_session_token()
    session_token.token_type = "speech_to_text"
    session_token.expires_at = datetime(2026, 3, 8, 12, 10, 0, tzinfo=UTC)
    service.create_session_token.return_value = ("tk_single_use", session_token)
    return service


@pytest.fixture
def app(mock_auth_service):
    from dalston.gateway.dependencies import (
        get_auth_service,
        get_security_manager,
        require_auth,
    )
    from dalston.gateway.security.manager import SecurityManager

    app = FastAPI()
    app.include_router(single_use_token_router, prefix="/v1")
    app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
    app.dependency_overrides[get_security_manager] = lambda: MagicMock(
        spec=SecurityManager
    )
    app.dependency_overrides[require_auth] = lambda: _build_api_key([Scope.REALTIME])
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_create_single_use_token_success(client, mock_auth_service):
    response = client.post("/v1/single-use-token/speech_to_text")
    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "tk_single_use"
    assert payload["token_type"] == "speech_to_text"

    call_kwargs = mock_auth_service.create_session_token.call_args.kwargs
    assert call_kwargs["single_use"] is True
    assert call_kwargs["token_type"] == "speech_to_text"


def test_create_single_use_token_rejects_unsupported_type(client):
    response = client.post("/v1/single-use-token/unknown_type")
    assert response.status_code == 400


def test_create_single_use_token_accepts_realtime_scribe(client):
    response = client.post("/v1/single-use-token/realtime_scribe")
    assert response.status_code == 200
    assert response.json()["token"] == "tk_single_use"


def test_create_single_use_token_requires_realtime_scope(app, mock_auth_service):
    from dalston.gateway.dependencies import require_auth

    app.dependency_overrides[require_auth] = lambda: _build_api_key([Scope.JOBS_READ])
    client = TestClient(app)

    response = client.post("/v1/single-use-token/speech_to_text")
    assert response.status_code == 403
    mock_auth_service.create_session_token.assert_not_called()


def test_create_single_use_token_rejects_session_token_identity(app, mock_auth_service):
    from dalston.gateway.dependencies import require_auth

    app.dependency_overrides[require_auth] = _build_session_token
    client = TestClient(app)

    response = client.post("/v1/single-use-token/speech_to_text")
    assert response.status_code == 403
    mock_auth_service.create_session_token.assert_not_called()
