"""Integration tests for model endpoint authentication requirements (M45).

Tests verify that model management endpoints require proper authentication.
These tests fail if any protected endpoint is reachable without auth.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.models import router as models_router
from dalston.gateway.api.v1.pii import router as pii_router
from dalston.gateway.services.model_registry import ModelRegistryService


@pytest.fixture
def mock_model_registry_service():
    """Create a mock ModelRegistryService."""
    return AsyncMock(spec=ModelRegistryService)


@pytest.fixture
def app_no_auth(mock_model_registry_service):
    """Create FastAPI app without auth overrides (to test 401 behavior)."""
    from dalston.gateway.api.v1.models import get_model_registry_service
    from dalston.gateway.dependencies import get_db

    app = FastAPI()
    app.include_router(models_router, prefix="/v1")
    app.include_router(pii_router, prefix="/v1")

    # Mock DB and service, but NOT auth - we want to test auth failures
    mock_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_model_registry_service] = lambda: (
        mock_model_registry_service
    )

    return app


@pytest.fixture
def client_no_auth(app_no_auth):
    """Client without authentication."""
    return TestClient(app_no_auth, raise_server_exceptions=False)


class TestModelEndpointsRequireAuth:
    """Tests that verify model mutation endpoints require authentication."""

    def test_pull_model_requires_auth(self, client_no_auth):
        """POST /v1/models/{model_id}/pull requires authentication."""
        response = client_no_auth.post("/v1/models/test-model/pull")
        assert response.status_code == 401, (
            f"POST /v1/models/{{model_id}}/pull returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )

    def test_delete_model_requires_auth(self, client_no_auth):
        """DELETE /v1/models/{model_id} requires authentication."""
        response = client_no_auth.delete("/v1/models/test-model")
        assert response.status_code == 401, (
            f"DELETE /v1/models/{{model_id}} returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )

    def test_sync_models_requires_auth(self, client_no_auth):
        """POST /v1/models/sync requires authentication."""
        response = client_no_auth.post("/v1/models/sync")
        assert response.status_code == 401, (
            f"POST /v1/models/sync returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )

    def test_resolve_hf_model_requires_auth(self, client_no_auth):
        """POST /v1/models/hf/resolve requires authentication."""
        response = client_no_auth.post(
            "/v1/models/hf/resolve",
            json={"model_id": "openai/whisper-base"},
        )
        assert response.status_code == 401, (
            f"POST /v1/models/hf/resolve returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )

    def test_hf_mappings_requires_auth(self, client_no_auth):
        """GET /v1/models/hf/mappings requires authentication."""
        response = client_no_auth.get("/v1/models/hf/mappings")
        assert response.status_code == 401, (
            f"GET /v1/models/hf/mappings returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )


class TestPIIEndpointsRequireAuth:
    """Tests that verify PII endpoints require authentication."""

    def test_entity_types_requires_auth(self, client_no_auth):
        """GET /v1/pii/entity-types requires authentication."""
        response = client_no_auth.get("/v1/pii/entity-types")
        assert response.status_code == 401, (
            f"GET /v1/pii/entity-types returned {response.status_code}, "
            "expected 401. Endpoint may be missing authentication requirement."
        )


class TestModelEndpointsRequireAdminPermission:
    """Tests that verify model mutation endpoints require admin permission."""

    @pytest.fixture
    def non_admin_api_key(self):
        """Create a mock non-admin API key (jobs:read only)."""
        from datetime import UTC, datetime

        from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope

        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_user123",
            name="User Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],  # No admin scope
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app_with_non_admin(self, mock_model_registry_service, non_admin_api_key):
        """Create FastAPI app with non-admin auth."""
        from dalston.gateway.api.v1.models import get_model_registry_service
        from dalston.gateway.dependencies import get_db, require_auth
        from dalston.gateway.middleware.security_error_handler import (
            SecurityErrorHandlerMiddleware,
        )

        app = FastAPI()
        app.add_middleware(SecurityErrorHandlerMiddleware)
        app.include_router(models_router, prefix="/v1")
        app.include_router(pii_router, prefix="/v1")

        mock_db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_model_registry_service] = lambda: (
            mock_model_registry_service
        )
        # Override auth to return non-admin key
        app.dependency_overrides[require_auth] = lambda: non_admin_api_key

        return app

    @pytest.fixture
    def client_non_admin(self, app_with_non_admin):
        """Client with non-admin authentication."""
        return TestClient(app_with_non_admin, raise_server_exceptions=False)

    def test_pull_model_requires_admin_scope(self, client_non_admin):
        """POST /v1/models/{model_id}/pull requires admin permission."""
        response = client_non_admin.post("/v1/models/test-model/pull")
        assert response.status_code == 403, (
            f"POST /v1/models/{{model_id}}/pull returned {response.status_code}, "
            "expected 403. Endpoint may not be enforcing admin permission requirement."
        )

    def test_delete_model_requires_admin_scope(self, client_non_admin):
        """DELETE /v1/models/{model_id} requires admin permission."""
        response = client_non_admin.delete("/v1/models/test-model")
        assert response.status_code == 403, (
            f"DELETE /v1/models/{{model_id}} returned {response.status_code}, "
            "expected 403. Endpoint may not be enforcing admin permission requirement."
        )

    def test_sync_models_requires_admin_scope(self, client_non_admin):
        """POST /v1/models/sync requires admin permission."""
        response = client_non_admin.post("/v1/models/sync")
        assert response.status_code == 403, (
            f"POST /v1/models/sync returned {response.status_code}, "
            "expected 403. Endpoint may not be enforcing admin permission requirement."
        )

    def test_resolve_hf_model_requires_admin_scope(self, client_non_admin):
        """POST /v1/models/hf/resolve requires admin permission."""
        response = client_non_admin.post(
            "/v1/models/hf/resolve",
            json={"model_id": "openai/whisper-base"},
        )
        assert response.status_code == 403, (
            f"POST /v1/models/hf/resolve returned {response.status_code}, "
            "expected 403. Endpoint may not be enforcing admin permission requirement."
        )

    def test_hf_mappings_allowed_with_jobs_read(self, client_non_admin):
        """GET /v1/models/hf/mappings should work with jobs:read scope."""
        response = client_non_admin.get("/v1/models/hf/mappings")
        # This endpoint requires MODEL_READ permission, granted by JOBS_READ scope
        assert response.status_code == 200, (
            f"GET /v1/models/hf/mappings returned {response.status_code}, "
            "expected 200. Non-admin with jobs:read should have access."
        )
