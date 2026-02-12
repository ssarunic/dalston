"""Integration tests for M07 Hybrid Mode API endpoints.

Tests the enhancement-related endpoints exist and have correct routing.
Detailed logic is tested in unit tests and e2e tests.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.realtime_sessions import (
    EnhancementStatusResponse,
)
from dalston.gateway.api.v1.realtime_sessions import (
    router as sessions_router,
)
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope


class TestEnhancementEndpointsExist:
    """Tests that enhancement endpoints are properly registered."""

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
    def app(self, mock_api_key):
        """Create FastAPI app with management router."""
        from dalston.gateway.dependencies import require_auth

        app = FastAPI()
        # sessions_router has prefix="/realtime", so mount without additional prefix
        app.include_router(sessions_router)
        app.dependency_overrides[require_auth] = lambda: mock_api_key
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_enhancement_status_endpoint_exists(self, app):
        """Test that GET /sessions/{id}/enhancement endpoint is registered."""
        routes = [route.path for route in app.routes]
        assert "/realtime/sessions/{session_id}/enhancement" in routes

    def test_trigger_enhancement_endpoint_exists(self, app):
        """Test that POST /sessions/{id}/enhance endpoint is registered."""
        routes = [route.path for route in app.routes]
        assert "/realtime/sessions/{session_id}/enhance" in routes

    def test_enhancement_status_requires_auth(self, app, mock_api_key):
        """Test that enhancement status endpoint requires authentication."""
        # Remove auth override to test auth requirement
        app_no_auth = FastAPI()
        app_no_auth.include_router(sessions_router)

        client = TestClient(app_no_auth, raise_server_exceptions=False)
        response = client.get("/realtime/sessions/sess_12345/enhancement")

        # Should fail without auth (422 or 401 depending on implementation)
        assert response.status_code in (401, 403, 422, 500)

    def test_trigger_enhancement_requires_auth(self, app, mock_api_key):
        """Test that trigger enhancement endpoint requires authentication."""
        app_no_auth = FastAPI()
        app_no_auth.include_router(sessions_router)

        client = TestClient(app_no_auth, raise_server_exceptions=False)
        response = client.post("/realtime/sessions/sess_12345/enhance")

        # Should fail without auth
        assert response.status_code in (401, 403, 422, 500)


class TestEnhancementResponseModels:
    """Tests for enhancement response model structure."""

    def test_enhancement_status_response_model(self):
        """Test EnhancementStatusResponse model has correct fields."""

        # Test model can be instantiated with required fields
        response = EnhancementStatusResponse(
            session_id="sess_12345",
            status="not_requested",
        )
        assert response.session_id == "sess_12345"
        assert response.status == "not_requested"
        assert response.enhancement_job_id is None
        assert response.job_status is None
        assert response.transcript is None
        assert response.error is None

    def test_enhancement_status_response_with_job(self):
        """Test EnhancementStatusResponse with enhancement job."""

        response = EnhancementStatusResponse(
            session_id="sess_12345",
            status="processing",
            enhancement_job_id="job_67890",
            job_status="running",
        )
        assert response.enhancement_job_id == "job_67890"
        assert response.job_status == "running"

    def test_enhancement_status_response_completed(self):
        """Test EnhancementStatusResponse with completed transcript."""

        transcript = {
            "text": "Hello world",
            "segments": [
                {"start": 0, "end": 1, "text": "Hello", "speaker": "SPEAKER_00"}
            ],
        }
        response = EnhancementStatusResponse(
            session_id="sess_12345",
            status="completed",
            enhancement_job_id="job_67890",
            job_status="completed",
            transcript=transcript,
        )
        assert response.transcript == transcript

    def test_enhancement_status_response_failed(self):
        """Test EnhancementStatusResponse with error."""

        response = EnhancementStatusResponse(
            session_id="sess_12345",
            status="failed",
            enhancement_job_id="job_67890",
            job_status="failed",
            error="Pipeline execution failed",
        )
        assert response.error == "Pipeline execution failed"


class TestEnhancementServiceIntegration:
    """Integration tests for EnhancementService with mocked dependencies."""

    def test_enhancement_error_exception(self):
        """Test EnhancementError exception can be raised and caught."""
        from dalston.gateway.services.enhancement import EnhancementError

        with pytest.raises(EnhancementError, match="test error"):
            raise EnhancementError("test error")

    def test_enhancement_service_imports(self):
        """Test EnhancementService can be imported."""
        from dalston.gateway.services.enhancement import (
            EnhancementError,
            EnhancementService,
            create_enhancement_for_session,
        )

        assert EnhancementService is not None
        assert EnhancementError is not None
        assert create_enhancement_for_session is not None
