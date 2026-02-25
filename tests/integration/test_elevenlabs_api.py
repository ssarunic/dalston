"""Integration tests for ElevenLabs-compatible API endpoints.

Tests the /v1/speech-to-text/* API endpoints including sync transcription,
async mode, and transcript retrieval.
"""

from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.common.models import JobStatus
from dalston.gateway.api.v1.speech_to_text import router as speech_to_text_router
from dalston.gateway.services.audio_probe import AudioMetadata
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.ingestion import AudioIngestionService, IngestedAudio
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RateLimitResult, RedisRateLimiter
from dalston.gateway.services.storage import StorageService


class TestCreateTranscriptionEndpoint:
    """Tests for POST /v1/speech-to-text endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_storage_service(self):
        service = AsyncMock(spec=StorageService)
        service.upload_audio = AsyncMock(return_value="s3://bucket/jobs/test/audio.mp3")
        service.get_transcript = AsyncMock(
            return_value={
                "text": "Hello world",
                "words": [
                    {"text": "Hello", "start": 0.0, "end": 0.5},
                    {"text": "world", "start": 0.5, "end": 1.0},
                ],
                "metadata": {"language": "en", "language_confidence": 0.95},
            }
        )
        return service

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.publish = AsyncMock()
        return redis

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.fixture
    def mock_ingestion_service(self):
        """Create mock ingestion service that returns valid ingested audio."""
        service = AsyncMock(spec=AudioIngestionService)
        service.ingest.return_value = IngestedAudio(
            content=b"fake audio content",
            filename="test.mp3",
            metadata=AudioMetadata(
                format="mp3",
                duration=60.0,
                sample_rate=44100,
                channels=2,
                bit_depth=16,
            ),
        )
        return service

    @pytest.fixture
    def mock_rate_limiter(self):
        """Create mock rate limiter that always allows requests."""
        limiter = AsyncMock(spec=RedisRateLimiter)
        limiter.check_request_rate.return_value = RateLimitResult(
            allowed=True, limit=100, remaining=99, reset_seconds=60
        )
        limiter.check_concurrent_jobs.return_value = RateLimitResult(
            allowed=True, limit=10, remaining=9, reset_seconds=0
        )
        limiter.increment_concurrent_jobs.return_value = None
        return limiter

    @pytest.fixture
    def api_key(self):
        """Create a mock API key with jobs:write scope."""
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_test12",
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
    def app(
        self,
        mock_jobs_service,
        mock_storage_service,
        mock_redis,
        mock_settings,
        mock_ingestion_service,
        mock_rate_limiter,
        api_key,
    ):
        from dalston.gateway.dependencies import (
            get_db,
            get_ingestion_service,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(speech_to_text_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @pytest.fixture
    def mock_job(self, api_key):
        """Create a mock job object."""
        job = MagicMock()
        job.id = uuid4()
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.PENDING.value
        job.audio_uri = "pending"
        job.error = None
        return job

    def test_create_transcription_async_mode(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test async transcription returns immediately with job ID."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.speech_to_text.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )

            with patch(
                "dalston.gateway.api.v1.speech_to_text.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                # Create a test audio file
                audio_content = b"fake audio content"
                files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                response = client.post(
                    "/v1/speech-to-text",
                    files=files,
                    data={"model_id": "scribe_v1", "webhook": "true"},
                )

        assert response.status_code == 200
        data = response.json()
        assert "transcription_id" in data
        assert data["message"] == "Request processed successfully"

    def test_create_transcription_any_model_accepted(
        self, client, mock_jobs_service, mock_job
    ):
        """Test that any model_id is accepted (validated at orchestrator level)."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.speech_to_text.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )

            with patch(
                "dalston.gateway.api.v1.speech_to_text.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                audio_content = b"fake audio content"
                files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                # Any model_id should be accepted - validation happens at orchestrator
                response = client.post(
                    "/v1/speech-to-text",
                    files=files,
                    data={"model_id": "custom_engine", "webhook": "true"},
                )

        # Should succeed - model validation is done at orchestrator level
        assert response.status_code == 200

    def test_create_transcription_missing_filename(self, client):
        """Test that file without filename returns error."""
        audio_content = b"fake audio content"
        # Send file without filename
        files = {"file": ("", BytesIO(audio_content), "audio/mpeg")}

        response = client.post(
            "/v1/speech-to-text",
            files=files,
            data={"model_id": "scribe_v1"},
        )

        # FastAPI may return 400 or 422 for validation errors
        assert response.status_code in (400, 422)

    def test_create_transcription_model_mapping(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test that ElevenLabs model IDs are mapped correctly."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.speech_to_text.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )

            with patch("dalston.gateway.api.v1.speech_to_text.publish_job_created"):
                audio_content = b"fake audio content"
                files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                # Test scribe_v1 (ElevenLabs model) uses auto model selection
                response = client.post(
                    "/v1/speech-to-text",
                    files=files,
                    data={"model_id": "scribe_v1", "webhook": "true"},
                )

                assert response.status_code == 200

                # Check the create_job call used the mapped model
                call_kwargs = mock_jobs_service.create_job.call_args.kwargs
                # The parameters should contain the resolved model
                assert "parameters" in call_kwargs

    def test_create_transcription_with_diarization(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test that diarization parameter is passed correctly."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.speech_to_text.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )

            with patch("dalston.gateway.api.v1.speech_to_text.publish_job_created"):
                audio_content = b"fake audio content"
                files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                response = client.post(
                    "/v1/speech-to-text",
                    files=files,
                    data={
                        "model_id": "scribe_v1",
                        "webhook": "true",
                        "diarize": "true",
                        "num_speakers": "2",
                    },
                )

                assert response.status_code == 200

                call_kwargs = mock_jobs_service.create_job.call_args.kwargs
                params = call_kwargs["parameters"]
                assert params["speaker_detection"] == "diarize"
                assert params["num_speakers"] == 2


class TestGetTranscriptEndpoint:
    """Tests for GET /v1/speech-to-text/transcripts/{id} endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.fixture
    def api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_test12",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_settings, api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_jobs_service,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(speech_to_text_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_get_transcript_completed(self, client, mock_jobs_service, api_key):
        """Test retrieving a completed transcript."""
        job_id = uuid4()
        job = MagicMock()
        job.id = job_id
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.COMPLETED.value
        job.error = None

        mock_jobs_service.get_job.return_value = job

        with patch(
            "dalston.gateway.api.v1.speech_to_text.StorageService"
        ) as MockStorage:
            MockStorage.return_value.get_transcript = AsyncMock(
                return_value={
                    "text": "Hello world",
                    "words": [
                        {"text": "Hello", "start": 0.0, "end": 0.5},
                        {"text": "world", "start": 0.5, "end": 1.0},
                    ],
                    "metadata": {"language": "en", "language_confidence": 0.95},
                }
            )

            response = client.get(f"/v1/speech-to-text/transcripts/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["text"] == "Hello world"
        assert data["transcription_id"] == str(job_id)
        assert data["language_code"] == "en"
        assert len(data["words"]) == 2

    def test_get_transcript_processing(self, client, mock_jobs_service, api_key):
        """Test retrieving a still-processing transcript."""
        job_id = uuid4()
        job = MagicMock()
        job.id = job_id
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.RUNNING.value
        job.error = None

        mock_jobs_service.get_job.return_value = job

        response = client.get(f"/v1/speech-to-text/transcripts/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["transcription_id"] == str(job_id)

    def test_get_transcript_failed(self, client, mock_jobs_service, api_key):
        """Test retrieving a failed transcript."""
        job_id = uuid4()
        job = MagicMock()
        job.id = job_id
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.FAILED.value
        job.error = "Transcription engine error"

        mock_jobs_service.get_job.return_value = job

        response = client.get(f"/v1/speech-to-text/transcripts/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["message"] == "Transcription engine error"

    def test_get_transcript_not_found(self, client, mock_jobs_service):
        """Test 404 when transcript doesn't exist."""
        mock_jobs_service.get_job.return_value = None

        response = client.get(f"/v1/speech-to-text/transcripts/{uuid4()}")

        assert response.status_code == 404


class TestExportTranscriptEndpoint:
    """Tests for GET /v1/speech-to-text/transcripts/{id}/export/{format} endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_export_service(self):
        from dalston.gateway.services.export import ExportService

        service = MagicMock(spec=ExportService)
        service.validate_format.return_value = "srt"
        return service

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.fixture
    def api_key(self):
        return APIKey(
            id=UUID("12345678-1234-1234-1234-123456789abc"),
            key_hash="abc123def456",
            prefix="dk_test12",
            name="Test Key",
            tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
            scopes=[Scope.JOBS_READ],
            rate_limit=None,
            created_at=datetime.now(UTC),
            last_used_at=None,
            expires_at=DEFAULT_EXPIRES_AT,
            revoked_at=None,
        )

    @pytest.fixture
    def app(self, mock_jobs_service, mock_export_service, mock_settings, api_key):
        from dalston.gateway.dependencies import (
            get_db,
            get_export_service,
            get_jobs_service,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(speech_to_text_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_export_service] = lambda: mock_export_service
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_export_not_found(self, client, mock_jobs_service):
        """Test 404 when transcript doesn't exist."""
        mock_jobs_service.get_job.return_value = None

        response = client.get(f"/v1/speech-to-text/transcripts/{uuid4()}/export/srt")

        assert response.status_code == 404

    def test_export_not_completed(self, client, mock_jobs_service, api_key):
        """Test 400 when transcript is not completed."""
        job_id = uuid4()
        job = MagicMock()
        job.id = job_id
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.RUNNING.value

        mock_jobs_service.get_job.return_value = job

        response = client.get(f"/v1/speech-to-text/transcripts/{job_id}/export/srt")

        assert response.status_code == 400
        assert "not completed" in response.json()["detail"].lower()
