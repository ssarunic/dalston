"""Unit tests for M29: Engine Catalog & Capabilities.

Tests for:
- EngineCapabilities schema
- get_capabilities() in base Engine class
- Registry with capabilities
- Catalog loading and validation
- Scheduler validation with capabilities
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from dalston.common.registry import UnifiedEngineRegistry
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineCapabilities, EngineInput, EngineOutput
from dalston.orchestrator.catalog import EngineCatalog
from dalston.orchestrator.exceptions import (
    CatalogValidationError,
    EngineCapabilityError,
)


class TestEngineCapabilities:
    """Tests for EngineCapabilities schema."""

    def test_create_minimal(self):
        """Test creating capabilities with minimal fields."""
        caps = EngineCapabilities(
            engine_id="test-engine",
            version="1.0.0",
            stages=["transcribe"],
        )

        assert caps.engine_id == "test-engine"
        assert caps.version == "1.0.0"
        assert caps.stages == ["transcribe"]
        assert caps.supports_word_timestamps is False
        assert caps.supports_streaming is False
        assert caps.gpu_required is False

    def test_create_full(self):
        """Test creating capabilities with all fields."""
        caps = EngineCapabilities(
            engine_id="parakeet",
            version="1.0.0",
            stages=["transcribe"],
            supports_word_timestamps=True,
            supports_streaming=False,
            model_variants=["tdt-110m", "rnnt-1.1b"],
            gpu_required=True,
            gpu_vram_mb=4000,
        )

        assert caps.engine_id == "parakeet"
        assert caps.supports_word_timestamps is True
        assert caps.model_variants == ["tdt-110m", "rnnt-1.1b"]
        assert caps.gpu_required is True
        assert caps.gpu_vram_mb == 4000

    def test_serialize_json(self):
        """Test serializing capabilities to JSON."""
        caps = EngineCapabilities(
            engine_id="test",
            version="1.0.0",
            stages=["transcribe"],
        )

        json_str = caps.model_dump_json()
        parsed = json.loads(json_str)

        assert parsed["engine_id"] == "test"

    def test_deserialize_json(self):
        """Test deserializing capabilities from JSON."""
        json_str = '{"engine_id":"test","version":"1.0.0","stages":["transcribe"]}'

        caps = EngineCapabilities.model_validate_json(json_str)

        assert caps.engine_id == "test"


class TestEngineGetCapabilities:
    """Tests for get_capabilities() in Engine base class."""

    def test_default_capabilities(self):
        """Test default capabilities from base Engine."""

        class TestEngine(Engine):
            def process(
                self, input: EngineInput, ctx: BatchTaskContext
            ) -> EngineOutput:
                pass

        engine = TestEngine()
        caps = engine.get_capabilities()

        assert isinstance(caps, EngineCapabilities)
        assert caps.version == "unknown"
        assert caps.stages == []

    def test_override_capabilities(self):
        """Test overriding get_capabilities in subclass."""

        class CustomEngine(Engine):
            def process(
                self, input: EngineInput, ctx: BatchTaskContext
            ) -> EngineOutput:
                pass

            def get_capabilities(self) -> EngineCapabilities:
                return EngineCapabilities(
                    engine_id="custom",
                    version="2.0.0",
                    stages=["transcribe"],
                    supports_word_timestamps=True,
                    gpu_required=True,
                    gpu_vram_mb=4000,
                )

        engine = CustomEngine()
        caps = engine.get_capabilities()

        assert caps.engine_id == "custom"
        assert caps.version == "2.0.0"
        assert caps.supports_word_timestamps is True


class TestEngineCatalog:
    """Tests for engine catalog loading and validation."""

    @pytest.fixture
    def catalog_json(self):
        """Create a test catalog JSON file (M30 format)."""
        content = {
            "generated_at": "2026-02-16T10:00:00Z",
            "schema_version": "1.1",
            "engines": {
                "parakeet": {
                    "id": "parakeet",
                    "stage": "transcribe",
                    "version": "1.0.0",
                    "image": "dalston/parakeet:latest",
                    "execution_profile": "inproc",
                    "capabilities": {
                        "stages": ["transcribe"],
                        "supports_word_timestamps": True,
                        "supports_streaming": False,
                    },
                    "hardware": {
                        "gpu_required": True,
                        "min_vram_gb": 4,
                        "supports_cpu": False,
                    },
                    "performance": {},
                },
                "faster-whisper": {
                    "id": "faster-whisper",
                    "stage": "transcribe",
                    "version": "1.0.0",
                    "image": "dalston/faster-whisper:latest",
                    "capabilities": {
                        "stages": ["transcribe"],
                        "supports_word_timestamps": False,
                        "supports_streaming": False,
                    },
                    "hardware": {
                        "gpu_required": True,
                        "min_vram_gb": 5,
                        "supports_cpu": True,
                    },
                    "performance": {},
                },
                "audio-prepare": {
                    "id": "audio-prepare",
                    "stage": "prepare",
                    "version": "1.0.0",
                    "image": "dalston/audio-prepare:latest",
                    "capabilities": {
                        "stages": ["prepare"],
                        "supports_word_timestamps": False,
                        "supports_streaming": False,
                    },
                    "hardware": {
                        "gpu_required": False,
                        "supports_cpu": True,
                    },
                    "performance": {},
                },
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(content, f)
            return Path(f.name)

    def test_load_catalog(self, catalog_json):
        """Test loading catalog from JSON."""
        catalog = EngineCatalog.load(catalog_json)

        assert len(catalog) == 3
        assert "parakeet" in catalog
        assert "faster-whisper" in catalog
        assert "audio-prepare" in catalog

    def test_get_engine(self, catalog_json):
        """Test getting a specific engine."""
        catalog = EngineCatalog.load(catalog_json)

        entry = catalog.get_engine("parakeet")

        assert entry is not None
        assert entry.engine_id == "parakeet"
        assert entry.image == "dalston/parakeet:latest"
        assert entry.execution_profile == "inproc"
        assert entry.capabilities.supports_word_timestamps is True

    def test_execution_profile_defaults_to_container(self, catalog_json):
        """Missing execution_profile should default to container."""
        catalog = EngineCatalog.load(catalog_json)

        entry = catalog.get_engine("faster-whisper")

        assert entry is not None
        assert entry.execution_profile == "container"

    def test_invalid_execution_profile_rejected(self, catalog_json):
        """Catalog entries must use a known execution profile."""
        raw = json.loads(catalog_json.read_text())
        raw["engines"]["faster-whisper"]["execution_profile"] = "bad-profile"
        catalog_json.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(ValueError, match="execution_profile must be one of"):
            EngineCatalog.load(catalog_json)

    def test_get_engine_not_found(self, catalog_json):
        """Test getting non-existent engine."""
        catalog = EngineCatalog.load(catalog_json)

        entry = catalog.get_engine("nonexistent")

        assert entry is None

    def test_get_engines_for_stage(self, catalog_json):
        """Test getting engines by stage."""
        catalog = EngineCatalog.load(catalog_json)

        transcribers = catalog.get_engines_for_stage("transcribe")

        assert len(transcribers) == 2
        engine_ids = {e.engine_id for e in transcribers}
        assert engine_ids == {"parakeet", "faster-whisper"}


class TestExceptions:
    """Tests for M29 exceptions."""

    def test_catalog_validation_error(self):
        """Test CatalogValidationError attributes."""
        error = CatalogValidationError(
            "No engine supports language 'xx'",
            stage="transcribe",
            language="xx",
        )

        assert str(error) == "No engine supports language 'xx'"
        assert error.stage == "transcribe"
        assert error.language == "xx"

    def test_engine_capability_error(self):
        """Test EngineCapabilityError attributes."""
        error = EngineCapabilityError(
            "Engine 'parakeet' does not support 'hr'",
            engine_id="parakeet",
            stage="transcribe",
            language="hr",
        )

        assert str(error) == "Engine 'parakeet' does not support 'hr'"
        assert error.engine_id == "parakeet"
        assert error.stage == "transcribe"
        assert error.language == "hr"


class TestServerRegistryCapabilities:
    """Tests for server-side registry parsing capabilities."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        return AsyncMock()

    @pytest.fixture
    def registry(self, mock_redis):
        """Create registry with mock Redis."""
        return UnifiedEngineRegistry(mock_redis)

    @pytest.mark.asyncio
    async def test_get_engine_with_capabilities(self, registry, mock_redis):
        """Test get_engine parses capabilities from Redis."""
        now = datetime.now(UTC).isoformat()
        caps_json = json.dumps(
            {
                "engine_id": "parakeet",
                "version": "1.0.0",
                "stages": ["transcribe"],
                "supports_word_timestamps": True,
                "supports_streaming": False,
                "model_variants": None,
                "gpu_required": True,
                "gpu_vram_mb": 4000,
            }
        )

        # Server registry now queries instance set first, then hgetall for instance
        mock_redis.smembers.return_value = {"parakeet-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "parakeet",
            "instance_id": "parakeet-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:parakeet",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
            "capabilities": caps_json,
        }

        engine = await registry.get_engine("parakeet")

        assert engine is not None
        assert engine.capabilities is not None
        assert engine.capabilities.engine_id == "parakeet"
        assert engine.capabilities.supports_word_timestamps is True
        assert engine.capabilities.gpu_vram_mb == 4000

    @pytest.mark.asyncio
    async def test_get_engine_without_capabilities(self, registry, mock_redis):
        """Test get_engine works without capabilities (M28 compat)."""
        now = datetime.now(UTC).isoformat()

        mock_redis.smembers.return_value = {"old-engine-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "old-engine",
            "instance_id": "old-engine-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:old-engine",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
            # No capabilities field
        }

        engine = await registry.get_engine("old-engine")

        assert engine is not None
        assert engine.capabilities is None

    @pytest.mark.asyncio
    async def test_get_engine_malformed_capabilities(self, registry, mock_redis):
        """Test get_engine handles malformed capabilities gracefully."""
        now = datetime.now(UTC).isoformat()

        mock_redis.smembers.return_value = {"bad-engine-abc123"}
        mock_redis.hgetall.return_value = {
            "engine_id": "bad-engine",
            "instance_id": "bad-engine-abc123",
            "stage": "transcribe",
            "stream_name": "dalston:stream:bad-engine",
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
            "capabilities": "not valid json{{{",
        }

        engine = await registry.get_engine("bad-engine")

        # Should still return engine state, just without capabilities
        assert engine is not None
        assert engine.capabilities is None
