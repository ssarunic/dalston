"""Tests for error handler middleware."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dalston.gateway.middleware.error_handler import setup_exception_handlers


@pytest.fixture
def app():
    """Create a test FastAPI app with error handlers."""
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/rate-limited")
    async def rate_limited_endpoint():
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "0",
            },
        )

    @app.get("/not-found")
    async def not_found_endpoint():
        raise HTTPException(status_code=404, detail="Resource not found")

    @app.get("/custom-header")
    async def custom_header_endpoint():
        raise HTTPException(
            status_code=400,
            detail="Bad request",
            headers={"X-Custom-Header": "custom-value"},
        )

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestErrorHandlerHeaders:
    """Tests for HTTP exception header passthrough."""

    def test_rate_limit_headers_passed_through(self, client):
        """Rate limit headers should be included in 429 response."""
        response = client.get("/rate-limited")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        assert response.headers["X-RateLimit-Limit"] == "100"
        assert response.headers["X-RateLimit-Remaining"] == "0"

    def test_error_body_format(self, client):
        """Error response body should follow standard format."""
        response = client.get("/rate-limited")

        assert response.json() == {
            "error": {
                "code": "rate_limit_exceeded",
                "message": "Rate limit exceeded",
            }
        }

    def test_custom_headers_passed_through(self, client):
        """Custom headers should be included in error response."""
        response = client.get("/custom-header")

        assert response.status_code == 400
        assert response.headers["X-Custom-Header"] == "custom-value"

    def test_no_headers_when_none_provided(self, client):
        """Response should work when no custom headers provided."""
        response = client.get("/not-found")

        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "not_found",
                "message": "Resource not found",
            }
        }
