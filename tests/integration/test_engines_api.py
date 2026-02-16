"""Integration tests for engine discovery API endpoints (M30).

Tests:
- GET /v1/engines - List all engines with status
- GET /v1/engines/capabilities - Aggregate capabilities
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.engine_sdk.types import EngineCapabilities
from dalston.gateway.api.v1.engines import router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.registry import BatchEngineState


@pytest.fixture
def mock_catalog() -> EngineCatalog:
    """Create a mock engine catalog."""
    entries = {
        "faster-whisper": CatalogEntry(
            engine_id="faster-whisper",
            image="dalston/stt-batch-transcribe-whisper:1.0.0",
            capabilities=EngineCapabilities(
                engine_id="faster-whisper",
                version="1.0.0",
                stages=["transcribe"],
                languages=None,  # All languages
                supports_word_timestamps=True,
                supports_streaming=False,
                gpu_required=False,
                gpu_vram_mb=4096,
                supports_cpu=True,
                min_ram_gb=8,
                rtf_gpu=0.05,
                rtf_cpu=0.8,
                max_concurrent_jobs=4,
            ),
        ),
        "parakeet": CatalogEntry(
            engine_id="parakeet",
            image="dalston/stt-batch-transcribe-parakeet:1.0.0",
            capabilities=EngineCapabilities(
                engine_id="parakeet",
                version="1.0.0",
                stages=["transcribe"],
                languages=["en"],
                supports_word_timestamps=True,
                supports_streaming=True,
                gpu_required=True,
                gpu_vram_mb=6144,
                supports_cpu=False,
                rtf_gpu=0.02,
                max_concurrent_jobs=8,
            ),
        ),
        "pyannote-3.1": CatalogEntry(
            engine_id="pyannote-3.1",
            image="dalston/stt-batch-diarize-pyannote-3.1:1.0.0",
            capabilities=EngineCapabilities(
                engine_id="pyannote-3.1",
                version="1.0.0",
                stages=["diarize"],
                languages=None,
                supports_word_timestamps=False,
                supports_streaming=False,
                gpu_required=True,
                gpu_vram_mb=2048,
                supports_cpu=True,
                rtf_gpu=0.1,
                rtf_cpu=0.5,
            ),
        ),
    }
    return EngineCatalog(entries)


@pytest.fixture
def mock_running_engines() -> list[BatchEngineState]:
    """Create mock running engine states."""
    return [
        BatchEngineState(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
            status="idle",
            current_task=None,
            last_heartbeat=datetime.now(UTC),
            registered_at=datetime.now(UTC),
            capabilities=EngineCapabilities(
                engine_id="faster-whisper",
                version="1.0.0",
                stages=["transcribe"],
                languages=None,
                supports_word_timestamps=True,
            ),
        ),
    ]


@pytest.fixture
def mock_api_key():
    """Create a mock API key for authentication."""
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        key_hash="test_hash",
        prefix="dk_test",
        name="Test Key",
        scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return AsyncMock()


@pytest.fixture
def app(mock_api_key, mock_redis):
    """Create test FastAPI application with mocked dependencies."""
    from dalston.gateway.dependencies import get_redis, require_auth

    test_app = FastAPI()
    test_app.include_router(router, prefix="/v1")

    # Override dependencies
    test_app.dependency_overrides[require_auth] = lambda: mock_api_key
    test_app.dependency_overrides[get_redis] = lambda: mock_redis

    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


class TestListEngines:
    """Tests for GET /v1/engines endpoint."""

    def test_list_engines_returns_all_from_catalog(
        self, client, mock_catalog, mock_running_engines
    ):
        """Should return all engines from catalog with status."""
        mock_registry = AsyncMock()
        mock_registry.get_engines = AsyncMock(return_value=mock_running_engines)

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=mock_registry,
            ),
        ):
            response = client.get("/v1/engines")

            assert response.status_code == 200
            data = response.json()

            assert "engines" in data
            assert "total" in data
            assert data["total"] == 3

            engines_by_id = {e["id"]: e for e in data["engines"]}

            # faster-whisper is running
            assert engines_by_id["faster-whisper"]["status"] == "running"
            assert engines_by_id["faster-whisper"]["stage"] == "transcribe"

            # parakeet is not running (available)
            assert engines_by_id["parakeet"]["status"] == "available"

            # pyannote is not running (available)
            assert engines_by_id["pyannote-3.1"]["status"] == "available"

    def test_engine_includes_capabilities(
        self, client, mock_catalog, mock_running_engines
    ):
        """Engine response should include capabilities."""
        mock_registry = AsyncMock()
        mock_registry.get_engines = AsyncMock(return_value=mock_running_engines)

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=mock_registry,
            ),
        ):
            response = client.get("/v1/engines")

            data = response.json()
            engines_by_id = {e["id"]: e for e in data["engines"]}

            fw = engines_by_id["faster-whisper"]
            assert fw["capabilities"]["languages"] is None
            assert fw["capabilities"]["supports_word_timestamps"] is True
            assert fw["hardware"]["supports_cpu"] is True
            assert fw["performance"]["rtf_gpu"] == 0.05

    def test_engine_includes_hardware_and_performance(
        self, client, mock_catalog, mock_running_engines
    ):
        """Engine response should include hardware and performance info."""
        mock_registry = AsyncMock()
        mock_registry.get_engines = AsyncMock(return_value=mock_running_engines)

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=mock_registry,
            ),
        ):
            response = client.get("/v1/engines")

            data = response.json()
            engines_by_id = {e["id"]: e for e in data["engines"]}

            parakeet = engines_by_id["parakeet"]
            assert parakeet["hardware"]["gpu_required"] is True
            assert parakeet["hardware"]["supports_cpu"] is False
            assert parakeet["performance"]["rtf_gpu"] == 0.02


class TestGetCapabilities:
    """Tests for GET /v1/engines/capabilities endpoint."""

    def test_aggregate_capabilities_from_running_engines(
        self, client, mock_catalog, mock_running_engines
    ):
        """Should aggregate capabilities from running engines only."""
        mock_registry = AsyncMock()
        mock_registry.get_engines = AsyncMock(return_value=mock_running_engines)

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=mock_registry,
            ),
        ):
            response = client.get("/v1/engines/capabilities")

            assert response.status_code == 200
            data = response.json()

            assert "languages" in data
            assert "stages" in data
            assert "supported_formats" in data

            # Only faster-whisper is running, which supports all languages
            assert "*" in data["languages"]

            # Only transcribe stage should be available
            assert "transcribe" in data["stages"]
            assert data["stages"]["transcribe"]["supports_word_timestamps"] is True

    def test_capabilities_includes_stage_details(
        self, client, mock_catalog, mock_running_engines
    ):
        """Stage capabilities should include engine list and features."""
        mock_registry = AsyncMock()
        mock_registry.get_engines = AsyncMock(return_value=mock_running_engines)

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=mock_registry,
            ),
        ):
            response = client.get("/v1/engines/capabilities")

            data = response.json()

            transcribe_caps = data["stages"]["transcribe"]
            assert "engines" in transcribe_caps
            assert "faster-whisper" in transcribe_caps["engines"]
            assert transcribe_caps["supports_word_timestamps"] is True

    def test_empty_capabilities_when_no_engines_running(self, client, mock_catalog):
        """Should return empty stages when no engines are running."""
        empty_registry = AsyncMock()
        empty_registry.get_engines = AsyncMock(return_value=[])

        with (
            patch(
                "dalston.gateway.api.v1.engines.get_catalog", return_value=mock_catalog
            ),
            patch(
                "dalston.gateway.api.v1.engines.BatchEngineRegistry",
                return_value=empty_registry,
            ),
        ):
            response = client.get("/v1/engines/capabilities")

            assert response.status_code == 200
            data = response.json()

            assert data["stages"] == {}
            assert data["languages"] == []
