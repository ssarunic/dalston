"""Integration tests for OpenAI-compatible API endpoints.

Tests the /v1/audio/transcriptions endpoint in OpenAI mode (model=whisper-1, etc.)
and the /v1/audio/translations endpoint.
"""

from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.common.models import JobStatus
from dalston.gateway.api.v1.openai_audio import (
    format_openai_response,
    is_openai_model,
    map_openai_model,
)
from dalston.gateway.api.v1.openai_translation import router as translation_router
from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.services.audio_probe import AudioMetadata
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.ingestion import AudioIngestionService, IngestedAudio
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RateLimitResult, RedisRateLimiter


class TestOpenAIModelDetection:
    """Tests for OpenAI model detection and mapping."""

    def test_is_openai_model_returns_true_for_whisper_1(self):
        """Test whisper-1 is detected as OpenAI model."""
        assert is_openai_model("whisper-1") is True

    def test_is_openai_model_returns_true_for_gpt_4o_transcribe(self):
        """Test gpt-4o-transcribe is detected as OpenAI model."""
        assert is_openai_model("gpt-4o-transcribe") is True

    def test_is_openai_model_returns_true_for_gpt_4o_mini_transcribe(self):
        """Test gpt-4o-mini-transcribe is detected as OpenAI model."""
        assert is_openai_model("gpt-4o-mini-transcribe") is True

    def test_is_openai_model_returns_false_for_dalston_models(self):
        """Test Dalston models are not detected as OpenAI models."""
        assert is_openai_model("auto") is False
        assert is_openai_model("faster-whisper-base") is False
        assert is_openai_model("parakeet-0.6b") is False
        assert is_openai_model("whisper-large-v3") is False

    def test_is_openai_model_handles_future_models(self):
        """Test pattern matching for future OpenAI model releases."""
        # Future whisper versions
        assert is_openai_model("whisper-2") is True
        assert is_openai_model("whisper-3") is True
        # Future GPT audio models
        assert is_openai_model("gpt-5o-transcribe") is True
        assert is_openai_model("gpt-5o-mini-transcribe") is True
        # Audio preview models
        assert is_openai_model("gpt-4-audio") is True
        assert is_openai_model("gpt-4-audio-preview") is True

    def test_map_openai_model_whisper_1(self):
        """Test whisper-1 maps to faster-whisper."""
        engine_id = map_openai_model("whisper-1")
        assert engine_id == "faster-whisper"

    def test_map_openai_model_gpt_4o_transcribe(self):
        """Test gpt-4o-transcribe maps to faster-whisper."""
        engine_id = map_openai_model("gpt-4o-transcribe")
        assert engine_id == "faster-whisper"


class TestOpenAIResponseFormatting:
    """Tests for OpenAI response formatting."""

    @pytest.fixture
    def sample_transcript(self):
        """Sample Dalston transcript for formatting tests."""
        return {
            "text": "Hello world, how are you?",
            "metadata": {
                "language": "en",
                "duration": 2.5,
            },
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.5,
                    "text": "Hello world, how are you?",
                    "speaker": "speaker_0",
                    "words": [
                        {"text": "Hello", "start": 0.0, "end": 0.3},
                        {"text": "world", "start": 0.3, "end": 0.6},
                        {"text": "how", "start": 1.0, "end": 1.2},
                        {"text": "are", "start": 1.2, "end": 1.4},
                        {"text": "you", "start": 1.4, "end": 1.8},
                    ],
                }
            ],
        }

    @pytest.fixture
    def mock_export_service(self):
        from dalston.gateway.services.export import ExportService

        service = MagicMock(spec=ExportService)
        return service

    def test_format_json_response(self, sample_transcript, mock_export_service):
        """Test json response format returns simple text."""
        result = format_openai_response(
            sample_transcript, "json", None, mock_export_service
        )
        assert result == {"text": "Hello world, how are you?"}

    def test_format_verbose_json_response(self, sample_transcript, mock_export_service):
        """Test verbose_json response format includes segments."""
        result = format_openai_response(
            sample_transcript, "verbose_json", None, mock_export_service
        )
        assert result["task"] == "transcribe"
        assert result["language"] == "en"
        assert result["duration"] == 2.5
        assert result["text"] == "Hello world, how are you?"
        assert len(result["segments"]) == 1
        assert result["words"] is None  # No timestamp_granularities specified

    def test_format_verbose_json_with_word_timestamps(
        self, sample_transcript, mock_export_service
    ):
        """Test verbose_json with word timestamp granularity."""
        result = format_openai_response(
            sample_transcript, "verbose_json", ["word"], mock_export_service
        )
        assert result["words"] is not None
        assert len(result["words"]) == 5
        assert result["words"][0]["word"] == "Hello"
        assert result["words"][0]["start"] == 0.0
        assert result["words"][0]["end"] == 0.3


