"""Unit tests for the correlation ID middleware."""

import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from dalston.gateway.middleware.correlation import (
    REQUEST_ID_HEADER,
    CorrelationIdMiddleware,
    _generate_request_id,
    _sanitize_request_id,
)


class TestGenerateRequestId:
    """Tests for the _generate_request_id helper."""

    def test_has_req_prefix(self):
        rid = _generate_request_id()
        assert rid.startswith("req_")

    def test_is_unique(self):
        ids = {_generate_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_length(self):
        rid = _generate_request_id()
        # "req_" (4) + 32 hex chars = 36
        assert len(rid) == 36


class TestCorrelationIdMiddleware:
    """Tests for CorrelationIdMiddleware."""

    @pytest.fixture
    def captured_context(self):
        """Fixture that provides a mutable dict to capture contextvars in handlers."""
        return {}

    @pytest.fixture
    def app(self, captured_context):
        """Create a test Starlette app with the middleware."""
        app = Starlette()

        @app.route("/test")
        async def test_endpoint(request: Request):
            # Capture the structlog contextvars at the time of the request
            ctx = structlog.contextvars.get_contextvars()
            captured_context.update(ctx)
            # Also capture request.state.request_id
            captured_context["state_request_id"] = getattr(
                request.state, "request_id", None
            )
            return PlainTextResponse("ok")

        app.add_middleware(CorrelationIdMiddleware)
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_generates_request_id_when_none_provided(self, client):
        """Generates a req_ ID when no X-Request-ID header is sent."""
        response = client.get("/test")
        assert response.status_code == 200
        rid = response.headers[REQUEST_ID_HEADER]
        assert rid.startswith("req_")

    def test_uses_client_provided_request_id(self, client):
        """Uses the client's X-Request-ID if provided."""
        response = client.get("/test", headers={REQUEST_ID_HEADER: "client-trace-123"})
        assert response.status_code == 200
        assert response.headers[REQUEST_ID_HEADER] == "client-trace-123"

    def test_sets_request_id_on_request_state(self, client, captured_context):
        """request.state.request_id is set for route handlers."""
        client.get("/test", headers={REQUEST_ID_HEADER: "my-trace"})
        assert captured_context["state_request_id"] == "my-trace"

    def test_binds_request_id_to_structlog_contextvars(self, client, captured_context):
        """request_id is bound into structlog contextvars during request."""
        client.get("/test", headers={REQUEST_ID_HEADER: "trace-456"})
        assert captured_context.get("request_id") == "trace-456"

    def test_response_header_matches_generated_id(self, client, captured_context):
        """Generated request_id matches what's in the response header and context."""
        response = client.get("/test")
        rid = response.headers[REQUEST_ID_HEADER]
        assert captured_context.get("request_id") == rid
        assert captured_context["state_request_id"] == rid

    def test_clears_previous_contextvars(self, client, captured_context):
        """Middleware clears contextvars before binding, isolating requests."""
        # Bind some stale context
        structlog.contextvars.bind_contextvars(stale_key="should_be_gone")

        client.get("/test")

        # The stale_key should NOT be present in the handler's context
        assert "stale_key" not in captured_context

    def test_different_requests_get_different_ids(self, client):
        """Each request gets a unique request_id."""
        r1 = client.get("/test")
        r2 = client.get("/test")
        assert r1.headers[REQUEST_ID_HEADER] != r2.headers[REQUEST_ID_HEADER]

    def test_rejects_invalid_request_id(self, client):
        """Invalid X-Request-ID is replaced with a generated one."""
        response = client.get(
            "/test", headers={REQUEST_ID_HEADER: "has spaces & <script>"}
        )
        assert response.status_code == 200
        rid = response.headers[REQUEST_ID_HEADER]
        assert rid.startswith("req_")

    def test_rejects_oversized_request_id(self, client):
        """Oversized X-Request-ID is replaced with a generated one."""
        response = client.get("/test", headers={REQUEST_ID_HEADER: "a" * 200})
        rid = response.headers[REQUEST_ID_HEADER]
        assert rid.startswith("req_")


class TestSanitizeRequestId:
    """Tests for _sanitize_request_id validation."""

    def test_accepts_valid_alphanumeric(self):
        assert _sanitize_request_id("abc123") == "abc123"

    def test_accepts_dashes_underscores_dots(self):
        assert _sanitize_request_id("req_abc-123.456") == "req_abc-123.456"

    def test_rejects_none(self):
        result = _sanitize_request_id(None)
        assert result.startswith("req_")

    def test_rejects_empty_string(self):
        result = _sanitize_request_id("")
        assert result.startswith("req_")

    def test_rejects_spaces(self):
        result = _sanitize_request_id("has space")
        assert result.startswith("req_")

    def test_rejects_special_chars(self):
        result = _sanitize_request_id("<script>alert(1)</script>")
        assert result.startswith("req_")

    def test_rejects_over_128_chars(self):
        result = _sanitize_request_id("a" * 129)
        assert result.startswith("req_")

    def test_accepts_exactly_128_chars(self):
        value = "a" * 128
        assert _sanitize_request_id(value) == value
