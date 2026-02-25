"""Integration tests for audio URL transcription API endpoints."""

from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.transcription import router as transcription_router
from dalston.gateway.services.audio_probe import AudioMetadata
from dalston.gateway.services.audio_url import DownloadedAudio
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope


class TestAudioUrlTranscription:
    """Tests for audio URL transcription endpoint."""

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
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.audio_url_max_size_gb = 3.0
        settings.audio_url_timeout_seconds = 300
        settings.s3_bucket = "test-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = None
        return settings

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        return redis

    @pytest.fixture
    def mock_jobs_service(self):
        """Create mock jobs service."""
        service = AsyncMock()
        # Mock job creation
        mock_job = MagicMock()
        mock_job.id = UUID("00000000-0000-0000-0000-000000789abc")
        mock_job.status = "pending"
        mock_job.created_at = datetime.now(UTC)
        service.create_job.return_value = mock_job
        return service

    @pytest.fixture
    def mock_rate_limiter(self):
        """Create mock rate limiter."""
        limiter = AsyncMock()
        return limiter

    @pytest.fixture
    def mock_audit_service(self):
        """Create mock audit service."""
        service = AsyncMock()
        return service

    @pytest.fixture
    def app(
        self,
        mock_api_key,
        mock_settings,
        mock_db,
        mock_redis,
        mock_jobs_service,
        mock_rate_limiter,
        mock_audit_service,
    ):
        """Create FastAPI test app with mocked dependencies."""
        from dalston.gateway.dependencies import (
            get_audit_service,
            get_db,
            get_jobs_service,
            get_rate_limiter,
            get_redis,
            get_settings,
            require_auth,
        )

        app = FastAPI()
        app.include_router(transcription_router, prefix="/v1")

        # Override dependencies
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_settings] = lambda: mock_settings
        app.dependency_overrides[get_jobs_service] = lambda: mock_jobs_service
        app.dependency_overrides[get_rate_limiter] = lambda: mock_rate_limiter
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service

        # Override auth dependency (base require_auth that all auth deps use)
        app.dependency_overrides[require_auth] = lambda: mock_api_key

        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    def test_transcription_requires_file_or_url(self, client):
        """Test that either file or audio_url is required."""
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "whisper-large-v3"},
        )

        assert response.status_code == 400
        assert (
            "Either 'file' or 'audio_url' must be provided" in response.json()["detail"]
        )

    def test_transcription_rejects_both_file_and_url(self, client):
        """Test that providing both file and audio_url is rejected."""
        # Create a small audio-like file
        audio_content = b"RIFF" + b"\x00" * 100  # Fake WAV header

        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "https://example.com/audio.mp3",
            },
            files={"file": ("test.wav", BytesIO(audio_content), "audio/wav")},
        )

        assert response.status_code == 400
        assert "not both" in response.json()["detail"]

    @patch("dalston.gateway.services.ingestion.download_audio_from_url")
    @patch("dalston.gateway.services.ingestion.probe_audio")
    @patch("dalston.gateway.services.storage.get_s3_client")
    @patch("dalston.common.events.publish_job_created")
    def test_transcription_with_audio_url_success(
        self,
        mock_publish,
        mock_s3_client,
        mock_probe,
        mock_download,
        client,
        mock_jobs_service,
    ):
        """Test successful transcription submission with audio URL."""
        # Mock download
        mock_download.return_value = DownloadedAudio(
            content=b"fake audio content",
            filename="audio.mp3",
            content_type="audio/mpeg",
            size=18,
        )

        # Mock audio probe
        mock_probe.return_value = AudioMetadata(
            format="mp3",
            duration=60.0,
            sample_rate=44100,
            channels=2,
            bit_depth=16,
        )

        # Mock S3 client
        mock_s3 = AsyncMock()
        mock_s3_client.return_value.__aenter__.return_value = mock_s3

        # Submit with audio_url
        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "https://example.com/audio.mp3",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["status"] == "pending"

        # Verify download was called with correct URL
        mock_download.assert_called_once()
        call_args = mock_download.call_args
        assert call_args.kwargs["url"] == "https://example.com/audio.mp3"

    @patch("dalston.gateway.services.ingestion.download_audio_from_url")
    def test_transcription_with_invalid_url(self, mock_download, client):
        """Test transcription with invalid URL returns proper error."""
        from dalston.gateway.services.audio_url import InvalidUrlError

        mock_download.side_effect = InvalidUrlError("Invalid URL format")

        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "not-a-valid-url",
            },
        )

        assert response.status_code == 400
        assert "Invalid URL" in response.json()["detail"]

    @patch("dalston.gateway.services.ingestion.download_audio_from_url")
    def test_transcription_with_download_error(self, mock_download, client):
        """Test transcription with download failure returns proper error."""
        from dalston.gateway.services.audio_url import DownloadError

        mock_download.side_effect = DownloadError("HTTP 404: File not found")

        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "https://example.com/missing.mp3",
            },
        )

        assert response.status_code == 400
        assert "HTTP 404" in response.json()["detail"]

    @patch("dalston.gateway.services.ingestion.download_audio_from_url")
    def test_transcription_with_file_too_large(self, mock_download, client):
        """Test transcription with oversized file returns proper error."""
        from dalston.gateway.services.audio_url import FileTooLargeError

        mock_download.side_effect = FileTooLargeError(
            "File too large: 5.0 GB. Maximum: 3.0 GB"
        )

        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "https://example.com/huge.mp3",
            },
        )

        assert response.status_code == 400
        assert "File too large" in response.json()["detail"]

    @patch("dalston.gateway.services.ingestion.download_audio_from_url")
    def test_transcription_with_unsupported_content_type(self, mock_download, client):
        """Test transcription with non-audio file returns proper error."""
        from dalston.gateway.services.audio_url import UnsupportedContentTypeError

        mock_download.side_effect = UnsupportedContentTypeError(
            "Unsupported content type: text/html"
        )

        response = client.post(
            "/v1/audio/transcriptions",
            data={
                "model": "whisper-large-v3",
                "audio_url": "https://example.com/page.html",
            },
        )

        assert response.status_code == 400
        assert "Unsupported content type" in response.json()["detail"]
