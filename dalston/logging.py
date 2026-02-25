"""Unified structured logging configuration for all Dalston services.

Provides a single `configure()` function that sets up structlog with:
- JSON output for production (DALSTON_LOG_FORMAT=json)
- Colored console output for development (DALSTON_LOG_FORMAT=console)
- Configurable log level via DALSTON_LOG_LEVEL environment variable
- Context variable merging for correlation IDs (request_id, job_id, etc.)
- Standard library integration so third-party libraries emit structured output
- Log-trace correlation (M19): Injects trace_id and span_id when tracing is enabled
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

import dalston.telemetry

# Stores the service name set by configure() so reset_context() can restore it.
_configured_service_name: str | None = None


def _add_service_name(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Structlog processor that adds the service name to every log entry."""
    if "_service_name" in event_dict:
        event_dict["service"] = event_dict.pop("_service_name")
    return event_dict


def _add_trace_context(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Structlog processor that adds trace_id and span_id from OpenTelemetry span.

    Only adds trace context when tracing is enabled and a span is active.
    This enables log-trace correlation in observability platforms like Jaeger.
    """
    if not dalston.telemetry.is_tracing_enabled():
        return event_dict

    trace_id = dalston.telemetry.get_current_trace_id()
    span_id = dalston.telemetry.get_current_span_id()

    if trace_id:
        event_dict["trace_id"] = trace_id
    if span_id:
        event_dict["span_id"] = span_id

    return event_dict


def configure(service_name: str) -> None:
    """Configure structlog for a Dalston service.

    Sets up the structlog processor pipeline and integrates with the
    standard library so third-party loggers (uvicorn, boto3, etc.)
    also emit structured output.

    Args:
        service_name: Identifier for this service (e.g. "gateway",
            "orchestrator", "stt-batch-transcribe-whisper").

    Environment Variables:
        DALSTON_LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            Defaults to INFO.
        DALSTON_LOG_FORMAT: Output format. "json" (default) for machine-parseable
            JSON lines, "console" for colored human-readable output.
    """
    log_level_name = os.environ.get("DALSTON_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    log_format = os.environ.get("DALSTON_LOG_FORMAT", "json").lower()

    # Shared processors used by both structlog and stdlib integration
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_service_name,
        _add_trace_context,  # M19: Log-trace correlation
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Integrate standard library logging so that third-party loggers
    # (uvicorn, boto3, etc.) also produce structured output.
    # Reset any existing basicConfig handlers.
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *shared_processors,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Bind service name into the context so every log line includes it
    global _configured_service_name
    _configured_service_name = service_name
    structlog.contextvars.bind_contextvars(_service_name=service_name)


def reset_context(**extra: str) -> None:
    """Clear structlog contextvars and re-apply the service name.

    Use this in event loops (orchestrator, middleware) to isolate context
    between requests/events while preserving the service name originally
    bound by :func:`configure`.

    Args:
        **extra: Additional context variables to bind (e.g. request_id).
    """
    structlog.contextvars.clear_contextvars()
    if _configured_service_name:
        structlog.contextvars.bind_contextvars(_service_name=_configured_service_name)
    if extra:
        structlog.contextvars.bind_contextvars(**extra)
