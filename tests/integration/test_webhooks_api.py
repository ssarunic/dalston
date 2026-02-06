"""Integration tests for webhook management API endpoints.

Tests the /webhooks/* API endpoints including endpoint CRUD and delivery management.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.db.models import WebhookDeliveryModel, WebhookEndpointModel
from dalston.gateway.api.v1.webhooks import (
    get_webhook_endpoint_service,
)
from dalston.gateway.api.v1.webhooks import (
    router as webhooks_router,
)
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.webhook import WebhookValidationError
from dalston.gateway.services.webhook_endpoints import WebhookEndpointService


class TestCreateWebhookEndpoint:
    """Tests for POST /webhooks endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock admin API key with webhooks scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_create_endpoint_success(self, client, mock_service, admin_api_key):
        """Test successful webhook endpoint creation."""
        created_at = datetime.now(UTC)
        endpoint = WebhookEndpointModel(
            id=uuid4(),
            tenant_id=admin_api_key.tenant_id,
            url="https://example.com/webhook",
            description="Test webhook",
            events=["transcription.completed"],
            signing_secret="whsec_testsecret123",
            is_active=True,
            consecutive_failures=0,
            last_success_at=None,
            disabled_reason=None,
            created_at=created_at,
            updated_at=created_at,
        )

        mock_service.create_endpoint.return_value = (endpoint, "whsec_testsecret123")

        response = client.post(
            "/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["transcription.completed"],
                "description": "Test webhook",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["url"] == "https://example.com/webhook"
        assert data["events"] == ["transcription.completed"]
        assert data["description"] == "Test webhook"
        assert data["signing_secret"] == "whsec_testsecret123"
        assert data["is_active"] is True

    def test_create_endpoint_invalid_url(self, client, mock_service):
        """Test that invalid URL returns 400."""
        mock_service.create_endpoint.side_effect = WebhookValidationError(
            "Invalid URL: resolves to private IP"
        )

        response = client.post(
            "/webhooks",
            json={
                "url": "https://internal.corp/webhook",
                "events": ["transcription.completed"],
            },
        )

        assert response.status_code == 400
        assert "Invalid URL" in response.json()["detail"]

    def test_create_endpoint_invalid_events(self, client, mock_service):
        """Test that invalid events return 400."""
        mock_service.create_endpoint.side_effect = WebhookValidationError(
            "Invalid event types: {'bad.event'}"
        )

        response = client.post(
            "/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["bad.event"],
            },
        )

        assert response.status_code == 400
        assert "Invalid event types" in response.json()["detail"]


