"""Unit tests for the metrics module."""

import os
from unittest.mock import patch


def _reset_metrics_module():
    """Reset the metrics module and clear Prometheus registry.

    This is needed because Prometheus doesn't allow re-registering metrics,
    and importlib.reload() only resets the module state, not the registry.
    """
    import importlib

    from prometheus_client import REGISTRY

    import dalston.metrics

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


class TestMetricsConfiguration:
    """Tests for metrics configuration."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_metrics_disabled_by_default_false(self):
        """Metrics are enabled by default (METRICS_ENABLED not set or true)."""
        import dalston.metrics

        # Should be disabled before configure is called
        assert not dalston.metrics.is_metrics_enabled()

    def test_configure_metrics_enables_when_env_true(self):
        """configure_metrics() enables metrics when METRICS_ENABLED=true."""
        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("test-service")
            assert dalston.metrics.is_metrics_enabled()
            assert dalston.metrics.get_service_name() == "test-service"

    def test_configure_metrics_disabled_when_env_false(self):
        """configure_metrics() disables metrics when METRICS_ENABLED=false."""
        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "false"}):
            dalston.metrics.configure_metrics("test-service")
            assert not dalston.metrics.is_metrics_enabled()


class TestGatewayMetrics:
    """Tests for gateway metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        # Enable metrics for testing
        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")

    def test_inc_gateway_requests(self):
        """Test incrementing gateway requests counter."""
        import dalston.metrics

        # Should not raise
        dalston.metrics.inc_gateway_requests("GET", "/health", 200)
        dalston.metrics.inc_gateway_requests("POST", "/v1/audio/transcriptions", 201)

    def test_observe_gateway_request_duration(self):
        """Test observing gateway request duration."""
        import dalston.metrics

        # Should not raise
        dalston.metrics.observe_gateway_request_duration("GET", "/health", 0.05)
        dalston.metrics.observe_gateway_request_duration(
            "POST", "/v1/audio/transcriptions", 1.5
        )

    def test_inc_gateway_jobs_created(self):
        """Test incrementing jobs created counter."""
        import dalston.metrics

        dalston.metrics.inc_gateway_jobs_created("tenant-123")

    def test_websocket_connections_gauge(self):
        """Test WebSocket connections gauge operations."""
        import dalston.metrics

        dalston.metrics.set_gateway_websocket_connections(5)
        dalston.metrics.inc_gateway_websocket_connections()
        dalston.metrics.dec_gateway_websocket_connections()

    def test_inc_gateway_upload_bytes(self):
        """Test incrementing upload bytes counter."""
        import dalston.metrics

        dalston.metrics.inc_gateway_upload_bytes(1024)
        dalston.metrics.inc_gateway_upload_bytes(2048)


class TestOrchestratorMetrics:
    """Tests for orchestrator metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("orchestrator")

    def test_inc_orchestrator_jobs(self):
        """Test incrementing orchestrator jobs counter."""
        import dalston.metrics

        dalston.metrics.inc_orchestrator_jobs("completed")
        dalston.metrics.inc_orchestrator_jobs("failed")
        dalston.metrics.inc_orchestrator_jobs("cancelled")

    def test_observe_orchestrator_job_duration(self):
        """Test observing job duration."""
        import dalston.metrics

        dalston.metrics.observe_orchestrator_job_duration(5, 120.5)

    def test_inc_orchestrator_tasks_scheduled(self):
        """Test incrementing tasks scheduled counter."""
        import dalston.metrics

        dalston.metrics.inc_orchestrator_tasks_scheduled("faster-whisper", "transcribe")

    def test_inc_orchestrator_tasks_completed(self):
        """Test incrementing tasks completed counter."""
        import dalston.metrics

        dalston.metrics.inc_orchestrator_tasks_completed("faster-whisper", "success")
        dalston.metrics.inc_orchestrator_tasks_completed("faster-whisper", "failure")

    def test_inc_orchestrator_events(self):
        """Test incrementing events counter."""
        import dalston.metrics

        dalston.metrics.inc_orchestrator_events("job.created")
        dalston.metrics.inc_orchestrator_events("task.completed")

    def test_observe_orchestrator_dag_build(self):
        """Test observing DAG build duration."""
        import dalston.metrics

        dalston.metrics.observe_orchestrator_dag_build(0.015)


class TestEngineMetrics:
    """Tests for engine metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("engine-faster-whisper")

    def test_inc_engine_tasks(self):
        """Test incrementing engine tasks counter."""
        import dalston.metrics

        dalston.metrics.inc_engine_tasks("faster-whisper", "success")
        dalston.metrics.inc_engine_tasks("faster-whisper", "failure")

    def test_observe_engine_task_duration(self):
        """Test observing engine task duration."""
        import dalston.metrics

        dalston.metrics.observe_engine_task_duration("faster-whisper", 15.5)

    def test_observe_engine_queue_wait(self):
        """Test observing engine queue wait time."""
        import dalston.metrics

        dalston.metrics.observe_engine_queue_wait("faster-whisper", 2.5)

    def test_observe_engine_s3_download(self):
        """Test observing S3 download time."""
        import dalston.metrics

        dalston.metrics.observe_engine_s3_download("faster-whisper", 0.5)

    def test_observe_engine_s3_upload(self):
        """Test observing S3 upload time."""
        import dalston.metrics

        dalston.metrics.observe_engine_s3_upload("faster-whisper", 0.3)


