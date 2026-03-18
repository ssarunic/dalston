"""Prometheus metrics configuration for all Dalston services.

Provides a unified `configure_metrics()` function that sets up Prometheus
metrics collection for any Dalston service.

Environment Variables:
    DALSTON_METRICS_ENABLED: Enable/disable metrics collection (default: true)
    DALSTON_METRICS_PORT: Port for metrics endpoint when running standalone exporter

Metric Naming Convention:
    dalston_{service}_{metric_name}_{unit}

Common Labels:
    engine_id: Runtime identifier (e.g., "faster-whisper", "nemo")
    model: Model identifier (e.g., "nvidia/parakeet-tdt-1.1b")
    execution_profile: Runtime isolation profile ("inproc", "venv", "container")
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
_webhook_metrics: dict[str, Any] = {}
_model_metrics: dict[str, Any] = {}


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
            "orchestrator", "stt-transcribe-whisper").

    Environment Variables:
        DALSTON_METRICS_ENABLED: Set to "false" to disable metrics (default: "true")
    """
    global _metrics_enabled, _service_name, _metrics_initialized

    enabled = os.environ.get("DALSTON_METRICS_ENABLED", "true").lower() == "true"
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
        ["engine_id", "model"],
        buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
    )

    _orchestrator_metrics["tasks_scheduled_total"] = Counter(
        "dalston_orchestrator_tasks_scheduled_total",
        "Tasks pushed to queues",
        ["engine_id", "stage", "execution_profile"],
    )

    _orchestrator_metrics["tasks_completed_total"] = Counter(
        "dalston_orchestrator_tasks_completed_total",
        "Task completions",
        ["engine_id", "status", "execution_profile"],
    )

    _orchestrator_metrics["events_processed_total"] = Counter(
        "dalston_orchestrator_events_processed_total",
        "Redis events processed",
        ["event_type"],
    )

    _orchestrator_metrics["event_decisions_total"] = Counter(
        "dalston_orchestrator_event_decisions_total",
        "Durable event processing decisions (ack/retry/dlq)",
        ["decision", "failure_reason", "event_type"],
    )

    _orchestrator_metrics["dag_build_duration_seconds"] = Histogram(
        "dalston_orchestrator_dag_build_duration_seconds",
        "DAG construction time",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
    )

    _orchestrator_metrics["tasks_timed_out_total"] = Counter(
        "dalston_orchestrator_tasks_timed_out_total",
        "Tasks failed by scanner due to timeout",
        ["stage"],
    )

    _orchestrator_metrics["scanner_scans_total"] = Counter(
        "dalston_orchestrator_scanner_scans_total",
        "Number of stale task scanner runs",
        ["status"],
    )


