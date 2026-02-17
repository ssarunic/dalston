"""Prometheus metrics configuration for all Dalston services.

Provides a unified `configure_metrics()` function that sets up Prometheus
metrics collection for any Dalston service.

Environment Variables:
    METRICS_ENABLED: Enable/disable metrics collection (default: true)
    METRICS_PORT: Port for metrics endpoint when running standalone exporter

Metric Naming Convention:
    dalston_{service}_{metric_name}_{unit}

Common Labels:
    service: Service name (gateway, orchestrator, engine-*, etc.)
    instance: Instance identifier
"""

from __future__ import annotations

import os
from typing import Any

# Global state
_metrics_enabled: bool = False
_service_name: str = ""
_metrics_initialized: bool = False

# Metric registries by service
_gateway_metrics: dict[str, Any] = {}
_orchestrator_metrics: dict[str, Any] = {}
_engine_metrics: dict[str, Any] = {}
_session_router_metrics: dict[str, Any] = {}
_realtime_metrics: dict[str, Any] = {}
_queue_metrics: dict[str, Any] = {}


def is_metrics_enabled() -> bool:
    """Check if metrics collection is enabled."""
    return _metrics_enabled


def get_service_name() -> str:
    """Get the configured service name."""
    return _service_name


def configure_metrics(service_name: str) -> None:
    """Configure Prometheus metrics for a Dalston service.

    Initializes the Prometheus client and creates service-specific metrics.
    When disabled, all metric operations become no-ops with zero overhead.

    Args:
        service_name: Identifier for this service (e.g. "gateway",
            "orchestrator", "stt-batch-transcribe-whisper").

    Environment Variables:
        METRICS_ENABLED: Set to "false" to disable metrics (default: "true")
    """
    global _metrics_enabled, _service_name, _metrics_initialized

    enabled = os.environ.get("METRICS_ENABLED", "true").lower() == "true"
    _metrics_enabled = enabled
    _service_name = service_name

    if not enabled:
        return

    if _metrics_initialized:
        return

    _metrics_initialized = True

    # Initialize metrics based on service type
    if service_name == "gateway":
        _init_gateway_metrics()
    elif service_name == "orchestrator":
        _init_orchestrator_metrics()
    elif service_name.startswith("engine-"):
        _init_engine_metrics()
    elif service_name == "session-router":
        _init_session_router_metrics()
    elif service_name.startswith("realtime-"):
        _init_realtime_metrics()


def _init_gateway_metrics() -> None:
    """Initialize Gateway-specific metrics."""
    from prometheus_client import Counter, Gauge, Histogram

    _gateway_metrics["requests_total"] = Counter(
        "dalston_gateway_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status_code"],
    )

    _gateway_metrics["request_duration_seconds"] = Histogram(
        "dalston_gateway_request_duration_seconds",
        "Request latency in seconds",
        ["method", "endpoint"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )

    _gateway_metrics["jobs_created_total"] = Counter(
        "dalston_gateway_jobs_created_total",
        "Total jobs submitted",
        ["tenant_id"],
    )

    _gateway_metrics["websocket_connections_active"] = Gauge(
        "dalston_gateway_websocket_connections_active",
        "Active WebSocket connections",
    )

    _gateway_metrics["upload_bytes_total"] = Counter(
        "dalston_gateway_upload_bytes_total",
        "Total bytes uploaded",
    )


def _init_orchestrator_metrics() -> None:
    """Initialize Orchestrator-specific metrics."""
    from prometheus_client import Counter, Histogram

    _orchestrator_metrics["jobs_total"] = Counter(
        "dalston_orchestrator_jobs_total",
        "Jobs by final status",
        ["status"],
    )

    _orchestrator_metrics["job_duration_seconds"] = Histogram(
        "dalston_orchestrator_job_duration_seconds",
        "Total job duration from creation to completion",
        ["stage_count"],
        buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
    )

    _orchestrator_metrics["tasks_scheduled_total"] = Counter(
        "dalston_orchestrator_tasks_scheduled_total",
        "Tasks pushed to queues",
        ["engine_id", "stage"],
    )

    _orchestrator_metrics["tasks_completed_total"] = Counter(
        "dalston_orchestrator_tasks_completed_total",
        "Task completions",
        ["engine_id", "status"],
    )

    _orchestrator_metrics["events_processed_total"] = Counter(
        "dalston_orchestrator_events_processed_total",
        "Redis events processed",
        ["event_type"],
    )

    _orchestrator_metrics["dag_build_duration_seconds"] = Histogram(
        "dalston_orchestrator_dag_build_duration_seconds",
        "DAG construction time",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
    )


def _init_engine_metrics() -> None:
    """Initialize Engine-specific metrics."""
    from prometheus_client import Counter, Histogram

    _engine_metrics["tasks_processed_total"] = Counter(
        "dalston_engine_tasks_processed_total",
        "Tasks processed",
        ["engine_id", "status"],
    )

    _engine_metrics["task_duration_seconds"] = Histogram(
        "dalston_engine_task_duration_seconds",
        "Task processing time (excludes queue wait)",
        ["engine_id"],
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    )

    _engine_metrics["queue_wait_seconds"] = Histogram(
        "dalston_engine_queue_wait_seconds",
        "Time between task enqueue and dequeue",
        ["engine_id"],
        buckets=(0.01, 0.1, 0.5, 1, 5, 10, 30, 60, 300),
    )

    _engine_metrics["s3_download_seconds"] = Histogram(
        "dalston_engine_s3_download_seconds",
        "Input download time",
        ["engine_id"],
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10),
    )

    _engine_metrics["s3_upload_seconds"] = Histogram(
        "dalston_engine_s3_upload_seconds",
        "Output upload time",
        ["engine_id"],
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10),
    )


