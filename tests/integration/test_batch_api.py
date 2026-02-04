"""Integration tests for batch API endpoints.

Tests the jobs-related API endpoints including stats.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.jobs import router as jobs_router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.jobs import JobsService, JobStats


class TestJobsStatsEndpoint:
    """Tests for GET /v1/jobs/stats endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        service = AsyncMock(spec=JobsService)
        return service

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_api_key(self):
        """Create a mock API key with jobs:read scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_db, mock_api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(jobs_router, prefix="/v1")

        # Override dependencies - use require_auth as the base dependency
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_get_job_stats_success(self, client, mock_jobs_service):
        """Test successful job stats retrieval."""
        mock_jobs_service.get_stats.return_value = JobStats(
            running=3,
            queued=7,
            completed_today=15,
            failed_today=2,
        )

        response = client.get("/v1/jobs/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] == 3
        assert data["queued"] == 7
        assert data["completed_today"] == 15
        assert data["failed_today"] == 2

    def test_get_job_stats_empty(self, client, mock_jobs_service):
        """Test job stats with zero counts."""
        mock_jobs_service.get_stats.return_value = JobStats(
            running=0,
            queued=0,
            completed_today=0,
            failed_today=0,
        )

        response = client.get("/v1/jobs/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] == 0
        assert data["queued"] == 0
        assert data["completed_today"] == 0
        assert data["failed_today"] == 0

    def test_get_job_stats_high_volume(self, client, mock_jobs_service):
        """Test job stats with high volume counts."""
        mock_jobs_service.get_stats.return_value = JobStats(
            running=100,
            queued=500,
            completed_today=2500,
            failed_today=50,
        )

        response = client.get("/v1/jobs/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] == 100
        assert data["queued"] == 500
        assert data["completed_today"] == 2500
        assert data["failed_today"] == 50

    def test_get_job_stats_uses_tenant_filter(
        self, mock_jobs_service, mock_db, mock_api_key
    ):
        """Test that job stats uses the tenant_id from the API key."""
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        app = FastAPI()
        app.include_router(jobs_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        mock_jobs_service.get_stats.return_value = JobStats(
            running=1,
            queued=2,
            completed_today=3,
            failed_today=0,
        )

        client = TestClient(app)
        response = client.get("/v1/jobs/stats")

        assert response.status_code == 200

        # Verify the service was called with the correct tenant_id
        mock_jobs_service.get_stats.assert_called_once()
        call_args = mock_jobs_service.get_stats.call_args
        assert call_args[1]["tenant_id"] == mock_api_key.tenant_id


class TestJobsStatsEndpointAuthorization:
    """Tests for authorization on GET /v1/jobs/stats endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        service = AsyncMock(spec=JobsService)
        return service

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        return db

    def test_get_job_stats_requires_jobs_read_scope(self, mock_jobs_service, mock_db):
        """Test that jobs stats requires jobs:read scope."""
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            require_auth,
        )

        # API key without jobs:read scope
        api_key_no_read = APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="No Read Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.REALTIME],  # No jobs:read
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

        app = FastAPI()
        app.include_router(jobs_router, prefix="/v1")
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[require_auth] = lambda: api_key_no_read

        client = TestClient(app)
        response = client.get("/v1/jobs/stats")

        # Should fail with 403 Forbidden (missing scope)
        assert response.status_code == 403