def _init_engine_metrics() -> None:
    """Initialize Engine-specific metrics."""
    from prometheus_client import Counter, Histogram

    _engine_metrics["tasks_processed_total"] = Counter(
        "dalston_engine_tasks_processed_total",
        "Tasks processed",
        ["engine_id", "model", "status", "execution_profile"],
    )

    _engine_metrics["task_duration_seconds"] = Histogram(
        "dalston_engine_task_duration_seconds",
        "Task processing time (excludes queue wait)",
        ["engine_id", "model", "execution_profile"],
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    )

    _engine_metrics["queue_wait_seconds"] = Histogram(
        "dalston_engine_queue_wait_seconds",
        "Time between task enqueue and dequeue",
        ["engine_id", "execution_profile"],
        buckets=(0.01, 0.1, 0.5, 1, 5, 10, 30, 60, 300),
    )

    _engine_metrics["s3_download_seconds"] = Histogram(
        "dalston_engine_s3_download_seconds",
        "Input download time",
        ["engine_id", "execution_profile"],
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10),
    )

    _engine_metrics["s3_upload_seconds"] = Histogram(
        "dalston_engine_s3_upload_seconds",
        "Output upload time",
        ["engine_id", "execution_profile"],
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10),
    )

    _engine_metrics["task_redelivery_total"] = Counter(
        "dalston_engine_task_redelivery_total",
        "Number of task redeliveries (delivery_count > 1)",
        ["stage", "reason"],
    )

    _engine_metrics["tasks_skipped_cancelled_total"] = Counter(
        "dalston_engine_tasks_skipped_cancelled_total",
        "Tasks skipped because job was cancelled",
        ["stage"],
    )

    _engine_metrics["model_load_seconds"] = Histogram(
        "dalston_engine_model_load_seconds",
        "Model loading time (cold load or cache miss)",
        ["engine_id", "model", "execution_profile"],
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    )

    _engine_metrics["model_cache_hits_total"] = Counter(
        "dalston_engine_model_cache_hits_total",
        "Model cache hits (model already loaded)",
        ["engine_id", "model"],
    )

    # M76: Inference-layer telemetry (fires for both batch queue and HTTP paths)
    _engine_metrics["model_acquire_seconds"] = Histogram(
        "dalston_engine_model_acquire_seconds",
        "Time to acquire model handle (lock + possible cold load)",
        ["engine_id", "model", "in_memory"],
        buckets=(0.001, 0.01, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60),
    )

    _engine_metrics["recognize_seconds"] = Histogram(
        "dalston_engine_recognize_seconds",
        "Wall-clock time for the recognize/transcribe call",
        ["engine_id", "model", "device"],
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    )

    _engine_metrics["realtime_factor_ratio"] = Histogram(
        "dalston_engine_realtime_factor_ratio",
        "Real-time factor (processing_time / audio_duration)",
        ["engine_id", "model", "device"],
        buckets=(0.01, 0.03, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0, 5.0),
    )

    _engine_metrics["vad_segment_count"] = Histogram(
        "dalston_engine_vad_segment_count",
        "Number of VAD segments per transcription",
        ["engine_id"],
        buckets=(1, 2, 5, 10, 20, 50, 100, 200),
    )

    _engine_metrics["direct_request_seconds"] = Histogram(
        "dalston_engine_direct_request_seconds",
        "Total HTTP request duration for direct engine calls (M79 leaf API)",
        ["engine_id", "endpoint", "status_code"],
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    )

    _engine_metrics["direct_requests_total"] = Counter(
        "dalston_engine_direct_requests_total",
        "Total HTTP requests to direct engine endpoints (M79 leaf API)",
        ["engine_id", "endpoint", "status_code"],
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
        ["engine_id", "model"],
        buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
    )

    _realtime_metrics["audio_processed_seconds"] = Counter(
        "dalston_realtime_audio_processed_seconds",
        "Cumulative audio processed",
        ["engine_id", "model"],
    )

    _realtime_metrics["transcripts_total"] = Counter(
        "dalston_realtime_transcripts_total",
        "Transcripts emitted",
        ["engine_id", "model", "type"],
    )

    _realtime_metrics["resample_duration_seconds"] = Histogram(
        "dalston_realtime_resample_duration_seconds",
        "Time spent resampling audio chunks",
        ["from_rate", "to_rate"],
        buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05),
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


def _init_webhook_metrics() -> None:
    """Initialize webhook delivery metrics."""
    from prometheus_client import Counter, Histogram

    _webhook_metrics["deliveries_total"] = Counter(
        "dalston_webhook_deliveries_total",
        "Webhook deliveries by outcome",
        ["status"],
    )

    _webhook_metrics["delivery_duration_seconds"] = Histogram(
        "dalston_webhook_delivery_duration_seconds",
        "Webhook delivery latency",
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
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


def observe_orchestrator_job_duration(
    engine_id: str, model: str, duration: float
) -> None:
    """Record job duration.

    Args:
        engine_id: Runtime identifier (e.g., "faster-whisper")
        model: Model identifier (e.g., "nvidia/parakeet-tdt-1.1b")
        duration: Duration in seconds
    """
    if not _metrics_enabled or "job_duration_seconds" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["job_duration_seconds"].labels(
        engine_id=engine_id, model=model
    ).observe(duration)


def inc_orchestrator_tasks_scheduled(
    engine_id: str,
    stage: str,
    execution_profile: str = "unknown",
) -> None:
    """Increment tasks scheduled counter.

    Args:
        engine_id: Runtime identifier
        stage: Pipeline stage name
    """
    if not _metrics_enabled or "tasks_scheduled_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["tasks_scheduled_total"].labels(
        engine_id=engine_id,
        stage=stage,
        execution_profile=execution_profile,
    ).inc()


