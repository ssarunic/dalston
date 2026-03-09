"""Deny-by-default test suite for M45 Security Hardening.

Tests verify that protected endpoints require authentication and return 401
when accessed without valid credentials. This is a critical security test that
catches any endpoints accidentally left unprotected.

Run with: pytest tests/integration/test_deny_by_default.py -v
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def app_no_auth():
    """Create FastAPI app with mocked services but NO auth override.

    This allows us to test that endpoints properly return 401 when
    no authentication is provided.
    """
    from dalston.gateway.api.auth import router as auth_router
    from dalston.gateway.api.console import router as console_router
    from dalston.gateway.api.v1 import router as v1_router
    from dalston.gateway.dependencies import get_db

    app = FastAPI()

    # Include all routers (same as main.py does)
    app.include_router(v1_router)
    app.include_router(auth_router)
    app.include_router(console_router)

    # Mock DB but NOT auth - we want to test auth failures
    mock_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    return app


@pytest.fixture
def client_no_auth(app_no_auth):
    """Client without authentication."""
    return TestClient(app_no_auth, raise_server_exceptions=False)


# =============================================================================
# Protected endpoint definitions
# =============================================================================

# Transcription API (actual endpoints in transcription.py)
TRANSCRIPTION_ENDPOINTS = [
    ("POST", "/v1/audio/transcriptions", "Create transcription job"),
    ("GET", "/v1/audio/transcriptions", "List transcription jobs"),
    ("GET", f"/v1/audio/transcriptions/{uuid4()}", "Get job details"),
    ("PATCH", f"/v1/audio/transcriptions/{uuid4()}", "Update job"),
    ("DELETE", f"/v1/audio/transcriptions/{uuid4()}", "Delete job"),
    ("GET", f"/v1/audio/transcriptions/{uuid4()}/export/srt", "Export transcript"),
    ("GET", f"/v1/audio/transcriptions/{uuid4()}/audio", "Get audio URL"),
    (
        "GET",
        f"/v1/audio/transcriptions/{uuid4()}/audio/redacted",
        "Get redacted audio URL",
    ),
    ("POST", f"/v1/audio/transcriptions/{uuid4()}/cancel", "Cancel job"),
    ("DELETE", f"/v1/audio/transcriptions/{uuid4()}/audio", "Delete audio"),
]

# Task observability (actual endpoints in tasks.py)
TASK_ENDPOINTS = [
    ("GET", f"/v1/audio/transcriptions/{uuid4()}/tasks", "List tasks"),
    (
        "GET",
        f"/v1/audio/transcriptions/{uuid4()}/tasks/{uuid4()}/artifacts",
        "Get task artifacts",
    ),
]

# ElevenLabs compatible API (actual endpoints in speech_to_text.py)
ELEVENLABS_ENDPOINTS = [
    ("POST", "/v1/speech-to-text", "Create transcription (ElevenLabs)"),
    ("GET", f"/v1/speech-to-text/transcripts/{uuid4()}", "Get transcript status"),
    (
        "POST",
        "/v1/single-use-token/speech_to_text",
        "Create single-use token (ElevenLabs)",
    ),
    (
        "GET",
        f"/v1/speech-to-text/transcripts/{uuid4()}/export/srt",
        "Export transcript",
    ),
]

# Job statistics
JOB_STATS_ENDPOINTS = [
    ("GET", "/v1/jobs/stats", "Get job statistics"),
]

# Realtime status
REALTIME_STATUS_ENDPOINTS = [
    ("GET", "/v1/realtime/status", "Pool status"),
    ("GET", "/v1/realtime/workers", "Worker list"),
    ("GET", "/v1/realtime/workers/worker-1", "Worker details"),
]

# Realtime sessions (actual endpoints in realtime_sessions.py)
REALTIME_SESSION_ENDPOINTS = [
    ("GET", "/v1/realtime/sessions", "List sessions"),
    ("GET", f"/v1/realtime/sessions/{uuid4()}", "Get session"),
    ("DELETE", f"/v1/realtime/sessions/{uuid4()}", "Delete session"),
    ("GET", f"/v1/realtime/sessions/{uuid4()}/transcript", "Get session transcript"),
    ("GET", f"/v1/realtime/sessions/{uuid4()}/export/srt", "Export session transcript"),
    ("GET", f"/v1/realtime/sessions/{uuid4()}/audio", "Get session audio"),
]

# Engine discovery (actual endpoints in engines.py)
ENGINE_ENDPOINTS = [
    ("GET", "/v1/engines", "List engines"),
    ("GET", "/v1/engines/capabilities", "Get engine capabilities"),
]

# Webhook management
WEBHOOK_ENDPOINTS = [
    ("POST", "/v1/webhooks", "Create webhook"),
    ("GET", "/v1/webhooks", "List webhooks"),
    ("GET", f"/v1/webhooks/{uuid4()}", "Get webhook"),
    ("PATCH", f"/v1/webhooks/{uuid4()}", "Update webhook"),
    ("DELETE", f"/v1/webhooks/{uuid4()}", "Delete webhook"),
    ("POST", f"/v1/webhooks/{uuid4()}/rotate-secret", "Rotate secret"),
    ("GET", f"/v1/webhooks/{uuid4()}/deliveries", "List deliveries"),
    ("POST", f"/v1/webhooks/{uuid4()}/deliveries/{uuid4()}/retry", "Retry delivery"),
]

# Model management (protected endpoints only)
MODEL_ENDPOINTS = [
    ("POST", "/v1/models/test-model/pull", "Pull model"),
    ("DELETE", "/v1/models/test-model", "Delete model"),
    ("POST", "/v1/models/sync", "Sync models"),
    ("POST", "/v1/models/hf/resolve", "Resolve HF model"),
    ("GET", "/v1/models/hf/mappings", "Get HF mappings"),
]

# PII endpoints
PII_ENDPOINTS = [
    ("GET", "/v1/pii/entity-types", "List PII entity types"),
]

# Auth endpoints (admin required)
AUTH_ENDPOINTS = [
    ("POST", "/auth/keys", "Create API key"),
    ("GET", "/auth/keys", "List API keys"),
    ("GET", f"/auth/keys/{uuid4()}", "Get API key"),
    ("DELETE", f"/auth/keys/{uuid4()}", "Revoke API key"),
    ("GET", "/auth/me", "Get current key info"),
    ("POST", "/auth/tokens", "Create session token"),
]

# Console endpoints (admin required)
CONSOLE_ENDPOINTS = [
    ("GET", "/api/console/dashboard", "Dashboard overview"),
    ("GET", "/api/console/jobs", "Console list jobs"),
    ("GET", f"/api/console/jobs/{uuid4()}", "Console get job"),
    ("DELETE", f"/api/console/jobs/{uuid4()}", "Console delete job"),
    ("GET", f"/api/console/jobs/{uuid4()}/tasks", "Console get job tasks"),
    ("POST", f"/api/console/jobs/{uuid4()}/cancel", "Console cancel job"),
    ("GET", "/api/console/engines", "Console list engines"),
    ("GET", "/api/console/settings", "Console get all settings"),
    ("GET", "/api/console/settings/rate_limits", "Console get settings"),
    ("PATCH", "/api/console/settings/rate_limits", "Console update settings"),
    ("POST", "/api/console/settings/rate_limits/reset", "Console reset settings"),
    ("GET", "/api/console/metrics", "Console get metrics"),
]

# Combine all protected endpoints
ALL_PROTECTED_ENDPOINTS = (
    TRANSCRIPTION_ENDPOINTS
    + TASK_ENDPOINTS
    + ELEVENLABS_ENDPOINTS
    + JOB_STATS_ENDPOINTS
    + REALTIME_STATUS_ENDPOINTS
    + REALTIME_SESSION_ENDPOINTS
    + ENGINE_ENDPOINTS
    + WEBHOOK_ENDPOINTS
    + MODEL_ENDPOINTS
    + PII_ENDPOINTS
    + AUTH_ENDPOINTS
    + CONSOLE_ENDPOINTS
)


# =============================================================================
# Tests
# =============================================================================


class TestDenyByDefault:
    """Tests that all protected endpoints return 401 without authentication."""

    @pytest.mark.parametrize(
        "method,path,description",
        ALL_PROTECTED_ENDPOINTS,
        ids=[f"{m} {p.split('/')[-1][:20]}" for m, p, _ in ALL_PROTECTED_ENDPOINTS],
    )
    def test_endpoint_requires_auth(self, client_no_auth, method, path, description):
        """Verify endpoint returns 401 without authentication.

        This is a critical security test. Any failure here indicates an
        endpoint that may be accidentally exposed without authentication.
        """
        # Prepare request kwargs based on method
        kwargs = {}
        if method == "POST":
            # Provide minimal body for POST requests
            kwargs["json"] = {}
        elif method == "PATCH":
            kwargs["json"] = {}
        elif method == "DELETE" and "batch" in path:
            kwargs["json"] = {"job_ids": []}

        # Make request
        response = getattr(client_no_auth, method.lower())(path, **kwargs)

        assert response.status_code == 401, (
            f"{method} {path} ({description}) returned {response.status_code}, "
            f"expected 401. Endpoint may be missing authentication requirement. "
            f"Response: {response.text[:200]}"
        )


class TestTranscriptionEndpointsRequireAuth:
    """Focused tests for transcription API endpoints."""

    def test_create_transcription_requires_auth(self, client_no_auth):
        """POST /v1/audio/transcriptions requires authentication."""
        response = client_no_auth.post(
            "/v1/audio/transcriptions",
            json={},
        )
        assert response.status_code == 401

    def test_list_transcriptions_requires_auth(self, client_no_auth):
        """GET /v1/audio/transcriptions requires authentication."""
        response = client_no_auth.get("/v1/audio/transcriptions")
        assert response.status_code == 401

    def test_get_transcription_requires_auth(self, client_no_auth):
        """GET /v1/audio/transcriptions/{job_id} requires authentication."""
        job_id = uuid4()
        response = client_no_auth.get(f"/v1/audio/transcriptions/{job_id}")
        assert response.status_code == 401

    def test_delete_transcription_requires_auth(self, client_no_auth):
        """DELETE /v1/audio/transcriptions/{job_id} requires authentication."""
        job_id = uuid4()
        response = client_no_auth.delete(f"/v1/audio/transcriptions/{job_id}")
        assert response.status_code == 401


class TestWebhookEndpointsRequireAuth:
    """Focused tests for webhook API endpoints."""

    def test_create_webhook_requires_auth(self, client_no_auth):
        """POST /v1/webhooks requires authentication."""
        response = client_no_auth.post(
            "/v1/webhooks",
            json={"url": "https://example.com/hook", "events": ["job.completed"]},
        )
        assert response.status_code == 401

    def test_list_webhooks_requires_auth(self, client_no_auth):
        """GET /v1/webhooks requires authentication."""
        response = client_no_auth.get("/v1/webhooks")
        assert response.status_code == 401

    def test_get_webhook_requires_auth(self, client_no_auth):
        """GET /v1/webhooks/{endpoint_id} requires authentication."""
        endpoint_id = uuid4()
        response = client_no_auth.get(f"/v1/webhooks/{endpoint_id}")
        assert response.status_code == 401


class TestRealtimeSessionEndpointsRequireAuth:
    """Focused tests for realtime session API endpoints."""

    def test_list_sessions_requires_auth(self, client_no_auth):
        """GET /v1/realtime/sessions requires authentication."""
        response = client_no_auth.get("/v1/realtime/sessions")
        assert response.status_code == 401

    def test_get_session_requires_auth(self, client_no_auth):
        """GET /v1/realtime/sessions/{session_id} requires authentication."""
        session_id = uuid4()
        response = client_no_auth.get(f"/v1/realtime/sessions/{session_id}")
        assert response.status_code == 401

    def test_delete_session_requires_auth(self, client_no_auth):
        """DELETE /v1/realtime/sessions/{session_id} requires authentication."""
        session_id = uuid4()
        response = client_no_auth.delete(f"/v1/realtime/sessions/{session_id}")
        assert response.status_code == 401


class TestEngineEndpointsRequireAuth:
    """Focused tests for engine discovery endpoints."""

    def test_list_engines_requires_auth(self, client_no_auth):
        """GET /v1/engines requires authentication."""
        response = client_no_auth.get("/v1/engines")
        assert response.status_code == 401

    def test_get_capabilities_requires_auth(self, client_no_auth):
        """GET /v1/engines/capabilities requires authentication."""
        response = client_no_auth.get("/v1/engines/capabilities")
        assert response.status_code == 401


class TestAuthEndpointsRequireAuth:
    """Focused tests for authentication management endpoints."""

    def test_create_api_key_requires_auth(self, client_no_auth):
        """POST /auth/keys requires authentication."""
        response = client_no_auth.post(
            "/auth/keys",
            json={"name": "test-key"},
        )
        assert response.status_code == 401

    def test_list_api_keys_requires_auth(self, client_no_auth):
        """GET /auth/keys requires authentication."""
        response = client_no_auth.get("/auth/keys")
        assert response.status_code == 401

    def test_revoke_api_key_requires_auth(self, client_no_auth):
        """DELETE /auth/keys/{key_id} requires authentication."""
        key_id = uuid4()
        response = client_no_auth.delete(f"/auth/keys/{key_id}")
        assert response.status_code == 401

    def test_get_current_key_requires_auth(self, client_no_auth):
        """GET /auth/me requires authentication."""
        response = client_no_auth.get("/auth/me")
        assert response.status_code == 401


class TestConsoleEndpointsRequireAuth:
    """Focused tests for admin console endpoints."""

    def test_dashboard_requires_auth(self, client_no_auth):
        """GET /api/console/dashboard requires authentication."""
        response = client_no_auth.get("/api/console/dashboard")
        assert response.status_code == 401

    def test_console_list_jobs_requires_auth(self, client_no_auth):
        """GET /api/console/jobs requires authentication."""
        response = client_no_auth.get("/api/console/jobs")
        assert response.status_code == 401

    def test_console_delete_job_requires_auth(self, client_no_auth):
        """DELETE /api/console/jobs/{job_id} requires authentication."""
        job_id = uuid4()
        response = client_no_auth.delete(f"/api/console/jobs/{job_id}")
        assert response.status_code == 401

    def test_console_update_settings_requires_auth(self, client_no_auth):
        """PATCH /api/console/settings/{namespace} requires authentication."""
        response = client_no_auth.patch(
            "/api/console/settings/rate_limits",
            json={},
        )
        assert response.status_code == 401
