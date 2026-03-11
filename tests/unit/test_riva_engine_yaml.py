"""Tests for Riva engine YAML definitions.

Verifies that engine.yaml files for both batch and RT Riva engines
parse correctly and produce valid EngineCapabilities.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BATCH_YAML = Path("engines/stt-transcribe/riva/engine.yaml")
RT_YAML = Path("engines/stt-rt/riva/engine.yaml")


class TestBatchEngineYaml:
    """Verify batch engine.yaml parses correctly."""

    def test_yaml_parses(self) -> None:
        with open(BATCH_YAML) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_required_fields(self) -> None:
        with open(BATCH_YAML) as f:
            data = yaml.safe_load(f)

        assert data["engine_id"] == "riva"
        assert data["stage"] == "transcribe"
        assert data["version"] == "1.0.0"

    def test_capabilities(self) -> None:
        with open(BATCH_YAML) as f:
            data = yaml.safe_load(f)

        caps = data["capabilities"]
        assert "en" in caps["languages"]
        assert caps["word_timestamps"] is True
        assert caps["streaming"] is False
        assert caps["max_audio_duration"] == 7200

    def test_gpu_not_required(self) -> None:
        with open(BATCH_YAML) as f:
            data = yaml.safe_load(f)

        container = data.get("container", {})
        assert container.get("gpu") == "none"

    def test_schema_version(self) -> None:
        with open(BATCH_YAML) as f:
            data = yaml.safe_load(f)

        assert data["schema_version"] == "1.1"


class TestRtEngineYaml:
    """Verify RT engine.yaml parses correctly."""

    def test_yaml_parses(self) -> None:
        with open(RT_YAML) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_required_fields(self) -> None:
        with open(RT_YAML) as f:
            data = yaml.safe_load(f)

        assert data["engine_id"] == "riva"
        assert data["stage"] == "transcribe"
        assert data["mode"] == "realtime"
        assert data["version"] == "1.0.0"

    def test_capabilities(self) -> None:
        with open(RT_YAML) as f:
            data = yaml.safe_load(f)

        caps = data["capabilities"]
        assert "en" in caps["languages"]
        assert caps["word_timestamps"] is True
        assert caps["streaming"] is True
        assert caps["max_concurrency"] == 8
        assert caps["supports_vocabulary"] is False

    def test_input_format(self) -> None:
        with open(RT_YAML) as f:
            data = yaml.safe_load(f)

        input_cfg = data["input"]
        assert "wav" in input_cfg["audio_formats"]
        assert input_cfg["sample_rate"] == 16000
        assert input_cfg["channels"] == 1

    def test_gpu_not_required(self) -> None:
        with open(RT_YAML) as f:
            data = yaml.safe_load(f)

        container = data.get("container", {})
        assert container.get("gpu") == "none"


class TestLanguageParity:
    """Both engines should support the same languages."""

    def test_same_languages(self) -> None:
        with open(BATCH_YAML) as f:
            batch = yaml.safe_load(f)
        with open(RT_YAML) as f:
            rt = yaml.safe_load(f)

        assert sorted(batch["capabilities"]["languages"]) == sorted(
            rt["capabilities"]["languages"]
        )