class TestListWebhookEndpoints:
    """Tests for GET /webhooks endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_list_endpoints_empty(self, client, mock_service):
        """Test listing endpoints when none exist."""
        mock_service.list_endpoints.return_value = []

        response = client.get("/webhooks")

        assert response.status_code == 200
        data = response.json()
        assert data["endpoints"] == []

    def test_list_endpoints_with_results(self, client, mock_service, admin_api_key):
        """Test listing endpoints returns all endpoints."""
        now = datetime.now(UTC)
        endpoints = [
            WebhookEndpointModel(
                id=uuid4(),
                tenant_id=admin_api_key.tenant_id,
                url="https://example.com/webhook1",
                description="First webhook",
                events=["transcription.completed"],
                signing_secret="whsec_secret1",
                is_active=True,
                consecutive_failures=0,
                last_success_at=None,
                disabled_reason=None,
                created_at=now,
                updated_at=now,
            ),
            WebhookEndpointModel(
                id=uuid4(),
                tenant_id=admin_api_key.tenant_id,
                url="https://example.com/webhook2",
                description="Second webhook",
                events=["*"],
                signing_secret="whsec_secret2",
                is_active=False,
                consecutive_failures=10,
                last_success_at=None,
                disabled_reason="auto_disabled",
                created_at=now,
                updated_at=now,
            ),
        ]
        mock_service.list_endpoints.return_value = endpoints

        response = client.get("/webhooks")

        assert response.status_code == 200
        data = response.json()
        assert len(data["endpoints"]) == 2
        # Secrets should not be included in list response
        assert "signing_secret" not in data["endpoints"][0]
        assert "signing_secret" not in data["endpoints"][1]

    def test_list_endpoints_filter_active(self, client, mock_service):
        """Test filtering endpoints by active status."""
        mock_service.list_endpoints.return_value = []

        response = client.get("/webhooks?is_active=true")

        assert response.status_code == 200
        mock_service.list_endpoints.assert_called_once()
        call_args = mock_service.list_endpoints.call_args
        assert call_args.kwargs["is_active"] is True


class TestGetWebhookEndpoint:
    """Tests for GET /webhooks/{endpoint_id} endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_get_endpoint_success(self, client, mock_service, admin_api_key):
        """Test getting a specific endpoint."""
        now = datetime.now(UTC)
        endpoint_id = uuid4()
        endpoint = WebhookEndpointModel(
            id=endpoint_id,
            tenant_id=admin_api_key.tenant_id,
            url="https://example.com/webhook",
            description="Test webhook",
            events=["transcription.completed", "transcription.failed"],
            signing_secret="whsec_secret",
            is_active=True,
            consecutive_failures=0,
            last_success_at=None,
            disabled_reason=None,
            created_at=now,
            updated_at=now,
        )
        mock_service.get_endpoint.return_value = endpoint

        response = client.get(f"/webhooks/{endpoint_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(endpoint_id)
        assert data["url"] == "https://example.com/webhook"
        # Secret should not be included
        assert "signing_secret" not in data

    def test_get_endpoint_not_found(self, client, mock_service):
        """Test 404 when endpoint doesn't exist."""
        mock_service.get_endpoint.return_value = None

        response = client.get(f"/webhooks/{uuid4()}")

        assert response.status_code == 404


class TestDeleteWebhookEndpoint:
    """Tests for DELETE /webhooks/{endpoint_id} endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_delete_endpoint_success(self, client, mock_service):
        """Test successful endpoint deletion."""
        mock_service.delete_endpoint.return_value = True

        response = client.delete(f"/webhooks/{uuid4()}")

        assert response.status_code == 204

    def test_delete_endpoint_not_found(self, client, mock_service):
        """Test 404 when endpoint doesn't exist."""
        mock_service.delete_endpoint.return_value = False

        response = client.delete(f"/webhooks/{uuid4()}")

        assert response.status_code == 404


class TestRotateSecret:
    """Tests for POST /webhooks/{endpoint_id}/rotate-secret endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_rotate_secret_success(self, client, mock_service, admin_api_key):
        """Test successful secret rotation."""
        now = datetime.now(UTC)
        endpoint_id = uuid4()
        endpoint = WebhookEndpointModel(
            id=endpoint_id,
            tenant_id=admin_api_key.tenant_id,
            url="https://example.com/webhook",
            description="Test webhook",
            events=["transcription.completed"],
            signing_secret="whsec_newsecret456",
            is_active=True,
            consecutive_failures=0,
            last_success_at=None,
            disabled_reason=None,
            created_at=now,
            updated_at=now,
        )
        mock_service.rotate_secret.return_value = (endpoint, "whsec_newsecret456")

        response = client.post(f"/webhooks/{endpoint_id}/rotate-secret")

        assert response.status_code == 200
        data = response.json()
        assert data["signing_secret"] == "whsec_newsecret456"

    def test_rotate_secret_not_found(self, client, mock_service):
        """Test 404 when endpoint doesn't exist."""
        mock_service.rotate_secret.return_value = None

        response = client.post(f"/webhooks/{uuid4()}/rotate-secret")

        assert response.status_code == 404


class TestRetryDelivery:
    """Tests for POST /webhooks/{endpoint_id}/deliveries/{delivery_id}/retry endpoint."""

    @pytest.fixture
    def mock_service(self):
        return AsyncMock(spec=WebhookEndpointService)

    @pytest.fixture
    def admin_api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin12",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.ADMIN, Scope.WEBHOOKS],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_service, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(webhooks_router)

        app.dependency_overrides[get_webhook_endpoint_service] = lambda: mock_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_retry_delivery_success(self, client, mock_service):
        """Test successful delivery retry."""
        now = datetime.now(UTC)
        endpoint_id = uuid4()
        delivery_id = uuid4()
        delivery = WebhookDeliveryModel(
            id=delivery_id,
            endpoint_id=endpoint_id,
            job_id=uuid4(),
            event_type="transcription.completed",
            payload={"event": "transcription.completed"},
            status="pending",  # Reset after retry
            attempts=3,
            last_attempt_at=now,
            last_status_code=500,
            last_error="HTTP 500",
            next_retry_at=now,
            created_at=now,
        )
        mock_service.retry_delivery.return_value = delivery

        response = client.post(
            f"/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    def test_retry_delivery_not_failed(self, client, mock_service):
        """Test 400 when delivery is not in failed status."""
        mock_service.retry_delivery.side_effect = ValueError(
            "Can only retry failed deliveries, current status: pending"
        )

        response = client.post(f"/webhooks/{uuid4()}/deliveries/{uuid4()}/retry")

        assert response.status_code == 400
        assert "Can only retry failed deliveries" in response.json()["detail"]

    def test_retry_delivery_not_found(self, client, mock_service):
        """Test 404 when delivery doesn't exist."""
        mock_service.retry_delivery.return_value = None

        response = client.post(f"/webhooks/{uuid4()}/deliveries/{uuid4()}/retry")

        assert response.status_code == 404
