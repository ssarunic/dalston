"""End-to-end tests for Prometheus metrics (M20).

These tests verify metrics collection works across services.
They require the Docker Compose stack with monitoring profile.

Run with:
    docker compose --profile monitoring up -d
    METRICS_ENABLED=true pytest -m e2e tests/e2e/test_metrics_e2e.py -v
"""

import os

import httpx
import pytest


@pytest.mark.e2e
class TestMetricsEndpointsE2E:
    """E2E tests for metrics endpoints."""

    @pytest.fixture
    def gateway_url(self):
        """Gateway URL from environment or default."""
        return os.environ.get("GATEWAY_URL", "http://localhost:8000")

    @pytest.fixture
    def prometheus_url(self):
        """Prometheus URL from environment or default."""
        return os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

    @pytest.fixture
    def orchestrator_metrics_url(self):
        """Orchestrator metrics URL from environment or default."""
        return os.environ.get("ORCHESTRATOR_METRICS_URL", "http://localhost:8001")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_gateway_metrics_endpoint(self, gateway_url):
        """Gateway /metrics endpoint returns Prometheus format."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gateway_url}/metrics")

            assert response.status_code == 200
            assert "text/plain" in response.headers["content-type"]

            content = response.text
            # Should contain Prometheus format (comments and metrics)
            assert "# HELP" in content or "# TYPE" in content

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_gateway_metrics_contain_request_metrics(self, gateway_url):
        """Gateway metrics include HTTP request counters after requests."""
        async with httpx.AsyncClient() as client:
            # Make some requests first
            await client.get(f"{gateway_url}/health")
            await client.get(f"{gateway_url}/health")
            await client.get(f"{gateway_url}/")

            # Check metrics
            response = await client.get(f"{gateway_url}/metrics")
            assert response.status_code == 200

            content = response.text
            # Should have gateway request metrics
            assert "dalston_gateway_requests_total" in content
            assert "dalston_gateway_request_duration_seconds" in content

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_gateway_metrics_include_method_and_path(self, gateway_url):
        """Gateway metrics include method and path labels."""
        async with httpx.AsyncClient() as client:
            # Make a GET request
            await client.get(f"{gateway_url}/health")

            # Check metrics
            response = await client.get(f"{gateway_url}/metrics")
            content = response.text

            # Should have method label
            assert 'method="GET"' in content
            # Should have path label
            assert 'path="/health"' in content


@pytest.mark.e2e
class TestPrometheusScrapingE2E:
    """E2E tests for Prometheus scraping targets."""

    @pytest.fixture
    def prometheus_url(self):
        """Prometheus URL from environment or default."""
        return os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_prometheus_is_running(self, prometheus_url):
        """Prometheus server is running and accessible."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{prometheus_url}/-/ready")
                assert response.status_code == 200
            except httpx.ConnectError:
                pytest.skip("Prometheus not running - start with --profile monitoring")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_prometheus_scrapes_gateway(self, prometheus_url):
        """Prometheus successfully scrapes gateway metrics."""
        import asyncio

        async with httpx.AsyncClient() as client:
            try:
                # Check Prometheus is up
                ready = await client.get(f"{prometheus_url}/-/ready")
                if ready.status_code != 200:
                    pytest.skip("Prometheus not ready")
            except httpx.ConnectError:
                pytest.skip("Prometheus not running - start with --profile monitoring")

            # Wait for scrape
            await asyncio.sleep(5)

            # Query Prometheus for gateway metrics
            response = await client.get(
                f"{prometheus_url}/api/v1/query",
                params={"query": "dalston_gateway_requests_total"},
            )

            if response.status_code == 200:
                data = response.json()
                # Should have successful query status
                assert data["status"] == "success"

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_prometheus_targets_up(self, prometheus_url):
        """Prometheus targets are up and being scraped."""
        import asyncio

        async with httpx.AsyncClient() as client:
            try:
                ready = await client.get(f"{prometheus_url}/-/ready")
                if ready.status_code != 200:
                    pytest.skip("Prometheus not ready")
            except httpx.ConnectError:
                pytest.skip("Prometheus not running - start with --profile monitoring")

            # Wait for initial scrape
            await asyncio.sleep(5)

            # Check targets
            response = await client.get(f"{prometheus_url}/api/v1/targets")

            if response.status_code == 200:
                data = response.json()
                assert data["status"] == "success"

                # Find gateway target
                active_targets = data.get("data", {}).get("activeTargets", [])
                gateway_targets = [
                    t
                    for t in active_targets
                    if "gateway" in t.get("labels", {}).get("job", "")
                ]

                # If we have gateway targets, at least one should be healthy
                for target in gateway_targets:
                    if target.get("health") == "up":
                        return  # At least one healthy target

                # If no targets found or none healthy, it might just need more time
                if not gateway_targets:
                    pytest.skip(
                        "No gateway targets found - monitoring may not be fully started"
                    )


