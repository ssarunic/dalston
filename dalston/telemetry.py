"""OpenTelemetry distributed tracing configuration for Dalston services.

Provides a unified `configure_tracing()` function that initializes OpenTelemetry with:
- OTLP exporter for trace data (supports Jaeger, Tempo, Datadog, etc.)
- No-op tracer when disabled (zero performance overhead)
- Trace context linked to structlog correlation IDs
- Context propagation utilities for Redis pub/sub

Environment Variables:
    OTEL_ENABLED: Enable/disable tracing (default: false)
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP exporter target (default: http://localhost:4317)
    OTEL_INSECURE: Use insecure (non-TLS) connection to exporter (default: true)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer

# Global state
_tracer: Tracer | None = None
_tracing_enabled: bool = False


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled."""
    return _tracing_enabled


def configure_tracing(service_name: str) -> None:
    """Configure OpenTelemetry tracing for a Dalston service.

    Initializes the OpenTelemetry SDK with OTLP exporter when enabled.
    When disabled, creates a no-op tracer with zero overhead.

    Args:
        service_name: Identifier for this service (e.g. "dalston-gateway",
            "dalston-orchestrator", "dalston-stt-batch-transcribe-whisper").

    Environment Variables:
        OTEL_ENABLED: Set to "true" to enable tracing (default: "false")
        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint URL
            (default: "http://localhost:4317")
        OTEL_INSECURE: Set to "false" to require TLS (default: "true")
    """
    global _tracer, _tracing_enabled

    enabled = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
    _tracing_enabled = enabled

    if not enabled:
        # Create no-op tracer
        from opentelemetry.trace import NoOpTracer

        _tracer = NoOpTracer()
        return

    # Import OpenTelemetry components only when tracing is enabled
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    insecure = os.environ.get("OTEL_INSECURE", "true").lower() == "true"

    # Create resource with service name
    resource = Resource.create({SERVICE_NAME: service_name})

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Create OTLP exporter
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)

    # Add batch processor for efficient export
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    # Get tracer for this service
    _tracer = trace.get_tracer(service_name)


def get_tracer() -> Tracer:
    """Get the configured tracer instance.

    Returns:
        The configured tracer, or a no-op tracer if not configured.
    """
    global _tracer
    if _tracer is None:
        from opentelemetry.trace import NoOpTracer

        _tracer = NoOpTracer()
    return _tracer


@contextmanager
def create_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: Any = None,
) -> Iterator[Span]:
    """Create a span with the given name and attributes.

    This is a convenience wrapper that handles the no-op case gracefully.

    Args:
        name: Name of the span
        attributes: Optional dict of span attributes
        kind: Optional span kind (SpanKind.SERVER, SpanKind.CLIENT, etc.)

    Yields:
        The created span (may be a no-op span if tracing is disabled)
    """
    tracer = get_tracer()

    if kind is None:
        from opentelemetry.trace import SpanKind

        kind = SpanKind.INTERNAL

    with tracer.start_as_current_span(name, kind=kind) as span:
        if attributes and hasattr(span, "set_attributes"):
            span.set_attributes(attributes)
        yield span


def inject_trace_context() -> dict[str, str]:
    """Extract current trace context for propagation.

    Use this to serialize trace context into Redis messages, task payloads,
    or other cross-service communication.

    Returns:
        Dict containing trace context headers (traceparent, tracestate)
    """
    if not _tracing_enabled:
        return {}

    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]) -> Any:
    """Extract trace context from a carrier dict.

    Use this to restore trace context from Redis messages, task payloads,
    or other cross-service communication.

    Args:
        carrier: Dict containing trace context headers

    Returns:
        Context object that can be used with trace.set_span_in_context()
    """
    if not _tracing_enabled or not carrier:
        return None

    from opentelemetry.propagate import extract

    return extract(carrier)


@contextmanager
def span_from_context(
    name: str,
    carrier: dict[str, str],
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Create a span linked to a propagated trace context.

    Use this when receiving messages from other services to continue
    the distributed trace.

    Args:
        name: Name of the span
        carrier: Dict containing trace context headers from the sender
        attributes: Optional dict of span attributes

    Yields:
        The created span linked to the parent trace
    """
    tracer = get_tracer()

    if not _tracing_enabled or not carrier:
        with tracer.start_as_current_span(name) as span:
            if attributes and hasattr(span, "set_attributes"):
                span.set_attributes(attributes)
            yield span
        return

    from opentelemetry import context as otel_context
    from opentelemetry.propagate import extract

    ctx = extract(carrier)
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span(name) as span:
            if attributes and hasattr(span, "set_attributes"):
                span.set_attributes(attributes)
            yield span
    finally:
        otel_context.detach(token)


def set_span_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span.

    Safe to call even if no span is active or tracing is disabled.

    Args:
        key: Attribute key
        value: Attribute value
    """
    if not _tracing_enabled:
        return

    from opentelemetry import trace

    span = trace.get_current_span()
    if span and hasattr(span, "set_attribute"):
        span.set_attribute(key, value)


def record_exception(exception: BaseException) -> None:
    """Record an exception on the current span.

    Safe to call even if no span is active or tracing is disabled.

    Args:
        exception: The exception to record
    """
    if not _tracing_enabled:
        return

    from opentelemetry import trace

    span = trace.get_current_span()
    if span and hasattr(span, "record_exception"):
        span.record_exception(exception)


def set_span_status_error(description: str) -> None:
    """Set the current span status to ERROR.

    Should be called alongside record_exception() to mark the span as failed.
    Safe to call even if no span is active or tracing is disabled.

    Args:
        description: Error description message
    """
    if not _tracing_enabled:
        return

    from opentelemetry import trace
    from opentelemetry.trace import StatusCode

    span = trace.get_current_span()
    if span and hasattr(span, "set_status"):
        span.set_status(StatusCode.ERROR, description)


def get_current_trace_id() -> str | None:
    """Get the current trace ID as a hex string.

    Returns:
        The trace ID or None if no active span or tracing disabled
    """
    if not _tracing_enabled:
        return None

    from opentelemetry import trace

    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().trace_id, "032x")
    return None


def get_current_span_id() -> str | None:
    """Get the current span ID as a hex string.

    Returns:
        The span ID or None if no active span or tracing disabled
    """
    if not _tracing_enabled:
        return None

    from opentelemetry import trace

    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        return format(span.get_span_context().span_id, "016x")
    return None


def shutdown_tracing() -> None:
    """Shutdown the tracer provider and flush pending spans.

    Call this during graceful shutdown to ensure all spans are exported.
    """
    if not _tracing_enabled:
        return

    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
