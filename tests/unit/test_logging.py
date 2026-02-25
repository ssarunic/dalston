"""Unit tests for dalston.logging unified structured logging module."""

import json
import logging
import os
from io import StringIO
from unittest.mock import patch

import pytest
import structlog

import dalston.logging


class TestConfigure:
    """Tests for dalston.logging.configure()."""

    def setup_method(self):
        """Reset structlog and stdlib state before each test."""
        structlog.reset_defaults()
        structlog.contextvars.clear_contextvars()
        root = logging.getLogger()
        root.handlers.clear()

    def teardown_method(self):
        """Clean up after each test."""
        structlog.reset_defaults()
        structlog.contextvars.clear_contextvars()
        root = logging.getLogger()
        root.handlers.clear()
        # Restore env vars
        os.environ.pop("DALSTON_LOG_LEVEL", None)
        os.environ.pop("DALSTON_LOG_FORMAT", None)

    def test_configure_sets_json_renderer_by_default(self):
        """Default LOG_FORMAT produces JSON output."""
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        # Capture output by patching print logger target
        with patch("sys.stdout", buf):
            logger.info("hello")

        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "hello"
        assert parsed["level"] == "info"
        assert parsed["service"] == "test-service"
        assert "timestamp" in parsed

    def test_configure_json_format_explicit(self):
        """LOG_FORMAT=json produces JSON output."""
        os.environ["DALSTON_LOG_FORMAT"] = "json"
        dalston.logging.configure("gateway")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("test_event", key="value")

        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "test_event"
        assert parsed["key"] == "value"
        assert parsed["service"] == "gateway"

    def test_configure_console_format(self):
        """LOG_FORMAT=console produces human-readable (non-JSON) output."""
        os.environ["DALSTON_LOG_FORMAT"] = "console"
        dalston.logging.configure("dev-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("dev_event")

        line = buf.getvalue().strip()
        # Console output should NOT be valid JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)
        # But should contain the event name
        assert "dev_event" in line

    def test_configure_default_log_level_info(self):
        """Default log level is INFO, so DEBUG messages are filtered."""
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("debug_msg")

        assert buf.getvalue() == ""

    def test_configure_log_level_debug(self):
        """LOG_LEVEL=DEBUG allows debug messages through."""
        os.environ["DALSTON_LOG_LEVEL"] = "DEBUG"
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("debug_msg")

        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "debug_msg"
        assert parsed["level"] == "debug"

    def test_configure_log_level_warning(self):
        """LOG_LEVEL=WARNING filters out INFO messages."""
        os.environ["DALSTON_LOG_LEVEL"] = "WARNING"
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("info_msg")
            logger.warning("warn_msg")

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event"] == "warn_msg"

    def test_configure_log_level_case_insensitive(self):
        """LOG_LEVEL is case-insensitive."""
        os.environ["DALSTON_LOG_LEVEL"] = "debug"
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("works")

        assert "works" in buf.getvalue()

    def test_configure_invalid_log_level_falls_back_to_info(self):
        """Invalid LOG_LEVEL falls back to INFO."""
        os.environ["DALSTON_LOG_LEVEL"] = "NOTAVALIDLEVEL"
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.debug("debug_msg")

        # DEBUG should be filtered since we fell back to INFO
        assert buf.getvalue() == ""

    def test_configure_service_name_in_output(self):
        """Service name appears in JSON output as 'service' field."""
        dalston.logging.configure("my-engine")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("test")

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["service"] == "my-engine"

    def test_configure_timestamp_iso_format(self):
        """Timestamps are in ISO format."""
        dalston.logging.configure("test-service")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("test")

        parsed = json.loads(buf.getvalue().strip())
        ts = parsed["timestamp"]
        # ISO format timestamps contain 'T' or '-'
        assert "T" in ts or "-" in ts

    def test_configure_contextvars_merged(self):
        """Context variables are included in log output."""
        dalston.logging.configure("test-service")
        structlog.contextvars.bind_contextvars(request_id="req_abc123")

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("with_context")

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["request_id"] == "req_abc123"

    def test_configure_stdlib_integration(self):
        """Standard library loggers produce structured output after configure."""
        dalston.logging.configure("test-service")

        # The handler was created with a reference to the real sys.stdout,
        # so we need to redirect the handler's stream directly.
        root = logging.getLogger()
        buf = StringIO()
        root.handlers[0].stream = buf

        stdlib_logger = logging.getLogger("some.third_party.lib")
        stdlib_logger.warning("stdlib warning message")

        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["event"] == "stdlib warning message"
        assert parsed["level"] == "warning"

    def test_configure_clears_previous_handlers(self):
        """Calling configure clears previous handlers and adds exactly one."""
        # Add dummy handlers
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        count_before = len(root.handlers)
        assert count_before >= 2

        dalston.logging.configure("test-service")

        # Should have exactly 1 handler (the structlog ProcessorFormatter one)
        assert len(root.handlers) == 1
        assert isinstance(
            root.handlers[0].formatter, structlog.stdlib.ProcessorFormatter
        )

    def test_configure_can_be_called_multiple_times(self):
        """Calling configure twice does not cause errors or duplicate handlers."""
        dalston.logging.configure("first")
        dalston.logging.configure("second")

        root = logging.getLogger()
        assert len(root.handlers) == 1

        logger = structlog.get_logger()
        buf = StringIO()
        with patch("sys.stdout", buf):
            logger.info("test")

        parsed = json.loads(buf.getvalue().strip())
        assert parsed["service"] == "second"


class TestAddServiceNameProcessor:
    """Tests for the _add_service_name processor."""

    def test_moves_private_key_to_service(self):
        event_dict = {"event": "test", "_service_name": "gateway"}
        result = dalston.logging._add_service_name(None, None, event_dict)
        assert result["service"] == "gateway"
        assert "_service_name" not in result

    def test_noop_without_private_key(self):
        event_dict = {"event": "test", "key": "value"}
        result = dalston.logging._add_service_name(None, None, event_dict)
        assert result == {"event": "test", "key": "value"}
        assert "service" not in result


class TestResetContext:
    """Tests for dalston.logging.reset_context()."""

    def setup_method(self):
        structlog.contextvars.clear_contextvars()
        dalston.logging._configured_service_name = None

    def teardown_method(self):
        structlog.contextvars.clear_contextvars()
        dalston.logging._configured_service_name = None

    def test_clears_stale_context(self):
        """reset_context removes previously bound keys."""
        structlog.contextvars.bind_contextvars(stale="old_value")
        dalston.logging.reset_context()
        ctx = structlog.contextvars.get_contextvars()
        assert "stale" not in ctx

    def test_preserves_service_name(self):
        """reset_context re-binds the service name set by configure()."""
        dalston.logging._configured_service_name = "orchestrator"
        structlog.contextvars.bind_contextvars(_service_name="orchestrator")
        dalston.logging.reset_context()
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["_service_name"] == "orchestrator"

    def test_binds_extra_kwargs(self):
        """reset_context binds additional keyword arguments."""
        dalston.logging._configured_service_name = "gateway"
        dalston.logging.reset_context(request_id="req_123")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "req_123"
        assert ctx["_service_name"] == "gateway"

    def test_works_without_prior_configure(self):
        """reset_context works even if configure() was never called."""
        dalston.logging.reset_context(request_id="req_456")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["request_id"] == "req_456"
        assert "_service_name" not in ctx
