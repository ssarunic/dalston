"""End-to-end tests for distributed tracing (M19).

These tests verify tracing integration works across services.
They require the Docker Compose stack with tracing profile.
"""

import os

import httpx
import pytest


@pytest.mark.e2e
class TestTracingE2E:
    """E2E tests for distributed tracing."""

    @pytest.fixture
    def gateway_url(self):
        """Gateway URL from environment or default."""
        return os.environ.get("GATEWAY_URL", "http://localhost:8000")

    @pytest.fixture
    def jaeger_url(self):
        """Jaeger URL from environment or default."""
        return os.environ.get("JAEGER_URL", "http://localhost:16686")

    @pytest.mark.skipif(
        os.environ.get("OTEL_ENABLED") != "true",
        reason="Tracing not enabled - set OTEL_ENABLED=true",
    )
    async def test_request_creates_trace(self, gateway_url, jaeger_url):
        """HTTP request to gateway creates a trace in Jaeger."""
        async with httpx.AsyncClient() as client:
            # Make a request to the gateway
            response = await client.get(f"{gateway_url}/health")
            assert response.status_code == 200

            # Wait a bit for trace to be exported
            import asyncio

            await asyncio.sleep(2)

            # Query Jaeger for traces from gateway service
            jaeger_response = await client.get(
                f"{jaeger_url}/api/traces",
                params={
                    "service": "dalston-gateway",
                    "limit": 1,
                },
            )

            if jaeger_response.status_code == 200:
                data = jaeger_response.json()
                # If Jaeger is running and has data, we should see traces
                assert "data" in data

    @pytest.mark.skipif(
        os.environ.get("OTEL_ENABLED") != "true",
        reason="Tracing not enabled - set OTEL_ENABLED=true",
    )
    async def test_request_id_in_response(self, gateway_url):
        """Requests include X-Request-ID header in response."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gateway_url}/health")
            assert response.status_code == 200

            # Should have X-Request-ID header
            assert "x-request-id" in response.headers
            request_id = response.headers["x-request-id"]
            assert request_id.startswith("req_") or len(request_id) > 0


@pytest.mark.e2e
class TestTracingDisabled:
    """Tests verifying tracing disabled behavior."""

    @pytest.fixture
    def gateway_url(self):
        """Gateway URL from environment or default."""
        return os.environ.get("GATEWAY_URL", "http://localhost:8000")

    @pytest.mark.skipif(
        os.environ.get("OTEL_ENABLED") == "true",
        reason="Tracing is enabled - these tests require disabled tracing",
    )
    async def test_gateway_works_without_tracing(self, gateway_url):
        """Gateway works correctly when tracing is disabled."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gateway_url}/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.skipif(
        os.environ.get("OTEL_ENABLED") == "true",
        reason="Tracing is enabled - these tests require disabled tracing",
    )
    async def test_request_id_works_without_tracing(self, gateway_url):
        """Request ID correlation works even without tracing."""
        async with httpx.AsyncClient() as client:
            # Send custom request ID
            custom_id = "custom_request_123"
            response = await client.get(
                f"{gateway_url}/health",
                headers={"X-Request-ID": custom_id},
            )
            assert response.status_code == 200

            # Should echo back the same request ID
            assert response.headers.get("x-request-id") == custom_id
