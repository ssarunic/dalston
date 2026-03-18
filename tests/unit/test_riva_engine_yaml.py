"""Tests for Riva unified engine YAML definition.

Verifies that engine.yaml for the unified Riva engine
parses correctly and contains the expected capabilities.
"""

from __future__ import annotations

from pathlib import Path

import yaml

UNIFIED_YAML = Path("engines/stt-transcribe/riva/engine.yaml")


class TestUnifiedEngineYaml:
    """Verify unified engine.yaml parses correctly."""

    def test_yaml_parses(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_required_fields(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)

        assert data["engine_id"] == "riva"
        assert data["stage"] == "transcribe"
        assert data["mode"] == "realtime"

    def test_capabilities(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)

        caps = data["capabilities"]
        assert caps["word_timestamps"] is True
        assert caps["native_streaming"] is True
        assert caps["max_concurrency"] == 8
        assert (
            "vocabulary" not in caps
        )  # vocabulary support is reported by code, not YAML
        assert caps["max_audio_duration"] == 7200

    def test_input_format(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)

        input_cfg = data["input"]
        assert "wav" in input_cfg["audio_formats"]
        assert "pcm_s16le" in input_cfg["audio_formats"]
        assert input_cfg["sample_rate"] == 16000
        assert input_cfg["channels"] == 1

    def test_gpu_not_required(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)

        container = data.get("container", {})
        assert container.get("gpu") == "none"

    def test_schema_version(self) -> None:
        with open(UNIFIED_YAML) as f:
            data = yaml.safe_load(f)

        assert data["schema_version"] == "1.1"