def _init_session_router_metrics() -> None:
    """Initialize Session Router-specific metrics."""
    from prometheus_client import Counter, Gauge, Histogram

    _session_router_metrics["workers_registered"] = Gauge(
        "dalston_session_router_workers_registered",
        "Workers in the pool",
    )

    _session_router_metrics["workers_healthy"] = Gauge(
        "dalston_session_router_workers_healthy",
        "Workers passing health checks",
    )

    _session_router_metrics["sessions_active"] = Gauge(
        "dalston_session_router_sessions_active",
        "Active sessions per worker",
        ["worker_id"],
    )

    _session_router_metrics["sessions_total"] = Counter(
        "dalston_session_router_sessions_total",
        "Sessions by outcome",
        ["status"],
    )

    _session_router_metrics["allocation_duration_seconds"] = Histogram(
        "dalston_session_router_allocation_duration_seconds",
        "Session allocation latency",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
    )


def _init_realtime_metrics() -> None:
    """Initialize Realtime SDK-specific metrics."""
    from prometheus_client import Counter, Histogram

    _realtime_metrics["session_duration_seconds"] = Histogram(
        "dalston_realtime_session_duration_seconds",
        "Total session duration",
        buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
    )

    _realtime_metrics["audio_processed_seconds"] = Counter(
        "dalston_realtime_audio_processed_seconds",
        "Cumulative audio processed",
        ["worker_id"],
    )

    _realtime_metrics["transcripts_total"] = Counter(
        "dalston_realtime_transcripts_total",
        "Transcripts emitted",
        ["type"],
    )


def _init_queue_metrics() -> None:
    """Initialize Queue Exporter metrics."""
    from prometheus_client import Gauge

    _queue_metrics["queue_depth"] = Gauge(
        "dalston_queue_depth",
        "Tasks waiting in each engine queue",
        ["engine_id"],
    )

    _queue_metrics["queue_oldest_task_age_seconds"] = Gauge(
        "dalston_queue_oldest_task_age_seconds",
        "Age of oldest task in queue",
        ["engine_id"],
    )

    _queue_metrics["redis_connected"] = Gauge(
        "dalston_redis_connected",
        "Redis connectivity (1 = connected, 0 = disconnected)",
    )


# =============================================================================
# Gateway Metrics
# =============================================================================


def inc_gateway_requests(method: str, endpoint: str, status_code: int) -> None:
    """Increment the gateway requests counter.

    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: Request endpoint path
        status_code: HTTP response status code
    """
    if not _metrics_enabled or "requests_total" not in _gateway_metrics:
        return
    _gateway_metrics["requests_total"].labels(
        method=method, endpoint=endpoint, status_code=str(status_code)
    ).inc()


def observe_gateway_request_duration(
    method: str, endpoint: str, duration: float
) -> None:
    """Record gateway request duration.

    Args:
        method: HTTP method
        endpoint: Request endpoint path
        duration: Duration in seconds
    """
    if not _metrics_enabled or "request_duration_seconds" not in _gateway_metrics:
        return
    _gateway_metrics["request_duration_seconds"].labels(
        method=method, endpoint=endpoint
    ).observe(duration)


def inc_gateway_jobs_created(tenant_id: str) -> None:
    """Increment the jobs created counter.

    Args:
        tenant_id: Tenant identifier
    """
    if not _metrics_enabled or "jobs_created_total" not in _gateway_metrics:
        return
    _gateway_metrics["jobs_created_total"].labels(tenant_id=tenant_id).inc()


