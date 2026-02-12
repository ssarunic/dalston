"""Integration tests for tracing and logging correlation (M19)."""

import json
import logging
import os
from io import StringIO
from unittest.mock import MagicMock, patch

import structlog

import dalston.logging
import dalston.telemetry


class TestLogTraceCorrelation:
    """Tests for log-trace correlation when tracing is enabled."""

    def setup_method(self):
        """Reset state before each test."""
        structlog.reset_defaults()
        structlog.contextvars.clear_contextvars()
        root = logging.getLogger()
        root.handlers.clear()
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("LOG_LEVEL", None)
        os.environ.pop("LOG_FORMAT", None)
        os.environ.pop("OTEL_ENABLED", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    def teardown_method(self):
        """Clean up after each test."""
        structlog.reset_defaults()
        structlog.contextvars.clear_contextvars()
        root = logging.getLogger()
        root.handlers.clear()
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("LOG_LEVEL", None)
        os.environ.pop("LOG_FORMAT", None)
        os.environ.pop("OTEL_ENABLED", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

    def test_logs_without_trace_when_disabled(self):
        """Logs don't include trace_id/span_id when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("test_event")

        parsed = json.loads(buf.getvalue().strip())
        assert "trace_id" not in parsed
        assert "span_id" not in parsed

    def test_logs_include_trace_when_enabled(self):
        """Logs include trace_id/span_id when tracing enabled and span active."""
        os.environ["OTEL_ENABLED"] = "true"

        # Mock the OTLP exporter to avoid network calls
        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")
            dalston.logging.configure("test-service")

            logger = structlog.get_logger()
            buf = StringIO()

            # Log within a span
            with dalston.telemetry.create_span("test-span"):
                with patch("sys.stdout", buf):
                    logger.info("test_event")

            parsed = json.loads(buf.getvalue().strip())
            assert "trace_id" in parsed
            assert "span_id" in parsed
            # Trace ID should be 32 hex chars
            assert len(parsed["trace_id"]) == 32
            # Span ID should be 16 hex chars
            assert len(parsed["span_id"]) == 16


class TestAddTraceContextProcessor:
    """Tests for the _add_trace_context processor."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)

    def test_noop_when_tracing_disabled(self):
        """Processor is no-op when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        event_dict = {"event": "test"}
        result = dalston.logging._add_trace_context(None, None, event_dict)

        assert result == {"event": "test"}
        assert "trace_id" not in result
        assert "span_id" not in result

    def test_adds_trace_context_when_enabled_with_span(self):
        """Processor adds trace_id/span_id when tracing enabled and span active."""
        os.environ["OTEL_ENABLED"] = "true"

        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")

            with dalston.telemetry.create_span("test-span"):
                event_dict = {"event": "test"}
                result = dalston.logging._add_trace_context(None, None, event_dict)

                assert "trace_id" in result
                assert "span_id" in result


class TestTracingWithEvents:
    """Tests for tracing context propagation with events."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)

    def test_inject_and_extract_roundtrip(self):
        """Trace context can be injected and extracted for propagation."""
        os.environ["OTEL_ENABLED"] = "true"

        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")

            # Inject within a span
            with dalston.telemetry.create_span("parent-span"):
                carrier = dalston.telemetry.inject_trace_context()

                # Should have traceparent header
                assert "traceparent" in carrier

            # Extract should work with the carrier
            ctx = dalston.telemetry.extract_trace_context(carrier)
            assert ctx is not None
