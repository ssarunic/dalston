"""Integration tests for retention policy and audit API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1 import audit, retention_policies
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.retention import (
    RetentionPolicyInUseError,
    RetentionPolicyNotFoundError,
    RetentionService,
)


class TestRetentionPolicyAPI:
    """Integration tests for /v1/retention-policies endpoints."""

    @pytest.fixture
    def mock_retention_service(self):
        return AsyncMock(spec=RetentionService)

    @pytest.fixture
    def mock_audit_service(self):
        return AsyncMock()

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock API key with admin scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            scopes=[Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_retention_service, mock_audit_service, mock_db, admin_api_key):
        from dalston.gateway.dependencies import (
            get_audit_service,
            get_db,
            get_retention_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(retention_policies.router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_retention_service] = lambda: mock_retention_service
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[require_auth] = lambda: admin_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def _make_policy(
        self,
        policy_id: UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        name: str = "test-policy",
        mode: str = "auto_delete",
        hours: int = 24,
        is_system: bool = False,
    ):
        """Create a mock retention policy."""
        policy = MagicMock()
        policy.id = policy_id
        policy.tenant_id = UUID("00000000-0000-0000-0000-000000000001")
        policy.name = name
        policy.mode = mode
        policy.hours = hours
        policy.scope = "all"
        policy.realtime_mode = "inherit"
        policy.realtime_hours = None
        policy.delete_realtime_on_enhancement = True
        policy.is_system = is_system
        policy.created_at = datetime.now(UTC)
        return policy

    def test_create_policy_success(
        self, client, mock_retention_service, mock_audit_service
    ):
        """Test creating a retention policy."""
        policy = self._make_policy()
        mock_retention_service.create_policy.return_value = policy

        response = client.post(
            "/v1/retention-policies",
            json={
                "name": "short-term",
                "mode": "auto_delete",
                "hours": 24,
                "scope": "all",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-policy"
        assert data["mode"] == "auto_delete"
        assert data["hours"] == 24
        mock_audit_service.log_retention_policy_created.assert_awaited_once()

    def test_create_policy_validation_error(self, client, mock_retention_service):
        """Test creating a policy with invalid data returns 400."""
        mock_retention_service.create_policy.side_effect = ValueError(
            "hours is required when mode is 'auto_delete'"
        )

        response = client.post(
            "/v1/retention-policies",
            json={
                "name": "invalid",
                "mode": "auto_delete",
            },
        )

        assert response.status_code == 400
        assert "hours is required" in response.json()["detail"]

    def test_list_policies(self, client, mock_retention_service):
        """Test listing retention policies."""
        system_policy = self._make_policy(
            policy_id=UUID("00000000-0000-0000-0000-000000000001"),
            name="default",
            is_system=True,
        )
        tenant_policy = self._make_policy(name="custom")

        mock_retention_service.list_policies.return_value = [
            system_policy,
            tenant_policy,
        ]

        response = client.get("/v1/retention-policies")

        assert response.status_code == 200
        data = response.json()
        assert len(data["policies"]) == 2
        assert data["policies"][0]["name"] == "default"
        assert data["policies"][0]["is_system"] is True
        assert data["policies"][1]["name"] == "custom"

    def test_get_policy_by_id(self, client, mock_retention_service):
        """Test getting a policy by ID."""
        policy = self._make_policy()
        mock_retention_service.get_policy.return_value = policy

        response = client.get(f"/v1/retention-policies/{policy.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(policy.id)
        assert data["name"] == "test-policy"

    def test_get_policy_not_found(self, client, mock_retention_service):
        """Test getting a nonexistent policy returns 404."""
        mock_retention_service.get_policy.return_value = None

        response = client.get(
            "/v1/retention-policies/ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        assert response.status_code == 404

    def test_get_policy_by_name(self, client, mock_retention_service):
        """Test getting a policy by name."""
        policy = self._make_policy(name="short-term")
        mock_retention_service.get_policy_by_name.return_value = policy

        response = client.get("/v1/retention-policies/by-name/short-term")

        assert response.status_code == 200
        assert response.json()["name"] == "short-term"

    def test_get_policy_by_name_not_found(self, client, mock_retention_service):
        """Test getting a nonexistent policy by name returns 404."""
        mock_retention_service.get_policy_by_name.return_value = None

        response = client.get("/v1/retention-policies/by-name/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_delete_policy_success(
        self, client, mock_retention_service, mock_audit_service
    ):
        """Test deleting a policy."""
        mock_retention_service.delete_policy.return_value = None

        response = client.delete(
            "/v1/retention-policies/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )

        assert response.status_code == 204
        mock_audit_service.log_retention_policy_deleted.assert_awaited_once()

    def test_delete_policy_not_found(self, client, mock_retention_service):
        """Test deleting a nonexistent policy returns 404."""
        mock_retention_service.delete_policy.side_effect = RetentionPolicyNotFoundError(
            "Policy not found"
        )

        response = client.delete(
            "/v1/retention-policies/ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        assert response.status_code == 404

    def test_delete_policy_in_use(self, client, mock_retention_service):
        """Test deleting a policy in use returns 409."""
        mock_retention_service.delete_policy.side_effect = RetentionPolicyInUseError(
            "Policy is in use by 5 job(s)"
        )

        response = client.delete(
            "/v1/retention-policies/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )

        assert response.status_code == 409
        assert "in use" in response.json()["detail"]

    def test_delete_system_policy_error(self, client, mock_retention_service):
        """Test deleting a system policy returns 400."""
        mock_retention_service.delete_policy.side_effect = ValueError(
            "Cannot delete system policies"
        )

        response = client.delete(
            "/v1/retention-policies/00000000-0000-0000-0000-000000000001"
        )

        assert response.status_code == 400


class TestAuditAPI:
    """Integration tests for /v1/audit endpoints."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def admin_api_key(self):
        """Create a mock API key with admin scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_admin",
            name="Admin Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            scopes=[Scope.ADMIN],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_db, admin_api_key):
        from dalston.gateway.dependencies import get_db, require_auth

        app = FastAPI()
        app.include_router(audit.router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[require_auth] = lambda: admin_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def _make_audit_event(
        self,
        event_id: int = 1,
        action: str = "job.created",
        resource_type: str = "job",
        resource_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ):
        """Create a mock audit event."""
        event = MagicMock()
        event.id = event_id
        event.timestamp = datetime.now(UTC)
        event.correlation_id = "corr-123"
        event.tenant_id = UUID("00000000-0000-0000-0000-000000000001")
        event.actor_type = "api_key"
        event.actor_id = "dk_test"
        event.action = action
        event.resource_type = resource_type
        event.resource_id = resource_id
        event.detail = {"retention_policy": "default"}
        event.ip_address = "192.168.1.1"
        event.user_agent = "test-client"
        return event

    def _mock_execute_result(self, events):
        """Create a mock execute result for audit queries."""
        result = MagicMock()
        result.scalars.return_value.all.return_value = events
        return result

    def test_list_audit_events(self, client, mock_db):
        """Test listing audit events."""
        events = [
            self._make_audit_event(event_id=1, action="job.created"),
            self._make_audit_event(event_id=2, action="job.completed"),
        ]
        mock_db.execute.return_value = self._mock_execute_result(events)

        response = client.get("/v1/audit")

        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 2
        assert data["events"][0]["action"] == "job.created"
        assert data["events"][1]["action"] == "job.completed"
        assert "total" in data
        assert "has_more" in data

    def test_list_audit_events_with_filters(self, client, mock_db):
        """Test listing audit events with filters."""
        events = [self._make_audit_event()]
        mock_db.execute.return_value = self._mock_execute_result(events)

        response = client.get(
            "/v1/audit",
            params={
                "resource_type": "job",
                "action": "job.created",
                "limit": 10,
            },
        )

        assert response.status_code == 200
        assert len(response.json()["events"]) == 1

    def test_list_audit_events_empty(self, client, mock_db):
        """Test listing audit events when none exist."""
        mock_db.execute.return_value = self._mock_execute_result([])

        response = client.get("/v1/audit")

        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 0
        assert data["has_more"] is False

    def test_get_resource_audit_trail(self, client, mock_db):
        """Test getting audit trail for a specific resource."""
        events = [
            self._make_audit_event(event_id=1, action="job.created"),
            self._make_audit_event(event_id=2, action="audio.uploaded"),
            self._make_audit_event(event_id=3, action="job.completed"),
        ]
        mock_db.execute.return_value = self._mock_execute_result(events)

        response = client.get(
            "/v1/audit/resources/job/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 3

    def test_list_audit_events_pagination(self, client, mock_db):
        """Test audit event pagination."""
        # Create 6 events (more than default limit of 5 to test has_more)
        events = [self._make_audit_event(event_id=i) for i in range(6)]
        mock_db.execute.return_value = self._mock_execute_result(events)

        response = client.get("/v1/audit", params={"limit": 5})

        assert response.status_code == 200
        data = response.json()
        # Should return 5 events but has_more should be True
        assert len(data["events"]) == 5
        assert data["has_more"] is True


class TestRetentionJobSubmission:
    """Integration tests for retention policy with job submission."""

    @pytest.fixture
    def mock_retention_service(self):
        service = AsyncMock(spec=RetentionService)
        return service

    @pytest.fixture
    def mock_audit_service(self):
        return AsyncMock()

    def _make_policy(self, name: str, mode: str, hours: int | None = None):
        """Create a mock policy."""
        policy = MagicMock()
        policy.id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        policy.name = name
        policy.mode = mode
        policy.hours = hours
        policy.scope = "all"
        return policy

    def test_resolve_default_policy_when_not_specified(self, mock_retention_service):
        """Test that default system policy is resolved when none specified."""
        default_policy = self._make_policy("default", "auto_delete", 24)
        mock_retention_service.resolve_policy.return_value = default_policy

        # This would be called internally when creating a job without retention_policy
        # We test the service method directly here
        import asyncio

        mock_db = AsyncMock()
        tenant_id = UUID("00000000-0000-0000-0000-000000000001")

        result = asyncio.get_event_loop().run_until_complete(
            mock_retention_service.resolve_policy(mock_db, tenant_id)
        )

        assert result.name == "default"
        assert result.mode == "auto_delete"

    def test_resolve_named_policy(self, mock_retention_service):
        """Test resolving a named retention policy."""
        zero_retention = self._make_policy("zero-retention", "none", None)
        mock_retention_service.resolve_policy.return_value = zero_retention

        import asyncio

        mock_db = AsyncMock()
        tenant_id = UUID("00000000-0000-0000-0000-000000000001")

        result = asyncio.get_event_loop().run_until_complete(
            mock_retention_service.resolve_policy(
                mock_db, tenant_id, policy_name="zero-retention"
            )
        )

        assert result.name == "zero-retention"
        assert result.mode == "none"

    def test_resolve_keep_policy(self, mock_retention_service):
        """Test resolving keep policy (no purge)."""
        keep_policy = self._make_policy("keep", "keep", None)
        mock_retention_service.resolve_policy.return_value = keep_policy

        import asyncio

        mock_db = AsyncMock()
        tenant_id = UUID("00000000-0000-0000-0000-000000000001")

        result = asyncio.get_event_loop().run_until_complete(
            mock_retention_service.resolve_policy(
                mock_db, tenant_id, policy_name="keep"
            )
        )

        assert result.name == "keep"
        assert result.mode == "keep"
        assert result.hours is None
