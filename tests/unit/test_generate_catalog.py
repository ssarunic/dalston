"""Tests for the catalog generation script.

M46: Updated after models moved to database. Catalog now only contains engines.
"""

from __future__ import annotations

import json

# Import the module functions directly
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from generate_catalog import (
    derive_image_name,
    find_engine_id_yamls,
    generate_catalog,
    transform_engine_id_to_entry,
)


@pytest.fixture
def valid_engine_id_yaml() -> dict:
    """A valid engine_id YAML structure (engine.yaml with engine_id field)."""
    return {
        "schema_version": "1.1",
        "id": "test-engine",
        "engine_id": "test-engine_id",
        "stage": "transcribe",
        "name": "Test Runtime",
        "version": "1.2.0",
        "description": "A test engine_id for unit testing catalog generation.",
        "execution_profile": "venv",
        "container": {
            "gpu": "optional",
            "memory": "8G",
        },
        "capabilities": {
            "languages": ["en", "es"],
            "native_streaming": False,
            "word_timestamps": True,
            "max_audio_duration": 7200,
        },
        "input": {
            "audio_formats": ["wav"],
            "sample_rate": 16000,
            "channels": 1,
        },
        "hardware": {
            "min_vram_gb": 4,
            "recommended_gpu": ["t4", "a10g"],
            "supports_cpu": True,
            "min_ram_gb": 8,
        },
        "performance": {
            "rtf_gpu": 0.05,
            "rtf_cpu": 0.8,
            "warm_start_latency_ms": 50,
        },
        "hf_compat": {
            "pipeline_tag": "automatic-speech-recognition",
            "library_name": "ctranslate2",
            "license": "mit",
        },
    }


class TestDeriveImageName:
    """Tests for derive_image_name function."""

    def test_batch_engine_image(self) -> None:
        """Batch engine should have stt-{stage} prefix."""
        result = derive_image_name("faster-whisper", "transcribe", "1.0.0")
        assert result == "dalston/stt-transcribe-faster-whisper:1.0.0"

    def test_diarize_stage(self) -> None:
        """Other stages should work correctly."""
        result = derive_image_name("pyannote-4.0", "diarize", "1.0.0")
        assert result == "dalston/stt-diarize-pyannote-4.0:1.0.0"


class TestTransformRuntimeToEntry:
    """Tests for transform_engine_id_to_entry function."""

    def test_engine_id_transform(self, valid_engine_id_yaml: dict) -> None:
        """Runtime should be transformed correctly."""
        entry = transform_engine_id_to_entry(valid_engine_id_yaml, Path("test.yaml"))

        assert entry["id"] == "test-engine_id"
        assert entry["engine_id"] == "test-engine"
        assert entry["name"] == "Test Runtime"
        assert entry["version"] == "1.2.0"
        assert entry["stage"] == "transcribe"
        assert entry["execution_profile"] == "venv"
        assert entry["capabilities"]["stages"] == ["transcribe"]
        assert entry["capabilities"]["supports_word_timestamps"] is True
        assert entry["hardware"]["gpu_required"] is False
        assert entry["hardware"]["gpu_optional"] is True
        assert entry["hardware"]["min_vram_gb"] == 4
        assert entry["performance"]["rtf_gpu"] == 0.05

    def test_execution_profile_defaults_to_container(
        self, valid_engine_id_yaml: dict
    ) -> None:
        """Missing execution_profile should default to container."""
        valid_engine_id_yaml.pop("execution_profile")
        entry = transform_engine_id_to_entry(valid_engine_id_yaml, Path("test.yaml"))
        assert entry["execution_profile"] == "container"

    def test_non_container_profile_allows_missing_container(
        self, valid_engine_id_yaml: dict
    ) -> None:
        """Venv/inproc profiles may omit container metadata."""
        valid_engine_id_yaml["execution_profile"] = "venv"
        valid_engine_id_yaml.pop("container")

        entry = transform_engine_id_to_entry(valid_engine_id_yaml, Path("test.yaml"))

        assert entry["execution_profile"] == "venv"
        assert entry["hardware"]["gpu_required"] is False
        assert entry["hardware"]["gpu_optional"] is True
        assert entry["hardware"]["memory"] is None

    def test_gpu_required(self, valid_engine_id_yaml: dict) -> None:
        """GPU required should be detected correctly."""
        valid_engine_id_yaml["container"]["gpu"] = "required"
        entry = transform_engine_id_to_entry(valid_engine_id_yaml, Path("test.yaml"))
        assert entry["hardware"]["gpu_required"] is True
        assert entry["hardware"]["gpu_optional"] is False