def set_gateway_websocket_connections(count: int) -> None:
    """Set the active WebSocket connections gauge.

    Args:
        count: Current connection count
    """
    if not _metrics_enabled or "websocket_connections_active" not in _gateway_metrics:
        return
    _gateway_metrics["websocket_connections_active"].set(count)


def inc_gateway_websocket_connections() -> None:
    """Increment active WebSocket connections."""
    if not _metrics_enabled or "websocket_connections_active" not in _gateway_metrics:
        return
    _gateway_metrics["websocket_connections_active"].inc()


def dec_gateway_websocket_connections() -> None:
    """Decrement active WebSocket connections."""
    if not _metrics_enabled or "websocket_connections_active" not in _gateway_metrics:
        return
    _gateway_metrics["websocket_connections_active"].dec()


def inc_gateway_upload_bytes(bytes_count: int) -> None:
    """Increment upload bytes counter.

    Args:
        bytes_count: Number of bytes uploaded
    """
    if not _metrics_enabled or "upload_bytes_total" not in _gateway_metrics:
        return
    _gateway_metrics["upload_bytes_total"].inc(bytes_count)


# =============================================================================
# Orchestrator Metrics
# =============================================================================


def inc_orchestrator_jobs(status: str) -> None:
    """Increment jobs counter by status.

    Args:
        status: Job status (completed, failed, cancelled)
    """
    if not _metrics_enabled or "jobs_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["jobs_total"].labels(status=status).inc()


def observe_orchestrator_job_duration(stage_count: int, duration: float) -> None:
    """Record job duration.

    Args:
        stage_count: Number of stages in the job
        duration: Duration in seconds
    """
    if not _metrics_enabled or "job_duration_seconds" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["job_duration_seconds"].labels(
        stage_count=str(stage_count)
    ).observe(duration)


def inc_orchestrator_tasks_scheduled(engine_id: str, stage: str) -> None:
    """Increment tasks scheduled counter.

    Args:
        engine_id: Engine identifier
        stage: Pipeline stage name
    """
    if not _metrics_enabled or "tasks_scheduled_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["tasks_scheduled_total"].labels(
        engine_id=engine_id, stage=stage
    ).inc()


def inc_orchestrator_tasks_completed(engine_id: str, status: str) -> None:
    """Increment tasks completed counter.

    Args:
        engine_id: Engine identifier
        status: Task status (success, failure)
    """
    if not _metrics_enabled or "tasks_completed_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["tasks_completed_total"].labels(
        engine_id=engine_id, status=status
    ).inc()


def inc_orchestrator_events(event_type: str) -> None:
    """Increment events processed counter.

    Args:
        event_type: Event type
    """
    if not _metrics_enabled or "events_processed_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["events_processed_total"].labels(event_type=event_type).inc()


def observe_orchestrator_dag_build(duration: float) -> None:
    """Record DAG build duration.

    Args:
        duration: Duration in seconds
    """
    if (
        not _metrics_enabled
        or "dag_build_duration_seconds" not in _orchestrator_metrics
    ):
        return
    _orchestrator_metrics["dag_build_duration_seconds"].observe(duration)


# =============================================================================
# Engine Metrics
# =============================================================================


def inc_engine_tasks(engine_id: str, status: str) -> None:
    """Increment engine tasks processed counter.

    Args:
        engine_id: Engine identifier
        status: Task status (success, failure)
    """
    if not _metrics_enabled or "tasks_processed_total" not in _engine_metrics:
        return
    _engine_metrics["tasks_processed_total"].labels(
        engine_id=engine_id, status=status
    ).inc()


def observe_engine_task_duration(engine_id: str, duration: float) -> None:
    """Record engine task processing duration.

    Args:
        engine_id: Engine identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "task_duration_seconds" not in _engine_metrics:
        return
    _engine_metrics["task_duration_seconds"].labels(engine_id=engine_id).observe(
        duration
    )


def observe_engine_queue_wait(engine_id: str, duration: float) -> None:
    """Record queue wait time.

    Args:
        engine_id: Engine identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "queue_wait_seconds" not in _engine_metrics:
        return
    _engine_metrics["queue_wait_seconds"].labels(engine_id=engine_id).observe(duration)


def observe_engine_s3_download(engine_id: str, duration: float) -> None:
    """Record S3 download time.

    Args:
        engine_id: Engine identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "s3_download_seconds" not in _engine_metrics:
        return
    _engine_metrics["s3_download_seconds"].labels(engine_id=engine_id).observe(duration)


def observe_engine_s3_upload(engine_id: str, duration: float) -> None:
    """Record S3 upload time.

    Args:
        engine_id: Engine identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "s3_upload_seconds" not in _engine_metrics:
        return
    _engine_metrics["s3_upload_seconds"].labels(engine_id=engine_id).observe(duration)


