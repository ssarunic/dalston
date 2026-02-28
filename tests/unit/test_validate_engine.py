"""Tests for the engine.yaml validator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from dalston.tools.validate_engine import (
    find_all_engine_yamls,
    format_languages,
    load_schema,
    validate_engine,
)


@pytest.fixture
def schema() -> dict:
    """Load the engine schema."""
    schema_path = (
        Path(__file__).parent.parent.parent
        / "dalston"
        / "schemas"
        / "engine.schema.json"
    )
    return load_schema(schema_path)


@pytest.fixture
def valid_batch_engine() -> dict:
    """A valid batch engine configuration."""
    return {
        "schema_version": "1.0",
        "id": "test-engine",
        "stage": "transcribe",
        "name": "Test Engine",
        "version": "1.0.0",
        "description": "A test engine for unit tests that does nothing useful.",
        "container": {
            "gpu": "optional",
            "memory": "4G",
        },
        "capabilities": {
            "languages": ["en", "es"],
            "streaming": False,
            "word_timestamps": True,
        },
        "config_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }


@pytest.fixture
def valid_realtime_engine() -> dict:
    """A valid realtime engine configuration."""
    return {
        "schema_version": "1.0",
        "id": "test-streaming",
        "type": "realtime",
        "name": "Test Streaming Engine",
        "version": "1.0.0",
        "description": "A test realtime engine for unit tests that does nothing useful.",
        "container": {
            "gpu": "required",
            "memory": "8G",
        },
        "capabilities": {
            "languages": ["all"],
            "streaming": True,
            "max_sessions": 4,
        },
        "server": {
            "port": 9000,
            "protocol": "websocket",
            "path": "/session",
        },
    }


class TestValidateEngine:
    """Tests for validate_engine function."""

    def test_valid_batch_engine_passes(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Valid batch engine should pass validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is True
        assert result.engine_id == "test-engine"
        assert result.version == "1.0.0"
        assert result.schema_version == "1.0"
        assert result.stage_or_type == "transcribe"
        assert result.errors == []

    def test_valid_realtime_engine_passes(
        self, schema: dict, valid_realtime_engine: dict
    ) -> None:
        """Valid realtime engine should pass validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_realtime_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is True
        assert result.engine_id == "test-streaming"
        assert result.stage_or_type == "realtime"
        assert result.errors == []

    def test_missing_required_field_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Missing required field should fail validation."""
        del valid_batch_engine["id"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("id" in error for error in result.errors)

    def test_invalid_schema_version_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Invalid schema_version should fail validation."""
        valid_batch_engine["schema_version"] = "2.0"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("schema_version" in error for error in result.errors)

    def test_invalid_stage_fails(self, schema: dict, valid_batch_engine: dict) -> None:
        """Invalid stage value should fail validation."""
        valid_batch_engine["stage"] = "invalid_stage"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("stage" in error for error in result.errors)

    def test_invalid_id_pattern_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """ID not matching pattern should fail validation."""
        valid_batch_engine["id"] = "Invalid-ID"  # Uppercase not allowed

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("id" in error for error in result.errors)

    def test_id_with_dots_passes(self, schema: dict, valid_batch_engine: dict) -> None:
        """ID with dots should pass validation (e.g., pyannote-4.0)."""
        valid_batch_engine["id"] = "pyannote-4.0"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is True
        assert result.engine_id == "pyannote-4.0"

    def test_invalid_gpu_value_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Invalid GPU value should fail validation."""
        valid_batch_engine["container"]["gpu"] = (
            "yes"  # Should be required/optional/none
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("gpu" in error for error in result.errors)

    def test_invalid_version_format_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Version not matching semver should fail validation."""
        valid_batch_engine["version"] = "v1.0"  # Missing patch version

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("version" in error for error in result.errors)

    def test_file_not_found_fails(self, schema: dict) -> None:
        """Non-existent file should fail validation."""
        result = validate_engine(Path("/nonexistent/path/engine.yaml"), schema)

        assert result.valid is False
        assert any("not found" in error.lower() for error in result.errors)

    def test_invalid_yaml_fails(self, schema: dict) -> None:
        """Invalid YAML syntax should fail validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False
        assert any("yaml" in error.lower() for error in result.errors)

    def test_both_stage_and_type_fails(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Having both stage and type should fail validation."""
        valid_batch_engine["type"] = "realtime"  # Add type to batch engine

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is False

    def test_schema_version_1_1_accepted(
        self, schema: dict, valid_batch_engine: dict
    ) -> None:
        """Schema version 1.1 should be accepted."""
        valid_batch_engine["schema_version"] = "1.1"
        valid_batch_engine["hf_compat"] = {
            "pipeline_tag": "automatic-speech-recognition",
            "library_name": "ctranslate2",
            "license": "apache-2.0",
        }
        valid_batch_engine["hardware"] = {
            "min_vram_gb": 4,
            "supports_cpu": True,
        }
        valid_batch_engine["performance"] = {
            "rtf_gpu": 0.05,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_batch_engine, f)
            f.flush()
            result = validate_engine(Path(f.name), schema)

        assert result.valid is True
        assert result.schema_version == "1.1"


