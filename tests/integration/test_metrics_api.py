"""Integration tests for metrics API endpoints and middleware (M20)."""

import importlib
import os
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import dalston.metrics
from dalston.gateway.middleware.metrics import MetricsMiddleware


def _reset_metrics_module():
    """Reset the metrics module and clear Prometheus registry."""
    from prometheus_client import REGISTRY

    # Clear all dalston metrics from the registry
    collectors_to_remove = []
    for collector in REGISTRY._names_to_collectors.values():
        name = getattr(collector, "_name", "")
        if name.startswith("dalston_"):
            collectors_to_remove.append(collector)

    for collector in collectors_to_remove:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass

    # Reload the module to reset its state
    importlib.reload(dalston.metrics)


class TestMetricsEndpoint:
    """Tests for the /metrics endpoint."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_metrics_endpoint_returns_prometheus_format(self):
        """The /metrics endpoint should return Prometheus text format."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("test-gateway")

            # Create minimal test app with /metrics endpoint
            app = FastAPI()

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)
            response = client.get("/metrics")

            assert response.status_code == 200
            assert "text/plain" in response.headers["content-type"]
            # Should contain standard Prometheus metric lines
            content = response.text
            assert "# HELP" in content or "# TYPE" in content or "dalston_" in content

    def test_metrics_endpoint_disabled(self):
        """When metrics disabled, /metrics should return 404."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "false"}):
            dalston.metrics.configure_metrics("test-gateway")

            app = FastAPI()

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response

                if not dalston.metrics.is_metrics_enabled():
                    return Response(content="Metrics disabled", status_code=404)

                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)
            response = client.get("/metrics")

            assert response.status_code == 404
            assert response.text == "Metrics disabled"


class TestMetricsMiddlewareIntegration:
    """Tests for MetricsMiddleware integration with FastAPI."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_middleware_records_request_metrics(self):
        """MetricsMiddleware should record request count and duration."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            app = FastAPI()
            app.add_middleware(MetricsMiddleware)

            @app.get("/test")
            async def test_endpoint():
                return {"status": "ok"}

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)

            # Make some requests
            for _ in range(3):
                response = client.get("/test")
                assert response.status_code == 200

            # Check metrics
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # Should have recorded request counts
            assert "dalston_gateway_requests_total" in content
            # Should have recorded request duration
            assert "dalston_gateway_request_duration_seconds" in content

    def test_middleware_normalizes_uuid_paths(self):
        """Middleware should normalize UUIDs in paths to prevent cardinality explosion."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            app = FastAPI()
            app.add_middleware(MetricsMiddleware)

            @app.get("/v1/jobs/{job_id}")
            async def get_job(job_id: str):
                return {"job_id": job_id}

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)

            # Make requests with different UUIDs
            client.get("/v1/jobs/12345678-1234-1234-1234-123456789abc")
            client.get("/v1/jobs/87654321-4321-4321-4321-cba987654321")

            # Check metrics - UUIDs should be normalized
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # Should have normalized path, not individual UUIDs
            assert "{uuid}" in content or "/v1/jobs" in content
            # Should NOT have the actual UUID values as separate labels
            assert "12345678-1234-1234-1234-123456789abc" not in content

    def test_middleware_records_error_status_codes(self):
        """Middleware should record error responses correctly."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            app = FastAPI()
            app.add_middleware(MetricsMiddleware)

            @app.get("/success")
            async def success_endpoint():
                return {"status": "ok"}

            @app.get("/error")
            async def error_endpoint():
                return JSONResponse(
                    status_code=500, content={"error": "Internal error"}
                )

            @app.get("/not-found")
            async def not_found_endpoint():
                return JSONResponse(status_code=404, content={"error": "Not found"})

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)

            # Make requests with different status codes
            client.get("/success")  # 200
            client.get("/error")  # 500
            client.get("/not-found")  # 404

            # Check metrics
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # Should have recorded different status codes
            assert 'status_code="200"' in content
            assert 'status_code="500"' in content
            assert 'status_code="404"' in content

    def test_middleware_skips_excluded_endpoints(self):
        """Middleware should not record metrics for excluded endpoints."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            app = FastAPI()
            app.add_middleware(MetricsMiddleware)

            @app.get("/health")
            async def health_endpoint():
                return {"status": "healthy"}

            @app.get("/v1/jobs")
            async def jobs_endpoint():
                return {"jobs": []}

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)

            # Make requests to endpoints
            client.get("/health")  # Excluded
            client.get("/v1/jobs")  # Not excluded - should be recorded
            client.get("/metrics")  # Excluded
            client.get("/metrics")  # Excluded

            # Check metrics
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # /v1/jobs should be recorded
            assert 'endpoint="/v1/jobs"' in content
            # /health and /metrics are excluded
            assert 'endpoint="/health"' not in content
            assert 'endpoint="/metrics"' not in content


