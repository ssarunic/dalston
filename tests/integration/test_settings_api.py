"""Integration tests for settings API endpoints.

Tests the /api/console/settings/* endpoints including list, get, update, reset,
and conflict detection.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.db.models import SettingModel
from dalston.gateway.api.console import router as console_router
from dalston.gateway.dependencies import get_db, get_redis, get_session_router, require_auth
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.settings import (
    SettingsService,
    clear_settings_cache,
)

TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
KEY_ID = UUID("12345678-1234-1234-1234-123456789abc")


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear settings cache before each test."""
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def admin_api_key():
    """Admin API key fixture."""
    return APIKey(
        id=KEY_ID,
        key_hash="abc123def456",
        prefix="dk_admin12",
        name="Admin Key",
        tenant_id=TENANT_ID,
        scopes=[Scope.ADMIN],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


@pytest.fixture
def non_admin_api_key():
    """Non-admin API key fixture (should be rejected)."""
    return APIKey(
        id=uuid4(),
        key_hash="xyz789",
        prefix="dk_user1234",
        name="User Key",
        tenant_id=TENANT_ID,
        scopes=[Scope.JOBS_READ],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


@pytest.fixture
def mock_db():
    """Mock async database session."""
    db = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_session_router():
    """Mock session router."""
    router = MagicMock()
    return router


def _make_app(admin_key, mock_db, mock_redis, mock_session_router):
    """Create a FastAPI app with mocked dependencies."""
    app = FastAPI()
    app.include_router(console_router)

    app.dependency_overrides[require_auth] = lambda: admin_key
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_session_router] = lambda: mock_session_router

    return app


class TestListNamespaces:
    """Tests for GET /api/console/settings."""

    def test_lists_all_namespaces(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Should return all defined namespaces."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # Mock the DB query to return no overrides
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.get("/api/console/settings")

        assert response.status_code == 200
        data = response.json()
        assert "namespaces" in data
        namespaces = {ns["namespace"] for ns in data["namespaces"]}
        assert "rate_limits" in namespaces
        assert "engines" in namespaces
        assert "audio" in namespaces
        assert "retention" in namespaces
        assert "webhooks" in namespaces
        assert "system" in namespaces

    def test_system_namespace_not_editable(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """System namespace should be marked as not editable."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.get("/api/console/settings")

        assert response.status_code == 200
        system = next(
            ns for ns in response.json()["namespaces"] if ns["namespace"] == "system"
        )
        assert system["editable"] is False


class TestGetNamespace:
    """Tests for GET /api/console/settings/{namespace}."""

    def test_get_rate_limits_defaults(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Should return rate limit settings with default values."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # No DB overrides
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.get("/api/console/settings/rate_limits")

        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "rate_limits"
        assert data["editable"] is True
        assert len(data["settings"]) == 3

        # All should be at default values
        for setting in data["settings"]:
            assert setting["is_overridden"] is False

    def test_get_rate_limits_with_override(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Should show overridden values from DB."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # Return one DB override
        override = MagicMock(spec=SettingModel)
        override.key = "requests_per_minute"
        override.value = {"v": 1200}
        override.updated_at = datetime(2026, 2, 25, 10, 0, 0, tzinfo=UTC)
        override.namespace = "rate_limits"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [override]
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.get("/api/console/settings/rate_limits")

        assert response.status_code == 200
        data = response.json()

        rpm_setting = next(s for s in data["settings"] if s["key"] == "requests_per_minute")
        assert rpm_setting["value"] == 1200
        assert rpm_setting["is_overridden"] is True
        assert rpm_setting["default_value"] == 600  # Config default

    def test_get_unknown_namespace_returns_404(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Unknown namespace should return 404."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)
        client = TestClient(app)
        response = client.get("/api/console/settings/nonexistent")
        assert response.status_code == 404

    def test_get_system_namespace_returns_readonly(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """System namespace should return read-only system info."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)
        client = TestClient(app)
        response = client.get("/api/console/settings/system")

        assert response.status_code == 200
        data = response.json()
        assert data["editable"] is False
        keys = {s["key"] for s in data["settings"]}
        assert "redis_url" in keys
        assert "version" in keys


class TestUpdateNamespace:
    """Tests for PATCH /api/console/settings/{namespace}."""

    def test_update_rate_limit(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Should update a setting and return new values."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # First call: fetch existing rows (empty — no overrides)
        # Second call: select for upsert (no existing row)
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []
        mock_result_empty.scalar_one_or_none.return_value = None

        # After commit, refetch will return the new value
        new_row = MagicMock(spec=SettingModel)
        new_row.key = "requests_per_minute"
        new_row.value = {"v": 1200}
        new_row.updated_at = datetime(2026, 2, 25, 14, 0, 0, tzinfo=UTC)
        new_row.namespace = "rate_limits"

        mock_result_updated = MagicMock()
        mock_result_updated.scalars.return_value.all.return_value = [new_row]

        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return mock_result_empty
            return mock_result_updated

        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        client = TestClient(app)
        response = client.patch(
            "/api/console/settings/rate_limits",
            json={
                "settings": {"requests_per_minute": 1200},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "rate_limits"

    def test_update_unknown_key_returns_400(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Unknown key should return 400."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # Empty overrides
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.patch(
            "/api/console/settings/rate_limits",
            json={
                "settings": {"nonexistent_key": 42},
            },
        )

        assert response.status_code == 400
        assert "Unknown setting" in response.json()["detail"]

    def test_update_invalid_value_type_returns_400(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Invalid value type should return 400."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.patch(
            "/api/console/settings/rate_limits",
            json={
                "settings": {"requests_per_minute": "not_a_number"},
            },
        )

        assert response.status_code == 400
        assert "expected integer" in response.json()["detail"]

    def test_update_below_minimum_returns_400(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Value below minimum should return 400."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.patch(
            "/api/console/settings/rate_limits",
            json={
                "settings": {"requests_per_minute": 0},
            },
        )

        assert response.status_code == 400
        assert "minimum value" in response.json()["detail"]


class TestResetNamespace:
    """Tests for POST /api/console/settings/{namespace}/reset."""

    def test_reset_namespace(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Should delete overrides and return default values."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)

        # First call: fetch existing overrides (one override exists)
        override = MagicMock(spec=SettingModel)
        override.key = "requests_per_minute"
        override.value = {"v": 1200}
        override.updated_at = datetime(2026, 2, 25, 10, 0, 0, tzinfo=UTC)
        override.namespace = "rate_limits"

        mock_result_with_override = MagicMock()
        mock_result_with_override.scalars.return_value.all.return_value = [override]

        # After reset: no overrides
        mock_result_empty = MagicMock()
        mock_result_empty.scalars.return_value.all.return_value = []

        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result_with_override
            return mock_result_empty

        mock_db.execute = AsyncMock(side_effect=mock_execute)
        mock_db.commit = AsyncMock()

        client = TestClient(app)
        response = client.post("/api/console/settings/rate_limits/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "rate_limits"
        # After reset, nothing should be overridden
        for setting in data["settings"]:
            assert setting["is_overridden"] is False

    def test_reset_readonly_returns_400(self, admin_api_key, mock_db, mock_redis, mock_session_router):
        """Resetting read-only namespace should return 400."""
        app = _make_app(admin_api_key, mock_db, mock_redis, mock_session_router)
        client = TestClient(app)
        response = client.post("/api/console/settings/system/reset")
        assert response.status_code == 400


class TestPermissions:
    """Tests for auth enforcement."""

    def test_non_admin_gets_403(self, non_admin_api_key, mock_db, mock_redis, mock_session_router):
        """Non-admin API key should be rejected with 403."""
        app = _make_app(non_admin_api_key, mock_db, mock_redis, mock_session_router)
        client = TestClient(app)

        response = client.get("/api/console/settings")
        assert response.status_code == 403