def inc_orchestrator_tasks_completed(
    engine_id: str,
    status: str,
    execution_profile: str = "unknown",
) -> None:
    """Increment tasks completed counter.

    Args:
        engine_id: Runtime identifier
        status: Task status (success, failure)
    """
    if not _metrics_enabled or "tasks_completed_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["tasks_completed_total"].labels(
        engine_id=engine_id,
        status=status,
        execution_profile=execution_profile,
    ).inc()


def inc_orchestrator_events(event_type: str) -> None:
    """Increment events processed counter.

    Args:
        event_type: Event type
    """
    if not _metrics_enabled or "events_processed_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["events_processed_total"].labels(event_type=event_type).inc()


def inc_orchestrator_event_decision(
    decision: str,
    failure_reason: str,
    event_type: str,
) -> None:
    """Increment durable event decision counter.

    Args:
        decision: Decision type ("ack", "retry", "dlq")
        failure_reason: Reason associated with decision ("none" when acked)
        event_type: Durable event type
    """
    if not _metrics_enabled or "event_decisions_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["event_decisions_total"].labels(
        decision=decision,
        failure_reason=failure_reason,
        event_type=event_type,
    ).inc()


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


def inc_orchestrator_tasks_timed_out(stage: str) -> None:
    """Increment tasks timed out counter.

    Called by the stale task scanner when a task is failed due to timeout.

    Args:
        stage: Pipeline stage name
    """
    if not _metrics_enabled or "tasks_timed_out_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["tasks_timed_out_total"].labels(stage=stage).inc()


def inc_orchestrator_scanner_scans(status: str) -> None:
    """Increment scanner scans counter.

    Called after each scanner run to track scan outcomes.

    Args:
        status: Scan status (success, error, skipped_not_leader)
    """
    if not _metrics_enabled or "scanner_scans_total" not in _orchestrator_metrics:
        return
    _orchestrator_metrics["scanner_scans_total"].labels(status=status).inc()


# =============================================================================
# Engine Metrics
# =============================================================================


def inc_engine_tasks(
    engine_id: str,
    model: str,
    status: str,
    execution_profile: str = "unknown",
) -> None:
    """Increment engine tasks processed counter.

    Args:
        engine_id: Runtime identifier (e.g., "faster-whisper")
        model: Model identifier (e.g., "nvidia/parakeet-tdt-1.1b")
        status: Task status (success, failure)
    """
    if not _metrics_enabled or "tasks_processed_total" not in _engine_metrics:
        return
    _engine_metrics["tasks_processed_total"].labels(
        engine_id=engine_id,
        model=model,
        status=status,
        execution_profile=execution_profile,
    ).inc()


def observe_engine_task_duration(
    engine_id: str,
    model: str,
    duration: float,
    execution_profile: str = "unknown",
) -> None:
    """Record engine task processing duration.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "task_duration_seconds" not in _engine_metrics:
        return
    _engine_metrics["task_duration_seconds"].labels(
        engine_id=engine_id,
        model=model,
        execution_profile=execution_profile,
    ).observe(duration)


def observe_engine_queue_wait(
    engine_id: str,
    duration: float,
    execution_profile: str = "unknown",
) -> None:
    """Record queue wait time.

    Args:
        engine_id: Runtime identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "queue_wait_seconds" not in _engine_metrics:
        return
    _engine_metrics["queue_wait_seconds"].labels(
        engine_id=engine_id,
        execution_profile=execution_profile,
    ).observe(duration)