class TestOpenAITranscriptionEndpoint:
    """Tests for POST /v1/audio/transcriptions with OpenAI models."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

    @pytest.fixture
    def mock_storage_service(self):
        from dalston.gateway.services.storage import StorageService

        service = AsyncMock(spec=StorageService)
        service.upload_audio = AsyncMock(return_value="s3://bucket/jobs/test/audio.mp3")
        service.get_transcript = AsyncMock(
            return_value={
                "text": "Hello world",
                "metadata": {"language": "en", "duration": 1.0},
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "Hello world"},
                ],
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
        settings.retention_default_days = 30
        return settings

    @pytest.fixture
    def mock_ingestion_service(self):
        service = AsyncMock(spec=AudioIngestionService)
        service.ingest.return_value = IngestedAudio(
            content=b"fake audio content",
            filename="test.mp3",
            metadata=AudioMetadata(
                format="mp3",
                duration=1.0,
                sample_rate=44100,
                channels=2,
                bit_depth=16,
            ),
        )
        return service

    @pytest.fixture
    def mock_rate_limiter(self):
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
    def mock_export_service(self):
        from dalston.gateway.services.export import ExportService

        return MagicMock(spec=ExportService)

    @pytest.fixture
    def api_key(self):
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
        mock_export_service,
        api_key,
    ):
        from dalston.gateway.dependencies import (
            get_db,
            get_export_service,
            get_ingestion_service,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[get_export_service] = lambda: mock_export_service
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @pytest.fixture
    def mock_job(self, api_key):
        job = MagicMock()
        job.id = uuid4()
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.COMPLETED.value
        job.error = None
        job.created_at = datetime.now(UTC)
        return job

    def test_dalston_native_mode_returns_201(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test Dalston native model returns 201 with job ID."""
        mock_job.status = JobStatus.PENDING.value
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.transcription.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )

            with patch(
                "dalston.gateway.api.v1.transcription.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                audio_content = b"fake audio content"
                files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                response = client.post(
                    "/v1/audio/transcriptions",
                    files=files,
                    data={"model": "auto"},
                )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["status"] == "pending"

    def test_openai_mode_detects_whisper_1(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test OpenAI mode is detected when model=whisper-1."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.transcription.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )
            MockStorage.return_value.get_transcript = AsyncMock(
                return_value={
                    "text": "Hello world",
                    "metadata": {"language": "en"},
                }
            )

            with patch(
                "dalston.gateway.api.v1.transcription.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                with patch("asyncio.sleep", return_value=None):
                    audio_content = b"fake audio content"
                    files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                    response = client.post(
                        "/v1/audio/transcriptions",
                        files=files,
                        data={"model": "whisper-1"},
                    )

        # OpenAI mode returns 200 with transcript
        assert response.status_code == 200
        data = response.json()
        assert "text" in data
        assert data["text"] == "Hello world"

    def test_openai_mode_verbose_json_format(
        self,
        client,
        mock_jobs_service,
        mock_job,
    ):
        """Test OpenAI mode with verbose_json response format."""
        mock_jobs_service.create_job.return_value = mock_job

        with patch(
            "dalston.gateway.api.v1.transcription.StorageService"
        ) as MockStorage:
            MockStorage.return_value.upload_audio = AsyncMock(
                return_value="s3://bucket/audio.mp3"
            )
            MockStorage.return_value.get_transcript = AsyncMock(
                return_value={
                    "text": "Hello world",
                    "metadata": {"language": "en", "duration": 1.0},
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": "Hello world"},
                    ],
                }
            )

            with patch(
                "dalston.gateway.api.v1.transcription.publish_job_created"
            ) as mock_publish:
                mock_publish.return_value = None

                with patch("asyncio.sleep", return_value=None):
                    audio_content = b"fake audio content"
                    files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

                    response = client.post(
                        "/v1/audio/transcriptions",
                        files=files,
                        data={
                            "model": "gpt-4o-transcribe",
                            "response_format": "verbose_json",
                        },
                    )

        assert response.status_code == 200
        data = response.json()
        assert data["task"] == "transcribe"
        assert data["language"] == "en"
        assert "segments" in data

    # NOTE: test_openai_mode_rejects_invalid_model was removed because:
    # OpenAI mode is only activated when model is a known OpenAI model ID (whisper-1, etc.)
    # Using model="invalid-model" goes through Dalston native mode, not OpenAI mode.
    # There's no way to trigger OpenAI validation with an invalid model since
    # invalid models don't activate OpenAI mode in the first place.


class TestOpenAITranslationEndpoint:
    """Tests for POST /v1/audio/translations endpoint."""

    @pytest.fixture
    def mock_jobs_service(self):
        return AsyncMock(spec=JobsService)

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
        service = AsyncMock(spec=AudioIngestionService)
        service.ingest.return_value = IngestedAudio(
            content=b"fake audio content",
            filename="test.mp3",
            metadata=AudioMetadata(
                format="mp3",
                duration=1.0,
                sample_rate=44100,
                channels=2,
                bit_depth=16,
            ),
        )
        return service

    @pytest.fixture
    def mock_rate_limiter(self):
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
    def mock_export_service(self):
        from dalston.gateway.services.export import ExportService

        return MagicMock(spec=ExportService)

    @pytest.fixture
    def api_key(self):
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
        mock_redis,
        mock_settings,
        mock_ingestion_service,
        mock_rate_limiter,
        mock_export_service,
        api_key,
    ):
        from dalston.gateway.dependencies import (
            get_db,
            get_export_service,
            get_ingestion_service,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )
        from dalston.gateway.middleware import setup_exception_handlers

        app = FastAPI()
        setup_exception_handlers(app)
        app.include_router(translation_router, prefix="/v1")

        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_ingestion_service] = lambda: mock_ingestion_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[get_export_service] = lambda: mock_export_service
        app.dependency_overrides[require_auth] = lambda: api_key

        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @pytest.fixture
    def mock_job(self, api_key):
        job = MagicMock()
        job.id = uuid4()
        job.tenant_id = api_key.tenant_id
        job.status = JobStatus.COMPLETED.value
        job.error = None
        return job

    def test_translation_only_accepts_whisper_1(self, client):
        """Test translation endpoint only accepts whisper-1 model."""
        audio_content = b"fake audio content"
        files = {"file": ("test.mp3", BytesIO(audio_content), "audio/mpeg")}

        response = client.post(
            "/v1/audio/translations",
            files=files,
            data={"model": "gpt-4o-transcribe"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "invalid_model"