# =============================================================================
# Session Router Metrics
# =============================================================================


def set_session_router_workers_registered(count: int) -> None:
    """Set the workers registered gauge.

    Args:
        count: Number of registered workers
    """
    if not _metrics_enabled or "workers_registered" not in _session_router_metrics:
        return
    _session_router_metrics["workers_registered"].set(count)


def set_session_router_workers_healthy(count: int) -> None:
    """Set the healthy workers gauge.

    Args:
        count: Number of healthy workers
    """
    if not _metrics_enabled or "workers_healthy" not in _session_router_metrics:
        return
    _session_router_metrics["workers_healthy"].set(count)


def set_session_router_sessions_active(worker_id: str, count: int) -> None:
    """Set active sessions for a worker.

    Args:
        worker_id: Worker identifier
        count: Number of active sessions
    """
    if not _metrics_enabled or "sessions_active" not in _session_router_metrics:
        return
    _session_router_metrics["sessions_active"].labels(worker_id=worker_id).set(count)


def inc_session_router_sessions(status: str) -> None:
    """Increment sessions counter by status.

    Args:
        status: Session status (completed, error, timeout)
    """
    if not _metrics_enabled or "sessions_total" not in _session_router_metrics:
        return
    _session_router_metrics["sessions_total"].labels(status=status).inc()


def observe_session_router_allocation(duration: float) -> None:
    """Record session allocation duration.

    Args:
        duration: Duration in seconds
    """
    if (
        not _metrics_enabled
        or "allocation_duration_seconds" not in _session_router_metrics
    ):
        return
    _session_router_metrics["allocation_duration_seconds"].observe(duration)


# =============================================================================
# Realtime Metrics
# =============================================================================


def observe_realtime_session_duration(duration: float) -> None:
    """Record realtime session duration.

    Args:
        duration: Duration in seconds
    """
    if not _metrics_enabled or "session_duration_seconds" not in _realtime_metrics:
        return
    _realtime_metrics["session_duration_seconds"].observe(duration)


def inc_realtime_audio_processed(worker_id: str, seconds: float) -> None:
    """Increment audio processed counter.

    Args:
        worker_id: Worker identifier
        seconds: Audio duration in seconds
    """
    if not _metrics_enabled or "audio_processed_seconds" not in _realtime_metrics:
        return
    _realtime_metrics["audio_processed_seconds"].labels(worker_id=worker_id).inc(
        seconds
    )


def inc_realtime_transcripts(transcript_type: str) -> None:
    """Increment transcripts counter.

    Args:
        transcript_type: Transcript type (partial, final)
    """
    if not _metrics_enabled or "transcripts_total" not in _realtime_metrics:
        return
    _realtime_metrics["transcripts_total"].labels(type=transcript_type).inc()


# =============================================================================
# Public Initialization Functions
# =============================================================================


def init_session_router_metrics() -> None:
    """Initialize Session Router metrics for use in Gateway.

    Call this when the Gateway hosts the Session Router and needs to expose
    session router metrics alongside gateway metrics.
    """
    if not _metrics_enabled:
        return
    if "workers_registered" not in _session_router_metrics:
        _init_session_router_metrics()


def init_queue_metrics() -> None:
    """Initialize queue metrics for the exporter."""
    global _metrics_enabled, _metrics_initialized

    _metrics_enabled = True
    _metrics_initialized = True
    _init_queue_metrics()


def set_queue_depth(engine_id: str, depth: int) -> None:
    """Set queue depth for an engine.

    Args:
        engine_id: Engine identifier
        depth: Number of tasks in queue
    """
    if not _metrics_enabled or "queue_depth" not in _queue_metrics:
        return
    _queue_metrics["queue_depth"].labels(engine_id=engine_id).set(depth)


def set_queue_oldest_task_age(engine_id: str, age_seconds: float) -> None:
    """Set oldest task age for an engine.

    Args:
        engine_id: Engine identifier
        age_seconds: Age in seconds
    """
    if not _metrics_enabled or "queue_oldest_task_age_seconds" not in _queue_metrics:
        return
    _queue_metrics["queue_oldest_task_age_seconds"].labels(engine_id=engine_id).set(
        age_seconds
    )


def set_redis_connected(connected: bool) -> None:
    """Set Redis connectivity status.

    Args:
        connected: Whether Redis is connected
    """
    if not _metrics_enabled or "redis_connected" not in _queue_metrics:
        return
    _queue_metrics["redis_connected"].set(1 if connected else 0)