def observe_engine_s3_download(
    engine_id: str,
    duration: float,
    execution_profile: str = "unknown",
) -> None:
    """Record S3 download time.

    Args:
        engine_id: Runtime identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "s3_download_seconds" not in _engine_metrics:
        return
    _engine_metrics["s3_download_seconds"].labels(
        engine_id=engine_id,
        execution_profile=execution_profile,
    ).observe(duration)


def observe_engine_s3_upload(
    engine_id: str,
    duration: float,
    execution_profile: str = "unknown",
) -> None:
    """Record S3 upload time.

    Args:
        engine_id: Runtime identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "s3_upload_seconds" not in _engine_metrics:
        return
    _engine_metrics["s3_upload_seconds"].labels(
        engine_id=engine_id,
        execution_profile=execution_profile,
    ).observe(duration)


def observe_engine_model_load(
    engine_id: str,
    model: str,
    duration: float,
    execution_profile: str = "unknown",
) -> None:
    """Record model loading time.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "model_load_seconds" not in _engine_metrics:
        return
    _engine_metrics["model_load_seconds"].labels(
        engine_id=engine_id,
        model=model,
        execution_profile=execution_profile,
    ).observe(duration)


def inc_engine_model_cache_hit(engine_id: str, model: str) -> None:
    """Increment model cache hit counter.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
    """
    if not _metrics_enabled or "model_cache_hits_total" not in _engine_metrics:
        return
    _engine_metrics["model_cache_hits_total"].labels(
        engine_id=engine_id, model=model
    ).inc()


def inc_task_redelivery(stage: str, reason: str) -> None:
    """Increment task redelivery counter.

    Called when a task is redelivered (delivery_count > 1), indicating
    recovery from a crashed engine.

    Args:
        stage: Pipeline stage name
        reason: Reason for redelivery (e.g., "engine_crash", "timeout", "manual_retry")
    """
    if not _metrics_enabled or "task_redelivery_total" not in _engine_metrics:
        return
    _engine_metrics["task_redelivery_total"].labels(stage=stage, reason=reason).inc()


def inc_tasks_skipped_cancelled(stage: str) -> None:
    """Increment counter for tasks skipped due to job cancellation.

    Called when an engine skips processing a task because its job was cancelled.

    Args:
        stage: Pipeline stage name
    """
    if not _metrics_enabled or "tasks_skipped_cancelled_total" not in _engine_metrics:
        return
    _engine_metrics["tasks_skipped_cancelled_total"].labels(stage=stage).inc()


# -- M76: Inference-layer metrics (fires for both batch and HTTP paths) -------


def observe_engine_model_acquire(
    engine_id: str, model: str, duration: float, *, in_memory: bool
) -> None:
    """Record time to acquire a model handle (lock + possible cold load)."""
    if not _metrics_enabled or "model_acquire_seconds" not in _engine_metrics:
        return
    _engine_metrics["model_acquire_seconds"].labels(
        engine_id=engine_id,
        model=model,
        in_memory=str(in_memory).lower(),
    ).observe(duration)


def observe_engine_recognize(
    engine_id: str, model: str, device: str, duration: float
) -> None:
    """Record wall-clock time for the recognize/transcribe call."""
    if not _metrics_enabled or "recognize_seconds" not in _engine_metrics:
        return
    _engine_metrics["recognize_seconds"].labels(
        engine_id=engine_id, model=model, device=device
    ).observe(duration)


def observe_engine_realtime_factor(
    engine_id: str, model: str, device: str, rtf: float
) -> None:
    """Record real-time factor (processing_time / audio_duration)."""
    if not _metrics_enabled or "realtime_factor_ratio" not in _engine_metrics:
        return
    _engine_metrics["realtime_factor_ratio"].labels(
        engine_id=engine_id, model=model, device=device
    ).observe(rtf)


def observe_engine_vad_segment_count(engine_id: str, count: int) -> None:
    """Record number of VAD segments produced."""
    if not _metrics_enabled or "vad_segment_count" not in _engine_metrics:
        return
    _engine_metrics["vad_segment_count"].labels(engine_id=engine_id).observe(count)


def observe_engine_direct_request(
    engine_id: str, endpoint: str, status_code: int, duration: float
) -> None:
    """Record an HTTP request to a direct engine endpoint (M79 leaf API)."""
    if not _metrics_enabled or "direct_request_seconds" not in _engine_metrics:
        return
    _engine_metrics["direct_request_seconds"].labels(
        engine_id=engine_id, endpoint=endpoint, status_code=str(status_code)
    ).observe(duration)


def inc_engine_direct_requests(
    engine_id: str, endpoint: str, status_code: int
) -> None:
    """Increment the request counter for direct engine endpoints (M79 leaf API)."""
    if not _metrics_enabled or "direct_requests_total" not in _engine_metrics:
        return
    _engine_metrics["direct_requests_total"].labels(
        engine_id=engine_id, endpoint=endpoint, status_code=str(status_code)
    ).inc()


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


def observe_realtime_session_duration(
    engine_id: str, model: str, duration: float
) -> None:
    """Record realtime session duration.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
        duration: Duration in seconds
    """
    if not _metrics_enabled or "session_duration_seconds" not in _realtime_metrics:
        return
    _realtime_metrics["session_duration_seconds"].labels(
        engine_id=engine_id, model=model
    ).observe(duration)


def inc_realtime_audio_processed(engine_id: str, model: str, seconds: float) -> None:
    """Increment audio processed counter.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
        seconds: Audio duration in seconds
    """
    if not _metrics_enabled or "audio_processed_seconds" not in _realtime_metrics:
        return
    _realtime_metrics["audio_processed_seconds"].labels(
        engine_id=engine_id, model=model
    ).inc(seconds)


def inc_realtime_transcripts(engine_id: str, model: str, transcript_type: str) -> None:
    """Increment transcripts counter.

    Args:
        engine_id: Runtime identifier
        model: Model identifier
        transcript_type: Transcript type (partial, final)
    """
    if not _metrics_enabled or "transcripts_total" not in _realtime_metrics:
        return
    _realtime_metrics["transcripts_total"].labels(
        engine_id=engine_id, model=model, type=transcript_type
    ).inc()


def observe_realtime_resample_duration(
    from_rate: int, to_rate: int, duration: float
) -> None:
    """Record time spent resampling an audio chunk.

    Args:
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz
        duration: Wall-clock seconds spent resampling
    """
    if not _metrics_enabled or "resample_duration_seconds" not in _realtime_metrics:
        return
    _realtime_metrics["resample_duration_seconds"].labels(
        from_rate=str(from_rate), to_rate=str(to_rate)
    ).observe(duration)


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
    """Set queue depth for a engine_id.

    Args:
        engine_id: Runtime identifier
        depth: Number of tasks in queue
    """
    if not _metrics_enabled or "queue_depth" not in _queue_metrics:
        return
    _queue_metrics["queue_depth"].labels(engine_id=engine_id).set(depth)


def set_queue_oldest_task_age(engine_id: str, age_seconds: float) -> None:
    """Set oldest task age for a engine_id.

    Args:
        engine_id: Runtime identifier
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


# =============================================================================
# Webhook Metrics
# =============================================================================


def inc_webhook_deliveries(status: str) -> None:
    """Increment webhook deliveries counter.

    Args:
        status: Delivery status (success, failed, retried)
    """
    if not _metrics_enabled or "deliveries_total" not in _webhook_metrics:
        return
    _webhook_metrics["deliveries_total"].labels(status=status).inc()


def observe_webhook_delivery_duration(duration: float) -> None:
    """Record webhook delivery latency.

    Args:
        duration: Duration in seconds
    """
    if not _metrics_enabled or "delivery_duration_seconds" not in _webhook_metrics:
        return
    _webhook_metrics["delivery_duration_seconds"].observe(duration)


def init_webhook_metrics() -> None:
    """Initialize webhook metrics for use in orchestrator."""
    if not _metrics_enabled:
        return
    if "deliveries_total" not in _webhook_metrics:
        _init_webhook_metrics()