class TestMetricsWithOtherComponents:
    """Tests for metrics integration with other system components."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_gateway_metrics_functions_with_middleware(self):
        """Gateway metric functions should work alongside middleware."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            app = FastAPI()
            app.add_middleware(MetricsMiddleware)

            @app.post("/v1/audio/transcriptions")
            async def create_transcription(request: Request):
                # Simulate what the real endpoint does
                dalston.metrics.inc_gateway_jobs_created("test-tenant")
                dalston.metrics.inc_gateway_upload_bytes(1024)
                return {"job_id": "test-job-id"}

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)

            # Create a job
            response = client.post("/v1/audio/transcriptions")
            assert response.status_code == 200

            # Check metrics
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # Should have both middleware metrics and custom metrics
            assert "dalston_gateway_requests_total" in content
            assert "dalston_gateway_jobs_created_total" in content
            assert "dalston_gateway_upload_bytes_total" in content

    def test_websocket_connection_gauges(self):
        """WebSocket connection gauge operations should work correctly."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

            # Simulate connection lifecycle
            dalston.metrics.set_gateway_websocket_connections(0)
            dalston.metrics.inc_gateway_websocket_connections()
            dalston.metrics.inc_gateway_websocket_connections()
            dalston.metrics.dec_gateway_websocket_connections()

            # Create app to get metrics
            app = FastAPI()

            @app.get("/metrics")
            async def metrics_endpoint():
                from fastapi.responses import Response
                from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

                return Response(
                    content=generate_latest(), media_type=CONTENT_TYPE_LATEST
                )

            client = TestClient(app)
            metrics_response = client.get("/metrics")
            content = metrics_response.text

            # Should have WebSocket connections gauge
            assert "dalston_gateway_websocket_connections" in content


class TestServiceMetricsConfiguration:
    """Tests for metrics configuration across different services."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_service_name_recorded(self):
        """Service name should be recorded in configuration."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("test-service")
            assert dalston.metrics.get_service_name() == "test-service"

    def test_configure_multiple_times_idempotent(self):
        """Calling configure_metrics multiple times should be safe."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            # Configure multiple times
            dalston.metrics.configure_metrics("service-1")
            dalston.metrics.configure_metrics("service-2")

            # Should still work without errors
            dalston.metrics.inc_gateway_requests("GET", "/test", 200)

    def test_orchestrator_metrics_isolation(self):
        """Orchestrator metrics should work independently."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("orchestrator")

            # These should all work without errors
            dalston.metrics.inc_orchestrator_jobs("completed")
            dalston.metrics.inc_orchestrator_jobs("failed")
            dalston.metrics.observe_orchestrator_job_duration(5, 120.5)
            dalston.metrics.inc_orchestrator_tasks_scheduled("whisper", "transcribe")
            dalston.metrics.inc_orchestrator_tasks_completed("whisper", "success")
            dalston.metrics.inc_orchestrator_events("job.created")
            dalston.metrics.observe_orchestrator_dag_build(0.025)

    def test_engine_metrics_isolation(self):
        """Engine metrics should work independently."""
        with patch.dict(os.environ, {"DALSTON_METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("stt-batch-transcribe-whisper")

            # These should all work without errors
            dalston.metrics.inc_engine_tasks("faster-whisper", "success")
            dalston.metrics.observe_engine_task_duration("faster-whisper", 15.5)
            dalston.metrics.observe_engine_queue_wait("faster-whisper", 2.5)
            dalston.metrics.observe_engine_s3_download("faster-whisper", 0.5)
            dalston.metrics.observe_engine_s3_upload("faster-whisper", 0.3)