class TestFindRuntimeYamls:
    """Tests for find_engine_id_yamls function."""

    def test_finds_engine_id_yamls(self) -> None:
        """Should find engine.yaml files in the engines directory."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if engines_dir.exists():
            files = find_engine_id_yamls(engines_dir)
            # Should find engine.yaml files
            assert len(files) >= 5
            # All files should be engine.yaml
            assert all(f.name == "engine.yaml" for f in files)

    def test_empty_for_nonexistent_dir(self) -> None:
        """Should return empty list for non-existent directory."""
        files = find_engine_id_yamls(Path("/nonexistent/path"))
        assert files == []


class TestGenerateCatalog:
    """Tests for generate_catalog function."""

    def test_generate_catalog_from_dirs(self) -> None:
        """Should generate catalog from engines directory."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)

        assert "generated_at" in catalog
        assert catalog["schema_version"] == "3.0"  # M46: models removed
        assert catalog["engine_count"] >= 5
        assert "engines" in catalog

    def test_catalog_structure(self) -> None:
        """Catalog should have correct structure."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)

        # Check a known engine_id/engine
        if "nemo" in catalog["engines"]:
            engine = catalog["engines"]["nemo"]
            assert engine["id"] == "nemo"
            assert engine["stage"] == "transcribe"
            assert "capabilities" in engine
            assert "hardware" in engine
            assert "performance" in engine

    def test_catalog_excludes_legacy_merge_engine_id(self) -> None:
        """Merge engine_id is deprecated and should not appear in generated catalog."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)
        assert "final-merger" not in catalog["engines"]
        assert all(engine["stage"] != "merge" for engine in catalog["engines"].values())

    def test_generate_catalog_empty_dir_raises(self) -> None:
        """Should raise error for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="No engine.yaml files found"):
                generate_catalog(Path(tmpdir))

    def test_generate_catalog_with_temp_engine(
        self, valid_engine_id_yaml: dict
    ) -> None:
        """Should generate catalog from temp directory with engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine_dir = Path(tmpdir) / "transcribe" / "test-engine"
            engine_dir.mkdir(parents=True)
            with open(engine_dir / "engine.yaml", "w") as f:
                yaml.dump(valid_engine_id_yaml, f)

            catalog = generate_catalog(Path(tmpdir))

            assert catalog["engine_count"] == 1
            assert "test-engine_id" in catalog["engines"]
            assert catalog["engines"]["test-engine_id"]["version"] == "1.2.0"
            assert catalog["engines"]["test-engine_id"]["execution_profile"] == "venv"


class TestCatalogJsonOutput:
    """Tests for catalog JSON output format."""

    def test_catalog_is_valid_json(self) -> None:
        """Generated catalog should be valid JSON."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)
        json_str = json.dumps(catalog)
        reparsed = json.loads(json_str)

        assert reparsed == catalog

    def test_generated_catalog_file_exists(self) -> None:
        """The generated catalog file should exist after running the script."""
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "dalston"
            / "orchestrator"
            / "generated_catalog.json"
        )
        assert catalog_path.exists(), "Run 'python scripts/generate_catalog.py' first"

        with open(catalog_path) as f:
            catalog = json.load(f)

        assert catalog["engine_count"] >= 5
