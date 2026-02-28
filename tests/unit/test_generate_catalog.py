"""Tests for the catalog generation script.

M36: Updated for runtime/model catalog structure.
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
    find_runtime_yamls,
    generate_catalog,
    transform_runtime_to_entry,
)


@pytest.fixture
def valid_runtime_yaml() -> dict:
    """A valid runtime YAML structure (engine.yaml with runtime field)."""
    return {
        "schema_version": "1.1",
        "id": "test-engine",
        "runtime": "test-runtime",
        "stage": "transcribe",
        "name": "Test Runtime",
        "version": "1.2.0",
        "description": "A test runtime for unit testing catalog generation.",
        "container": {
            "gpu": "optional",
            "memory": "8G",
        },
        "capabilities": {
            "languages": ["en", "es"],
            "streaming": False,
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


@pytest.fixture
def valid_model_yaml() -> dict:
    """A valid model YAML structure."""
    return {
        "id": "test-model-1b",
        "runtime": "test-runtime",
        "runtime_model_id": "org/test-model-1b",
        "name": "Test Model 1B",
        "source": "https://huggingface.co/org/test-model-1b",
        "size_gb": 2.5,
        "stage": "transcribe",
        "languages": ["en"],
        "capabilities": {
            "word_timestamps": True,
            "punctuation": True,
            "capitalization": True,
        },
        "hardware": {
            "min_vram_gb": 4,
            "supports_cpu": False,
        },
    }


class TestDeriveImageName:
    """Tests for derive_image_name function."""

    def test_batch_engine_image(self) -> None:
        """Batch engine should have stt-batch prefix."""
        result = derive_image_name("faster-whisper", "transcribe", "1.0.0")
        assert result == "dalston/stt-batch-transcribe-faster-whisper:1.0.0"

    def test_diarize_stage(self) -> None:
        """Other stages should work correctly."""
        result = derive_image_name("pyannote-3.1", "diarize", "1.0.0")
        assert result == "dalston/stt-batch-diarize-pyannote-3.1:1.0.0"


class TestTransformRuntimeToEntry:
    """Tests for transform_runtime_to_entry function."""

    def test_runtime_transform(self, valid_runtime_yaml: dict) -> None:
        """Runtime should be transformed correctly."""
        entry = transform_runtime_to_entry(valid_runtime_yaml, Path("test.yaml"))

        assert entry["id"] == "test-runtime"
        assert entry["engine_id"] == "test-engine"
        assert entry["name"] == "Test Runtime"
        assert entry["version"] == "1.2.0"
        assert entry["stage"] == "transcribe"
        assert entry["capabilities"]["stages"] == ["transcribe"]
        assert entry["capabilities"]["languages"] == ["en", "es"]
        assert entry["capabilities"]["supports_word_timestamps"] is True
        assert entry["hardware"]["gpu_required"] is False
        assert entry["hardware"]["gpu_optional"] is True
        assert entry["hardware"]["min_vram_gb"] == 4
        assert entry["performance"]["rtf_gpu"] == 0.05

    def test_all_languages_converted_to_null(self, valid_runtime_yaml: dict) -> None:
        """Languages ['all'] should be converted to None."""
        valid_runtime_yaml["capabilities"]["languages"] = ["all"]
        entry = transform_runtime_to_entry(valid_runtime_yaml, Path("test.yaml"))
        assert entry["capabilities"]["languages"] is None

    def test_gpu_required(self, valid_runtime_yaml: dict) -> None:
        """GPU required should be detected correctly."""
        valid_runtime_yaml["container"]["gpu"] = "required"
        entry = transform_runtime_to_entry(valid_runtime_yaml, Path("test.yaml"))
        assert entry["hardware"]["gpu_required"] is True
        assert entry["hardware"]["gpu_optional"] is False


class TestFindRuntimeYamls:
    """Tests for find_runtime_yamls function."""

    def test_finds_runtime_yamls(self) -> None:
        """Should find engine.yaml files in the engines directory."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if engines_dir.exists():
            files = find_runtime_yamls(engines_dir)
            # Should find engine.yaml files
            assert len(files) >= 5
            # All files should be engine.yaml
            assert all(f.name == "engine.yaml" for f in files)

    def test_empty_for_nonexistent_dir(self) -> None:
        """Should return empty list for non-existent directory."""
        files = find_runtime_yamls(Path("/nonexistent/path"))
        assert files == []


class TestGenerateCatalog:
    """Tests for generate_catalog function."""

    def test_generate_catalog_from_dirs(self) -> None:
        """Should generate catalog from engines and models directories."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        models_dir = Path(__file__).parent.parent.parent / "models"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir, models_dir)

        assert "generated_at" in catalog
        assert catalog["schema_version"] == "2.0"
        assert catalog["runtime_count"] >= 5
        assert "runtimes" in catalog
        assert "models" in catalog
        assert "engines" in catalog  # Backward compatibility

    def test_catalog_structure(self) -> None:
        """Catalog should have correct structure."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        models_dir = Path(__file__).parent.parent.parent / "models"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir, models_dir)

        # Check a known runtime
        if "nemo" in catalog["runtimes"]:
            runtime = catalog["runtimes"]["nemo"]
            assert runtime["id"] == "nemo"
            assert runtime["stage"] == "transcribe"
            assert "capabilities" in runtime
            assert "hardware" in runtime
            assert "performance" in runtime

    def test_generate_catalog_empty_dir_raises(self) -> None:
        """Should raise error for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="No engine.yaml files found"):
                generate_catalog(Path(tmpdir), Path(tmpdir))

    def test_generate_catalog_with_temp_engine(self, valid_runtime_yaml: dict) -> None:
        """Should generate catalog from temp directory with engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine_dir = Path(tmpdir) / "transcribe" / "test-engine"
            engine_dir.mkdir(parents=True)
            with open(engine_dir / "engine.yaml", "w") as f:
                yaml.dump(valid_runtime_yaml, f)

            # Create empty models dir
            models_dir = Path(tmpdir) / "models"
            models_dir.mkdir()

            catalog = generate_catalog(Path(tmpdir), models_dir)

            assert catalog["runtime_count"] == 1
            assert "test-runtime" in catalog["runtimes"]
            assert catalog["runtimes"]["test-runtime"]["version"] == "1.2.0"


class TestCatalogJsonOutput:
    """Tests for catalog JSON output format."""

    def test_catalog_is_valid_json(self) -> None:
        """Generated catalog should be valid JSON."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        models_dir = Path(__file__).parent.parent.parent / "models"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir, models_dir)
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

        assert catalog["runtime_count"] >= 5