class TestSessionRouterMetrics:
    """Tests for session router metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        # Note: Session router metrics are initialized when gateway starts
        # For testing, we simulate with gateway config
        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("gateway")
            # Manually init session router metrics for testing
            dalston.metrics._init_session_router_metrics()

    def test_set_session_router_workers_registered(self):
        """Test setting workers registered gauge."""
        import dalston.metrics

        dalston.metrics.set_session_router_workers_registered(3)

    def test_set_session_router_workers_healthy(self):
        """Test setting workers healthy gauge."""
        import dalston.metrics

        dalston.metrics.set_session_router_workers_healthy(2)

    def test_set_session_router_sessions_active(self):
        """Test setting active sessions per worker."""
        import dalston.metrics

        dalston.metrics.set_session_router_sessions_active("worker-1", 2)

    def test_inc_session_router_sessions(self):
        """Test incrementing sessions counter."""
        import dalston.metrics

        dalston.metrics.inc_session_router_sessions("completed")
        dalston.metrics.inc_session_router_sessions("error")

    def test_observe_session_router_allocation(self):
        """Test observing session allocation duration."""
        import dalston.metrics

        dalston.metrics.observe_session_router_allocation(0.025)


class TestRealtimeMetrics:
    """Tests for realtime metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "true"}):
            dalston.metrics.configure_metrics("realtime-whisper-1")

    def test_observe_realtime_session_duration(self):
        """Test observing realtime session duration."""
        import dalston.metrics

        dalston.metrics.observe_realtime_session_duration(120.5)

    def test_inc_realtime_audio_processed(self):
        """Test incrementing audio processed counter."""
        import dalston.metrics

        dalston.metrics.inc_realtime_audio_processed("worker-1", 30.0)

    def test_inc_realtime_transcripts(self):
        """Test incrementing transcripts counter."""
        import dalston.metrics

        dalston.metrics.inc_realtime_transcripts("partial")
        dalston.metrics.inc_realtime_transcripts("final")


class TestQueueMetrics:
    """Tests for queue metrics functions."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

    def test_init_queue_metrics(self):
        """Test initializing queue metrics."""
        import dalston.metrics

        dalston.metrics.init_queue_metrics()
        assert dalston.metrics.is_metrics_enabled()

    def test_set_queue_depth(self):
        """Test setting queue depth."""
        import dalston.metrics

        dalston.metrics.init_queue_metrics()
        dalston.metrics.set_queue_depth("faster-whisper", 5)

    def test_set_queue_oldest_task_age(self):
        """Test setting oldest task age."""
        import dalston.metrics

        dalston.metrics.init_queue_metrics()
        dalston.metrics.set_queue_oldest_task_age("faster-whisper", 30.0)

    def test_set_redis_connected(self):
        """Test setting Redis connectivity."""
        import dalston.metrics

        dalston.metrics.init_queue_metrics()
        dalston.metrics.set_redis_connected(True)
        dalston.metrics.set_redis_connected(False)


class TestMetricsNoOpWhenDisabled:
    """Tests that metric functions are no-ops when disabled."""

    def setup_method(self):
        """Reset metrics module before each test."""
        _reset_metrics_module()

        import dalston.metrics

        with patch.dict(os.environ, {"METRICS_ENABLED": "false"}):
            dalston.metrics.configure_metrics("test-service")

    def test_gateway_metrics_noop_when_disabled(self):
        """Gateway metrics should be no-ops when disabled."""
        import dalston.metrics

        # All these should return without error even though metrics are disabled
        dalston.metrics.inc_gateway_requests("GET", "/health", 200)
        dalston.metrics.observe_gateway_request_duration("GET", "/health", 0.05)
        dalston.metrics.inc_gateway_jobs_created("tenant-123")
        dalston.metrics.set_gateway_websocket_connections(5)
        dalston.metrics.inc_gateway_websocket_connections()
        dalston.metrics.dec_gateway_websocket_connections()
        dalston.metrics.inc_gateway_upload_bytes(1024)

    def test_orchestrator_metrics_noop_when_disabled(self):
        """Orchestrator metrics should be no-ops when disabled."""
        import dalston.metrics

        dalston.metrics.inc_orchestrator_jobs("completed")
        dalston.metrics.observe_orchestrator_job_duration(5, 120.5)
        dalston.metrics.inc_orchestrator_tasks_scheduled("engine", "stage")
        dalston.metrics.inc_orchestrator_tasks_completed("engine", "success")
        dalston.metrics.inc_orchestrator_events("event.type")
        dalston.metrics.observe_orchestrator_dag_build(0.015)

    def test_engine_metrics_noop_when_disabled(self):
        """Engine metrics should be no-ops when disabled."""
        import dalston.metrics

        dalston.metrics.inc_engine_tasks("engine", "success")
        dalston.metrics.observe_engine_task_duration("engine", 15.5)
        dalston.metrics.observe_engine_queue_wait("engine", 2.5)
        dalston.metrics.observe_engine_s3_download("engine", 0.5)
        dalston.metrics.observe_engine_s3_upload("engine", 0.3)

    def test_session_router_metrics_noop_when_disabled(self):
        """Session router metrics should be no-ops when disabled."""
        import dalston.metrics

        dalston.metrics.set_session_router_workers_registered(3)
        dalston.metrics.set_session_router_workers_healthy(2)
        dalston.metrics.set_session_router_sessions_active("worker", 2)
        dalston.metrics.inc_session_router_sessions("completed")
        dalston.metrics.observe_session_router_allocation(0.025)

    def test_realtime_metrics_noop_when_disabled(self):
        """Realtime metrics should be no-ops when disabled."""
        import dalston.metrics

        dalston.metrics.observe_realtime_session_duration(120.5)
        dalston.metrics.inc_realtime_audio_processed("worker", 30.0)
        dalston.metrics.inc_realtime_transcripts("final")
