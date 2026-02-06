"""Integration tests for Parakeet model selection via API.

Tests that Parakeet models are correctly exposed via the API and that
jobs submitted with Parakeet models are configured correctly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.common.models import MODEL_REGISTRY, resolve_model
from dalston.gateway.api.v1 import models as models_router
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope


class TestParakeetModelRegistry:
    """Tests for Parakeet in the model registry."""

    def test_parakeet_06b_in_registry(self):
        """Test that parakeet-0.6b is in the model registry."""
        assert "parakeet-0.6b" in MODEL_REGISTRY

    def test_parakeet_11b_in_registry(self):
        """Test that parakeet-1.1b is in the model registry."""
        assert "parakeet-1.1b" in MODEL_REGISTRY

    def test_parakeet_alias_resolves(self):
        """Test that 'parakeet' alias resolves to parakeet-0.6b."""
        model = resolve_model("parakeet")
        assert model.id == "parakeet-0.6b"

    def test_parakeet_model_properties(self):
        """Test Parakeet model properties are correct."""
        model = resolve_model("parakeet-0.6b")

        assert model.engine == "parakeet"
        assert model.engine_model == "nvidia/parakeet-rnnt-0.6b"
        assert model.languages == 1  # English only
        assert model.streaming is True
        assert model.word_timestamps is True
        assert model.tier == "fast"

    def test_parakeet_11b_is_balanced_tier(self):
        """Test that parakeet-1.1b is balanced tier."""
        model = resolve_model("parakeet-1.1b")
        assert model.tier == "balanced"


class TestModelsAPIParakeet:
    """Tests for Parakeet in the /v1/models API."""

    @pytest.fixture
    def mock_api_key(self):
        """Create a mock API key."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_api_key):
        from dalston.gateway.dependencies import require_auth

        app = FastAPI()
        app.include_router(models_router.router, prefix="/v1")
        app.dependency_overrides[require_auth] = lambda: mock_api_key
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_list_models_includes_parakeet(self, client):
        """Test that GET /v1/models includes Parakeet models."""
        response = client.get("/v1/models")

        assert response.status_code == 200
        data = response.json()
        model_ids = [m["id"] for m in data["data"]]

        assert "parakeet-0.6b" in model_ids
        assert "parakeet-1.1b" in model_ids

    def test_get_parakeet_model_details(self, client):
        """Test that GET /v1/models/parakeet-0.6b returns correct details."""
        response = client.get("/v1/models/parakeet-0.6b")

        assert response.status_code == 200
        data = response.json()

        assert data["id"] == "parakeet-0.6b"
        assert data["engine"] == "parakeet"
        assert data["capabilities"]["streaming"] is True
        assert data["capabilities"]["word_timestamps"] is True

    def test_get_parakeet_model_shows_english_only(self, client):
        """Test that Parakeet model shows English-only language support."""
        response = client.get("/v1/models/parakeet-0.6b")

        assert response.status_code == 200
        data = response.json()

        assert data["capabilities"]["languages"] == 1  # English only


class TestTranscriptionAPIParakeet:
    """Tests for Parakeet model selection in transcription API."""

    @pytest.fixture
    def mock_api_key(self):
        """Create a mock API key with jobs:write scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_abc1234",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def mock_jobs_service(self):
        """Create mock jobs service."""
        service = AsyncMock()
        mock_job = MagicMock()
        mock_job.id = uuid4()
        mock_job.status = "pending"
        mock_job.created_at = datetime.now(UTC)
        service.create_job.return_value = mock_job
        return service

    @pytest.fixture
    def mock_storage_service(self):
        """Create mock storage service."""
        storage = MagicMock()
        storage.upload_audio = AsyncMock(return_value="s3://bucket/audio.wav")
        return storage

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    def test_model_resolution_parakeet_06b(self):
        """Test that parakeet-0.6b resolves to correct engine config."""
        model = resolve_model("parakeet-0.6b")

        assert model.engine == "parakeet"
        assert model.engine_model == "nvidia/parakeet-rnnt-0.6b"

    def test_model_resolution_parakeet_alias(self):
        """Test that 'parakeet' alias resolves correctly."""
        model = resolve_model("parakeet")

        assert model.id == "parakeet-0.6b"
        assert model.engine == "parakeet"

    def test_invalid_parakeet_model_raises_error(self):
        """Test that invalid Parakeet model ID raises ValueError."""
        with pytest.raises(ValueError, match="Unknown model"):
            resolve_model("parakeet-invalid")


class TestParakeetDagIntegration:
    """Tests for Parakeet integration with job DAG."""

    def test_parakeet_job_skips_align_stage(self):
        """Test that a job with Parakeet model skips align stage."""
        from dalston.orchestrator.dag import build_task_dag

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"

        # Simulate parameters as they would come from gateway after model resolution
        model = resolve_model("parakeet-0.6b")
        parameters = {
            "model": model.id,
            "engine_transcribe": model.engine,
            "transcribe_config": {"model": model.engine_model},
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        # Parakeet produces native word timestamps, so align should be skipped
        assert "align" not in stages
        assert "transcribe" in stages

    def test_parakeet_transcribe_task_has_correct_engine(self):
        """Test that transcribe task uses parakeet engine."""
        from dalston.orchestrator.dag import build_task_dag

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"

        model = resolve_model("parakeet-0.6b")
        parameters = {
            "engine_transcribe": model.engine,
            "transcribe_config": {"model": model.engine_model},
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)
        transcribe_task = next(t for t in tasks if t.stage == "transcribe")

        assert transcribe_task.engine_id == "parakeet"
        assert transcribe_task.config["model"] == "nvidia/parakeet-rnnt-0.6b"

    def test_parakeet_with_diarization_still_skips_align(self):
        """Test that Parakeet + diarization skips align but includes diarize."""
        from dalston.orchestrator.dag import build_task_dag

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"

        model = resolve_model("parakeet-0.6b")
        parameters = {
            "engine_transcribe": model.engine,
            "transcribe_config": {"model": model.engine_model},
            "speaker_detection": "diarize",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "diarize" in stages
        assert "transcribe" in stages
