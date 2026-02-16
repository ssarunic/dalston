"""Tests for the catalog generation script."""

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
    find_engine_yamls,
    generate_catalog,
    transform_engine_to_catalog_entry,
)


@pytest.fixture
def valid_batch_engine_yaml() -> dict:
    """A valid batch engine YAML structure."""
    return {
        "schema_version": "1.1",
        "id": "test-engine",
        "stage": "transcribe",
        "name": "Test Engine",
        "version": "1.2.0",
        "description": "A test engine for unit testing catalog generation.",
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
            "max_concurrent_jobs": 4,
            "warm_start_latency_ms": 50,
        },
        "hf_compat": {
            "pipeline_tag": "automatic-speech-recognition",
            "library_name": "ctranslate2",
            "license": "mit",
        },
    }


@pytest.fixture
def valid_realtime_engine_yaml() -> dict:
    """A valid realtime engine YAML structure."""
    return {
        "schema_version": "1.1",
        "id": "test-streaming",
        "type": "realtime",
        "name": "Test Streaming",
        "version": "1.0.0",
        "description": "A test realtime engine.",
        "container": {
            "gpu": "required",
            "memory": "12G",
        },
        "capabilities": {
            "languages": ["all"],
            "streaming": True,
            "max_sessions": 4,
        },
        "hardware": {
            "min_vram_gb": 6,
            "supports_cpu": False,
        },
        "performance": {
            "rtf_gpu": 0.1,
            "rtf_cpu": None,
            "max_concurrent_jobs": 4,
        },
    }


class TestDeriveImageName:
    """Tests for derive_image_name function."""

    def test_batch_engine_image(self) -> None:
        """Batch engine should have stt-batch prefix."""
        result = derive_image_name("faster-whisper", "transcribe", "1.0.0")
        assert result == "dalston/stt-batch-transcribe-faster-whisper:1.0.0"

    def test_realtime_engine_image(self) -> None:
        """Realtime engine should have stt-rt prefix."""
        result = derive_image_name("whisper-streaming", "realtime", "1.0.0")
        assert result == "dalston/stt-rt-whisper-streaming:1.0.0"

    def test_diarize_stage(self) -> None:
        """Other stages should work correctly."""
        result = derive_image_name("pyannote-3.1", "diarize", "1.0.0")
        assert result == "dalston/stt-batch-diarize-pyannote-3.1:1.0.0"


class TestTransformEngineToCatalogEntry:
    """Tests for transform_engine_to_catalog_entry function."""

    def test_batch_engine_transform(self, valid_batch_engine_yaml: dict) -> None:
        """Batch engine should be transformed correctly."""
        entry = transform_engine_to_catalog_entry(
            valid_batch_engine_yaml, Path("test.yaml")
        )

        assert entry["id"] == "test-engine"
        assert entry["name"] == "Test Engine"
        assert entry["version"] == "1.2.0"
        assert entry["stage"] == "transcribe"
        assert entry["type"] is None
        assert entry["capabilities"]["stages"] == ["transcribe"]
        assert entry["capabilities"]["languages"] == ["en", "es"]
        assert entry["capabilities"]["supports_word_timestamps"] is True
        assert entry["hardware"]["gpu_required"] is False
        assert entry["hardware"]["gpu_optional"] is True
        assert entry["hardware"]["min_vram_gb"] == 4
        assert entry["performance"]["rtf_gpu"] == 0.05
        assert "hf_compat" in entry

    def test_realtime_engine_transform(self, valid_realtime_engine_yaml: dict) -> None:
        """Realtime engine should be transformed correctly."""
        entry = transform_engine_to_catalog_entry(
            valid_realtime_engine_yaml, Path("test.yaml")
        )

        assert entry["id"] == "test-streaming"
        assert entry["type"] == "realtime"
        assert entry["stage"] is None
        assert entry["capabilities"]["stages"] == []
        assert entry["capabilities"]["languages"] is None  # "all" -> None
        assert entry["hardware"]["gpu_required"] is True
        assert entry["hardware"]["supports_cpu"] is False

    def test_all_languages_converted_to_null(
        self, valid_batch_engine_yaml: dict
    ) -> None:
        """Languages ['all'] should be converted to None."""
        valid_batch_engine_yaml["capabilities"]["languages"] = ["all"]
        entry = transform_engine_to_catalog_entry(
            valid_batch_engine_yaml, Path("test.yaml")
        )
        assert entry["capabilities"]["languages"] is None


class TestFindEngineYamls:
    """Tests for find_engine_yamls function."""

    def test_finds_engine_yamls(self) -> None:
        """Should find engine.yaml files in the engines directory."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if engines_dir.exists():
            files = find_engine_yamls(engines_dir)
            assert len(files) >= 11
            assert all(f.name == "engine.yaml" for f in files)

    def test_empty_for_nonexistent_dir(self) -> None:
        """Should return empty list for non-existent directory."""
        files = find_engine_yamls(Path("/nonexistent/path"))
        assert files == []


class TestGenerateCatalog:
    """Tests for generate_catalog function."""

    def test_generate_catalog_from_engines_dir(self) -> None:
        """Should generate catalog from engines directory."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)

        assert "generated_at" in catalog
        assert catalog["schema_version"] == "1.1"
        assert catalog["engine_count"] >= 11
        assert "engines" in catalog
        assert len(catalog["engines"]) >= 11

    def test_catalog_structure(self) -> None:
        """Catalog should have correct structure."""
        engines_dir = Path(__file__).parent.parent.parent / "engines"
        if not engines_dir.exists():
            pytest.skip("Engines directory not found")

        catalog = generate_catalog(engines_dir)

        # Check a known engine
        if "faster-whisper" in catalog["engines"]:
            engine = catalog["engines"]["faster-whisper"]
            assert engine["id"] == "faster-whisper"
            assert engine["stage"] == "transcribe"
            assert "capabilities" in engine
            assert "hardware" in engine
            assert "performance" in engine

    def test_generate_catalog_empty_dir_raises(self) -> None:
        """Should raise error for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="No engine.yaml files found"):
                generate_catalog(Path(tmpdir))

    def test_generate_catalog_with_temp_engine(
        self, valid_batch_engine_yaml: dict
    ) -> None:
        """Should generate catalog from temp directory with engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine_dir = Path(tmpdir) / "transcribe" / "test-engine"
            engine_dir.mkdir(parents=True)
            with open(engine_dir / "engine.yaml", "w") as f:
                yaml.dump(valid_batch_engine_yaml, f)

            catalog = generate_catalog(Path(tmpdir))

            assert catalog["engine_count"] == 1
            assert "test-engine" in catalog["engines"]
            assert catalog["engines"]["test-engine"]["version"] == "1.2.0"


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

        assert catalog["engine_count"] >= 11
