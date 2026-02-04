"""Integration tests for authentication API endpoints.

Tests the /auth/* API endpoints including key management.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.auth import router as auth_router
from dalston.gateway.services.auth import (
    DEFAULT_EXPIRES_AT,
    APIKey,
    AuthService,
    Scope,
)


class TestListApiKeysEndpoint:
    """Tests for GET /auth/keys endpoint."""

    @pytest.fixture
    def mock_auth_service(self):
        service = AsyncMock(spec=AuthService)
        return service

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock admin API key."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_auth_service, admin_api_key):
        from dalston.gateway.dependencies import (
            get_auth_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(auth_router)

        # Override dependencies - use require_auth as the base dependency
        app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_list_keys_excludes_revoked_by_default(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that listing keys excludes revoked keys by default."""
        active_key = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_active1",
            name="Active Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_auth_service.list_api_keys.return_value = [active_key]

        response = client.get("/auth/keys")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["keys"]) == 1
        assert data["keys"][0]["name"] == "Active Key"
        assert data["keys"][0]["is_revoked"] is False

        # Verify include_revoked was False
        mock_auth_service.list_api_keys.assert_called_once_with(
            admin_api_key.tenant_id,
            include_revoked=False,
        )

    def test_list_keys_includes_revoked_when_requested(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that listing keys includes revoked when include_revoked=true."""
        active_key = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_active1",
            name="Active Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        revoked_key = APIKey(
            id=uuid4(),
            key_hash="hash2",
            prefix="dk_revoke",
            name="Revoked Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=datetime.now(UTC),
        )

        mock_auth_service.list_api_keys.return_value = [active_key, revoked_key]

        response = client.get("/auth/keys?include_revoked=true")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["keys"]) == 2

        # Verify include_revoked was True
        mock_auth_service.list_api_keys.assert_called_once_with(
            admin_api_key.tenant_id,
            include_revoked=True,
        )

    def test_list_keys_marks_current_key(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that the current key is marked with is_current=True."""
        other_key = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_other1",
            name="Other Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        # Return both the admin key and another key
        mock_auth_service.list_api_keys.return_value = [admin_api_key, other_key]

        response = client.get("/auth/keys")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

        # Find the current key and verify it's marked
        current_keys = [k for k in data["keys"] if k["is_current"]]
        other_keys = [k for k in data["keys"] if not k["is_current"]]

        assert len(current_keys) == 1
        assert len(other_keys) == 1
        assert current_keys[0]["id"] == str(admin_api_key.id)
        assert other_keys[0]["id"] == str(other_key.id)

    def test_list_keys_shows_expires_at(self, client, mock_auth_service, admin_api_key):
        """Test that expires_at is included in the response."""
        from datetime import timedelta

        custom_expires = datetime.now(UTC) + timedelta(days=30)
        key_with_expiry = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_expire",
            name="Expiring Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=custom_expires,
            revoked_at=None,
        )

        mock_auth_service.list_api_keys.return_value = [key_with_expiry]

        response = client.get("/auth/keys")

        assert response.status_code == 200
        data = response.json()
        assert data["keys"][0]["expires_at"] is not None
        # Parse the expires_at and verify it's close to our custom value
        response_expires = datetime.fromisoformat(
            data["keys"][0]["expires_at"].replace("Z", "+00:00")
        )
        assert abs((response_expires - custom_expires).total_seconds()) < 1