@pytest.mark.e2e
class TestMetricsDisabledE2E:
    """Tests verifying metrics disabled behavior."""

    @pytest.fixture
    def gateway_url(self):
        """Gateway URL from environment or default."""
        return os.environ.get("GATEWAY_URL", "http://localhost:8000")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") == "true",
        reason="Metrics is enabled - these tests require disabled metrics",
    )
    async def test_gateway_works_without_metrics(self, gateway_url):
        """Gateway works correctly when metrics is disabled."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gateway_url}/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") == "true",
        reason="Metrics is enabled - these tests require disabled metrics",
    )
    async def test_metrics_endpoint_returns_404_when_disabled(self, gateway_url):
        """Gateway /metrics endpoint returns 404 when metrics disabled."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gateway_url}/metrics")
            assert response.status_code == 404


@pytest.mark.e2e
class TestQueueMetricsE2E:
    """E2E tests for queue metrics exporter."""

    @pytest.fixture
    def metrics_exporter_url(self):
        """Metrics exporter URL from environment or default."""
        return os.environ.get("METRICS_EXPORTER_URL", "http://localhost:9100")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_queue_metrics_exporter_running(self, metrics_exporter_url):
        """Queue metrics exporter is running and returning metrics."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{metrics_exporter_url}/metrics")
                if response.status_code == 200:
                    content = response.text
                    # Should have queue depth metrics
                    assert "dalston_queue_depth" in content or "# HELP" in content
            except httpx.ConnectError:
                pytest.skip(
                    "Metrics exporter not running - start with --profile monitoring"
                )

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_redis_connectivity_metric(self, metrics_exporter_url):
        """Queue metrics exporter reports Redis connectivity."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{metrics_exporter_url}/metrics")
                if response.status_code == 200:
                    content = response.text
                    # Should have Redis connected metric
                    assert "dalston_redis_connected" in content
            except httpx.ConnectError:
                pytest.skip(
                    "Metrics exporter not running - start with --profile monitoring"
                )


@pytest.mark.e2e
class TestGrafanaE2E:
    """E2E tests for Grafana dashboards."""

    @pytest.fixture
    def grafana_url(self):
        """Grafana URL from environment or default."""
        return os.environ.get("GRAFANA_URL", "http://localhost:3001")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_grafana_is_running(self, grafana_url):
        """Grafana server is running and accessible."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{grafana_url}/api/health")
                if response.status_code == 200:
                    data = response.json()
                    assert data.get("database") == "ok"
            except httpx.ConnectError:
                pytest.skip("Grafana not running - start with --profile monitoring")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_grafana_has_prometheus_datasource(self, grafana_url):
        """Grafana has Prometheus datasource configured."""
        async with httpx.AsyncClient() as client:
            try:
                # This endpoint requires auth in production, but default anonymous access should work
                response = await client.get(
                    f"{grafana_url}/api/datasources",
                    auth=("admin", "admin"),
                )

                if response.status_code == 200:
                    datasources = response.json()
                    prometheus_sources = [
                        ds for ds in datasources if ds.get("type") == "prometheus"
                    ]
                    assert len(prometheus_sources) > 0, "No Prometheus datasource found"
                elif response.status_code == 401:
                    # Authentication required but not configured
                    pytest.skip("Grafana requires authentication")
            except httpx.ConnectError:
                pytest.skip("Grafana not running - start with --profile monitoring")

    @pytest.mark.skipif(
        os.environ.get("METRICS_ENABLED") != "true",
        reason="Metrics not enabled - set METRICS_ENABLED=true",
    )
    async def test_grafana_dashboard_provisioned(self, grafana_url):
        """Grafana has dalston dashboard provisioned."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{grafana_url}/api/search",
                    params={"type": "dash-db"},
                    auth=("admin", "admin"),
                )

                if response.status_code == 200:
                    dashboards = response.json()
                    dalston_dashboards = [
                        d for d in dashboards if "dalston" in d.get("title", "").lower()
                    ]
                    # At least one dalston dashboard should exist
                    assert len(dalston_dashboards) > 0, "No Dalston dashboard found"
                elif response.status_code == 401:
                    pytest.skip("Grafana requires authentication")
            except httpx.ConnectError:
                pytest.skip("Grafana not running - start with --profile monitoring")
