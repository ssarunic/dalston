"""Tests for dalston.common.engine_yaml shared helpers."""

from __future__ import annotations

from unittest.mock import patch

from dalston.common.engine_yaml import (
    generate_instance_id,
    is_port_in_use,
    parse_engine_capabilities,
)


class TestParseEngineCapabilities:
    """Tests for parse_engine_capabilities()."""

    def test_minimal_card(self):
        card = {"engine_id": "test-engine", "version": "1.0"}
        result = parse_engine_capabilities(card)
        assert result["engine_id"] == "test-engine"
        assert result["version"] == "1.0"
        assert result["stages"] == []
        assert result["gpu_required"] is False

    def test_falls_back_to_default_engine_id(self):
        card = {"version": "1.0"}
        result = parse_engine_capabilities(card, default_engine_id="my-engine")
        assert result["engine_id"] == "my-engine"

    def test_card_engine_id_takes_precedence(self):
        card = {"engine_id": "from-yaml", "version": "1.0"}
        result = parse_engine_capabilities(card, default_engine_id="from-caller")
        assert result["engine_id"] == "from-yaml"

    def test_stage_field_becomes_stages_list(self):
        card = {"engine_id": "e", "version": "1.0", "stage": "transcribe"}
        result = parse_engine_capabilities(card)
        assert result["stages"] == ["transcribe"]

    def test_default_stages_used_when_no_stage(self):
        card = {"engine_id": "e", "version": "1.0"}
        result = parse_engine_capabilities(card, default_stages=["transcribe"])
        assert result["stages"] == ["transcribe"]

    def test_gpu_required_from_container_gpu_field(self):
        card = {
            "engine_id": "e",
            "version": "1.0",
            "container": {"gpu": "required"},
        }
        result = parse_engine_capabilities(card)
        assert result["gpu_required"] is True

    def test_gpu_not_required_when_supports_cpu(self):
        card = {
            "engine_id": "e",
            "version": "1.0",
            "hardware": {"min_vram_gb": 4, "supports_cpu": True},
        }
        result = parse_engine_capabilities(card)
        assert result["gpu_required"] is False

    def test_max_concurrency_from_card(self):
        card = {
            "engine_id": "e",
            "version": "1.0",
            "capabilities": {"max_concurrency": 8},
        }
        result = parse_engine_capabilities(card)
        assert result["max_concurrency"] == 8

    def test_max_concurrency_fallback(self):
        card = {"engine_id": "e", "version": "1.0"}
        result = parse_engine_capabilities(card, max_concurrency=4)
        assert result["max_concurrency"] == 4

    def test_vocabulary_support_included_when_provided(self):
        card = {"engine_id": "e", "version": "1.0"}
        result = parse_engine_capabilities(card, vocabulary_support="mock")
        assert result["vocabulary_support"] == "mock"

    def test_vocabulary_support_absent_when_none(self):
        card = {"engine_id": "e", "version": "1.0"}
        result = parse_engine_capabilities(card)
        assert "vocabulary_support" not in result

    def test_native_streaming_from_card(self):
        card = {
            "engine_id": "e",
            "version": "1.0",
            "capabilities": {"native_streaming": True},
        }
        result = parse_engine_capabilities(card)
        assert result["supports_native_streaming"] is True

    def test_native_streaming_fallback(self):
        card = {"engine_id": "e", "version": "1.0"}
        result = parse_engine_capabilities(card, supports_native_streaming=True)
        assert result["supports_native_streaming"] is True


class TestGenerateInstanceId:
    """Tests for generate_instance_id()."""

    def test_uses_worker_id_env(self):
        with patch.dict("os.environ", {"DALSTON_WORKER_ID": "abcdef123456789"}):
            result = generate_instance_id("nemo")
        assert result == "nemo-abcdef123456"

    def test_falls_back_to_instance_env(self):
        with patch.dict(
            "os.environ",
            {"DALSTON_INSTANCE": "myworker12345"},
            clear=False,
        ):
            # Remove WORKER_ID if present
            import os

            os.environ.pop("DALSTON_WORKER_ID", None)
            result = generate_instance_id("onnx")
        assert result == "onnx-myworker12345"[:17]  # "onnx-" + 12 chars

    def test_generates_uuid_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            result = generate_instance_id("fw")
        assert result.startswith("fw-")
        suffix = result.removeprefix("fw-")
        assert len(suffix) == 12
        int(suffix, 16)  # valid hex

    def test_infix_inserted(self):
        with patch.dict("os.environ", {"DALSTON_WORKER_ID": "abcdef123456789"}):
            result = generate_instance_id("onnx", infix="rt")
        assert result == "onnx-rt-abcdef123456"

    def test_no_infix_when_empty(self):
        with patch.dict("os.environ", {"DALSTON_WORKER_ID": "abcdef123456789"}):
            result = generate_instance_id("nemo", infix="")
        assert result == "nemo-abcdef123456"


class TestIsPortInUse:
    """Tests for is_port_in_use()."""

    def test_unused_port_returns_false(self):
        assert is_port_in_use(59999) is False

    def test_used_port_returns_true(self):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            assert is_port_in_use(port) is True
        finally:
            s.close()