class TestFindAllEngineYamls:
    """Tests for find_all_engine_yamls function."""

    def test_finds_engine_yamls_in_subdirectories(self) -> None:
        """Should find engine metadata YAML files in nested directories."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if engines_dir.exists():
            files = find_all_engine_yamls(engines_dir)
            assert len(files) >= 1
            assert all(f.suffix == ".yaml" for f in files)
            for f in files:
                assert f.name == "engine.yaml" or f.parent.name == "variants"

    def test_skips_parent_engine_yaml_when_variants_exist(self) -> None:
        """Variant metadata should take precedence over parent engine.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "transcribe" / "example"
            variants = base / "variants"
            variants.mkdir(parents=True)

            (base / "engine.yaml").write_text("id: stale\n")
            (variants / "v1.yaml").write_text("id: example-v1\n")

            files = find_all_engine_yamls(Path(tmpdir))
            assert files == [variants / "v1.yaml"]

    def test_returns_empty_for_nonexistent_directory(self) -> None:
        """Should return empty list for non-existent directory."""
        files = find_all_engine_yamls(Path("/nonexistent/directory"))
        assert files == []


class TestFormatLanguages:
    """Tests for format_languages function."""

    def test_none_returns_none(self) -> None:
        """None should return 'none'."""
        assert format_languages(None) == "none"

    def test_all_returns_all(self) -> None:
        """['all'] should return 'all'."""
        assert format_languages(["all"]) == "all"

    def test_few_languages_returns_comma_separated(self) -> None:
        """Few languages should return comma-separated list."""
        assert format_languages(["en", "es", "fr"]) == "en, es, fr"

    def test_many_languages_truncates(self) -> None:
        """Many languages should be truncated."""
        result = format_languages(["en", "es", "fr", "de", "it", "pt", "nl"])
        assert result == "en, es, fr, de, +3 more"


class TestAllExistingEngines:
    """Test that all existing engine metadata in the repository passes validation."""

    def test_all_engines_valid(self, schema: dict) -> None:
        """All engine metadata YAML files in the engines directory should be valid."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        engine_files = find_all_engine_yamls(engines_dir)
        if not engine_files:
            pytest.skip("No engine metadata YAML files found")

        results = [validate_engine(path, schema) for path in engine_files]
        failed = [r for r in results if not r.valid]

        if failed:
            error_msg = "\n".join(f"{r.path}: {', '.join(r.errors)}" for r in failed)
            pytest.fail(f"Invalid engine metadata YAML files:\n{error_msg}")

        # We expect both engine.yaml and variants/*.yaml entries.
        assert len(results) >= 11, f"Expected at least 11 engines, found {len(results)}"