class TestRevokeApiKeyEndpoint:
    """Tests for DELETE /auth/keys/{key_id} endpoint."""

    @pytest.fixture
    def mock_auth_service(self):
        service = AsyncMock(spec=AuthService)
        return service

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock admin API key."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_auth_service, admin_api_key):
        from dalston.gateway.dependencies import (
            get_auth_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(auth_router)

        app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_revoke_key_prevents_self_revocation(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that revoking your own key returns 400 error."""
        # Setup mock to return the admin key itself
        mock_auth_service.get_api_key_by_id.return_value = admin_api_key

        response = client.delete(f"/auth/keys/{admin_api_key.id}")

        assert response.status_code == 400
        data = response.json()
        assert "Cannot revoke your own API key" in data["detail"]

    def test_revoke_key_success(self, client, mock_auth_service, admin_api_key):
        """Test successful key revocation."""
        other_key = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_other1",
            name="Other Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_auth_service.get_api_key_by_id.return_value = other_key
        mock_auth_service.revoke_api_key.return_value = True

        response = client.delete(f"/auth/keys/{other_key.id}")

        assert response.status_code == 204
        mock_auth_service.revoke_api_key.assert_called_once_with(other_key.id)

    def test_revoke_key_not_found(self, client, mock_auth_service, admin_api_key):
        """Test revoking a non-existent key returns 404."""
        mock_auth_service.get_api_key_by_id.return_value = None

        random_id = uuid4()
        response = client.delete(f"/auth/keys/{random_id}")

        assert response.status_code == 404
        data = response.json()
        assert "API key not found" in data["detail"]

    def test_revoke_key_different_tenant(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that revoking a key from a different tenant returns 404."""
        other_tenant_key = APIKey(
            id=uuid4(),
            key_hash="hash1",
            prefix="dk_other1",
            name="Other Tenant Key",
            tenant_id=UUID("11111111-1111-1111-1111-111111111111"),  # Different tenant
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_auth_service.get_api_key_by_id.return_value = other_tenant_key

        response = client.delete(f"/auth/keys/{other_tenant_key.id}")

        assert response.status_code == 404
        data = response.json()
        assert "API key not found" in data["detail"]


class TestCreateApiKeyEndpoint:
    """Tests for POST /auth/keys endpoint."""

    @pytest.fixture
    def mock_auth_service(self):
        service = AsyncMock(spec=AuthService)
        return service

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock admin API key."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_auth_service, admin_api_key):
        from dalston.gateway.dependencies import (
            get_auth_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(auth_router)

        app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_create_key_success(self, client, mock_auth_service, admin_api_key):
        """Test successful key creation."""
        new_key = APIKey(
            id=uuid4(),
            key_hash="newhash123",
            prefix="dk_newkey",
            name="New API Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=100,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_auth_service.create_api_key.return_value = (
            "dk_newkey_full_secret_key",
            new_key,
        )

        response = client.post(
            "/auth/keys",
            json={
                "name": "New API Key",
                "scopes": ["jobs:read", "jobs:write"],
                "rate_limit": 100,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New API Key"
        assert data["key"] == "dk_newkey_full_secret_key"
        assert "jobs:read" in data["scopes"]
        assert "jobs:write" in data["scopes"]
        assert data["rate_limit"] == 100
        assert data["expires_at"] is not None

    def test_create_key_includes_expires_at(
        self, client, mock_auth_service, admin_api_key
    ):
        """Test that created key response includes expires_at."""
        new_key = APIKey(
            id=uuid4(),
            key_hash="newhash123",
            prefix="dk_newkey",
            name="New Key",
            tenant_id=admin_api_key.tenant_id,
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        mock_auth_service.create_api_key.return_value = (
            "dk_newkey_secret",
            new_key,
        )

        response = client.post(
            "/auth/keys",
            json={"name": "New Key"},
        )

        assert response.status_code == 201
        data = response.json()
        assert "expires_at" in data
        # Default expires_at should be the distant future (2099-12-31)
        response_expires = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        )
        assert response_expires.year == 2099

    def test_create_key_invalid_scope(self, client, mock_auth_service, admin_api_key):
        """Test that invalid scope returns 400 error."""
        response = client.post(
            "/auth/keys",
            json={
                "name": "Bad Key",
                "scopes": ["invalid_scope"],
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "Invalid scope" in data["detail"]


class TestAuthApiKeyEndpointAuthorization:
    """Tests for authorization on auth endpoints."""

    @pytest.fixture
    def mock_auth_service(self):
        service = AsyncMock(spec=AuthService)
        return service

    def test_list_keys_requires_admin_scope(self, mock_auth_service):
        """Test that listing keys requires admin scope."""
        from dalston.gateway.dependencies import (
            get_auth_service,
            require_auth,
        )

        # API key without admin scope
        non_admin_key = APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_nonadm",
            name="Non-Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        app = FastAPI()
        app.include_router(auth_router)
        app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
        app.dependency_overrides[require_auth] = lambda: non_admin_key

        client = TestClient(app)
        response = client.get("/auth/keys")

        # Should fail with 403 Forbidden (missing admin scope)
        assert response.status_code == 403

    def test_create_key_requires_admin_scope(self, mock_auth_service):
        """Test that creating keys requires admin scope."""
        from dalston.gateway.dependencies import (
            get_auth_service,
            require_auth,
        )

        # API key without admin scope
        non_admin_key = APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_nonadm",
            name="Non-Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        app = FastAPI()
        app.include_router(auth_router)
        app.dependency_overrides[get_auth_service] = lambda: mock_auth_service
        app.dependency_overrides[require_auth] = lambda: non_admin_key

        client = TestClient(app)
        response = client.post(
            "/auth/keys",
            json={"name": "New Key"},
        )

        # Should fail with 403 Forbidden (missing admin scope)
        assert response.status_code == 403
