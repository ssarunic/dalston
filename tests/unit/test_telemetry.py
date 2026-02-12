"""Unit tests for dalston.telemetry distributed tracing module."""

import os
from unittest.mock import MagicMock, patch

import dalston.telemetry


class TestConfigureTracing:
    """Tests for dalston.telemetry.configure_tracing()."""

    def setup_method(self):
        """Reset telemetry state before each test."""
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        os.environ.pop("OTEL_INSECURE", None)

    def teardown_method(self):
        """Clean up after each test."""
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False
        os.environ.pop("OTEL_ENABLED", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        os.environ.pop("OTEL_INSECURE", None)

    def test_tracing_disabled_by_default(self):
        """Tracing is disabled when OTEL_ENABLED is not set."""
        dalston.telemetry.configure_tracing("test-service")

        assert not dalston.telemetry.is_tracing_enabled()
        assert dalston.telemetry._tracer is not None
        # Verify it's a NoOpTracer
        from opentelemetry.trace import NoOpTracer

        assert isinstance(dalston.telemetry._tracer, NoOpTracer)

    def test_tracing_disabled_when_false(self):
        """Tracing is disabled when OTEL_ENABLED=false."""
        os.environ["OTEL_ENABLED"] = "false"
        dalston.telemetry.configure_tracing("test-service")

        assert not dalston.telemetry.is_tracing_enabled()

    def test_tracing_enabled_when_true(self):
        """Tracing is enabled when OTEL_ENABLED=true."""
        os.environ["OTEL_ENABLED"] = "true"

        # Mock the OTLP exporter to avoid network calls
        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")

        assert dalston.telemetry.is_tracing_enabled()
        assert dalston.telemetry._tracer is not None

    def test_custom_endpoint(self):
        """Custom OTLP endpoint is used when specified."""
        os.environ["OTEL_ENABLED"] = "true"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://custom:4317"

        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")

            mock_exporter_class.assert_called_once_with(
                endpoint="http://custom:4317", insecure=True
            )

    def test_insecure_disabled_for_tls(self):
        """OTEL_INSECURE=false disables insecure mode for TLS connections."""
        os.environ["OTEL_ENABLED"] = "true"
        os.environ["OTEL_INSECURE"] = "false"

        with patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter_class.return_value = mock_exporter

            dalston.telemetry.configure_tracing("test-service")

            mock_exporter_class.assert_called_once_with(
                endpoint="http://localhost:4317", insecure=False
            )


class TestGetTracer:
    """Tests for dalston.telemetry.get_tracer()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_returns_noop_tracer_when_not_configured(self):
        """Returns NoOpTracer when telemetry not configured."""
        tracer = dalston.telemetry.get_tracer()

        from opentelemetry.trace import NoOpTracer

        assert isinstance(tracer, NoOpTracer)

    def test_returns_configured_tracer(self):
        """Returns the configured tracer after configure_tracing()."""
        dalston.telemetry.configure_tracing("test-service")
        tracer = dalston.telemetry.get_tracer()

        assert tracer is dalston.telemetry._tracer


class TestCreateSpan:
    """Tests for dalston.telemetry.create_span()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_creates_span_without_error(self):
        """create_span works even when tracing is disabled."""
        dalston.telemetry.configure_tracing("test-service")

        with dalston.telemetry.create_span("test-span") as span:
            assert span is not None

    def test_creates_span_with_attributes(self):
        """create_span accepts attributes."""
        dalston.telemetry.configure_tracing("test-service")

        with dalston.telemetry.create_span(
            "test-span",
            attributes={"key": "value"},
        ) as span:
            assert span is not None


class TestInjectExtractTraceContext:
    """Tests for trace context propagation."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_inject_returns_empty_dict_when_disabled(self):
        """inject_trace_context returns empty dict when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        carrier = dalston.telemetry.inject_trace_context()

        assert carrier == {}

    def test_extract_returns_none_when_disabled(self):
        """extract_trace_context returns None when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        result = dalston.telemetry.extract_trace_context({"traceparent": "test"})

        assert result is None

    def test_extract_returns_none_for_empty_carrier(self):
        """extract_trace_context returns None for empty carrier."""
        dalston.telemetry.configure_tracing("test-service")

        result = dalston.telemetry.extract_trace_context({})

        assert result is None


class TestSpanFromContext:
    """Tests for dalston.telemetry.span_from_context()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_creates_span_with_empty_carrier(self):
        """span_from_context works with empty carrier."""
        dalston.telemetry.configure_tracing("test-service")

        with dalston.telemetry.span_from_context("test-span", {}) as span:
            assert span is not None

    def test_creates_span_with_attributes(self):
        """span_from_context accepts attributes."""
        dalston.telemetry.configure_tracing("test-service")

        with dalston.telemetry.span_from_context(
            "test-span",
            {},
            attributes={"key": "value"},
        ) as span:
            assert span is not None


class TestSetSpanAttribute:
    """Tests for dalston.telemetry.set_span_attribute()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_noop_when_disabled(self):
        """set_span_attribute is a no-op when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        # Should not raise
        dalston.telemetry.set_span_attribute("key", "value")


class TestRecordException:
    """Tests for dalston.telemetry.record_exception()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_noop_when_disabled(self):
        """record_exception is a no-op when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        # Should not raise
        dalston.telemetry.record_exception(ValueError("test error"))


class TestSetSpanStatusError:
    """Tests for dalston.telemetry.set_span_status_error()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_noop_when_disabled(self):
        """set_span_status_error is a no-op when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        # Should not raise
        dalston.telemetry.set_span_status_error("test error")


class TestGetCurrentTraceId:
    """Tests for dalston.telemetry.get_current_trace_id()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_returns_none_when_disabled(self):
        """get_current_trace_id returns None when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        assert dalston.telemetry.get_current_trace_id() is None


class TestGetCurrentSpanId:
    """Tests for dalston.telemetry.get_current_span_id()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_returns_none_when_disabled(self):
        """get_current_span_id returns None when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        assert dalston.telemetry.get_current_span_id() is None


class TestShutdownTracing:
    """Tests for dalston.telemetry.shutdown_tracing()."""

    def setup_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def teardown_method(self):
        dalston.telemetry._tracer = None
        dalston.telemetry._tracing_enabled = False

    def test_noop_when_disabled(self):
        """shutdown_tracing is a no-op when tracing disabled."""
        dalston.telemetry.configure_tracing("test-service")

        # Should not raise
        dalston.telemetry.shutdown_tracing()
