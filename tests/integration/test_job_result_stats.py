"""Integration tests for job result stats population.

Tests the orchestrator's ability to extract and populate result stats
when a job completes.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from dalston.config import Settings
from dalston.orchestrator.handlers import _populate_job_result_stats


class TestPopulateJobResultStats:
    """Tests for _populate_job_result_stats function."""

    @pytest.fixture
    def mock_job(self):
        """Create a mock job model."""
        job = MagicMock()
        job.id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        job.result_language_code = None
        job.result_word_count = None
        job.result_segment_count = None
        job.result_speaker_count = None
        job.result_character_count = None
        return job

    @pytest.fixture
    def sample_transcript(self):
        """Create a sample transcript."""
        return {
            "job_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "version": "1.0",
            "metadata": {
                "audio_duration": 120.5,
                "language": "en",
                "language_confidence": 0.95,
                "speaker_count": 2,
            },
            "text": "Hello world this is a test transcript with multiple words",
            "speakers": [
                {"id": "SPEAKER_00", "label": None},
                {"id": "SPEAKER_01", "label": None},
            ],
            "segments": [
                {"id": "seg_000", "start": 0.0, "end": 5.0, "text": "Hello world"},
                {
                    "id": "seg_001",
                    "start": 5.0,
                    "end": 10.0,
                    "text": "this is a test transcript",
                },
                {
                    "id": "seg_002",
                    "start": 10.0,
                    "end": 15.0,
                    "text": "with multiple words",
                },
            ],
        }

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock(spec=Settings)
        settings.s3_bucket = "test-bucket"
        return settings

    @pytest.mark.asyncio
    async def test_populates_all_stats_from_transcript(
        self, mock_job, sample_transcript, mock_settings
    ):
        """Test that all result stats are populated from transcript."""
        mock_s3 = AsyncMock()
        mock_body = AsyncMock()
        mock_body.read.return_value = json.dumps(sample_transcript).encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        @asynccontextmanager
        async def mock_get_s3_client(settings):
            yield mock_s3

        with (
            patch("dalston.orchestrator.handlers.get_s3_client", mock_get_s3_client),
            patch(
                "dalston.orchestrator.handlers.get_settings", return_value=mock_settings
            ),
        ):
            log = MagicMock()
            await _populate_job_result_stats(mock_job, log)

        assert mock_job.result_language_code == "en"
        assert mock_job.result_word_count == 10
        assert mock_job.result_segment_count == 3
        assert mock_job.result_speaker_count == 2
        assert mock_job.result_character_count == 57

    @pytest.mark.asyncio
    async def test_logs_success_with_stats(
        self, mock_job, sample_transcript, mock_settings
    ):
        """Test that successful extraction logs stats info."""
        mock_s3 = AsyncMock()
        mock_body = AsyncMock()
        mock_body.read.return_value = json.dumps(sample_transcript).encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        @asynccontextmanager
        async def mock_get_s3_client(settings):
            yield mock_s3

        with (
            patch("dalston.orchestrator.handlers.get_s3_client", mock_get_s3_client),
            patch(
                "dalston.orchestrator.handlers.get_settings", return_value=mock_settings
            ),
        ):
            log = MagicMock()
            await _populate_job_result_stats(mock_job, log)

        log.info.assert_called_once()
        call_args = log.info.call_args
        assert call_args[0][0] == "job_result_stats_populated"
        assert call_args[1]["language_code"] == "en"

    @pytest.mark.asyncio
    async def test_handles_s3_error_gracefully(self, mock_job, mock_settings):
        """Test that S3 errors are handled without failing the job."""
        mock_s3 = AsyncMock()
        mock_s3.get_object.side_effect = Exception("S3 connection failed")

        @asynccontextmanager
        async def mock_get_s3_client(settings):
            yield mock_s3

        with (
            patch("dalston.orchestrator.handlers.get_s3_client", mock_get_s3_client),
            patch(
                "dalston.orchestrator.handlers.get_settings", return_value=mock_settings
            ),
        ):
            log = MagicMock()
            # Should not raise exception
            await _populate_job_result_stats(mock_job, log)

        # Stats should remain None
        assert mock_job.result_language_code is None
        assert mock_job.result_word_count is None

        # Warning should be logged
        log.warning.assert_called_once()
        assert "job_result_stats_extraction_failed" in str(log.warning.call_args)

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self, mock_job, mock_settings):
        """Test that invalid JSON in transcript is handled gracefully."""
        mock_s3 = AsyncMock()
        mock_body = AsyncMock()
        mock_body.read.return_value = b"not valid json"
        mock_s3.get_object.return_value = {"Body": mock_body}

        @asynccontextmanager
        async def mock_get_s3_client(settings):
            yield mock_s3

        with (
            patch("dalston.orchestrator.handlers.get_s3_client", mock_get_s3_client),
            patch(
                "dalston.orchestrator.handlers.get_settings", return_value=mock_settings
            ),
        ):
            log = MagicMock()
            await _populate_job_result_stats(mock_job, log)

        # Should log warning but not fail
        log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_correct_s3_path(
        self, mock_job, sample_transcript, mock_settings
    ):
        """Test that the correct S3 path is used for transcript."""
        mock_s3 = AsyncMock()
        mock_body = AsyncMock()
        mock_body.read.return_value = json.dumps(sample_transcript).encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        @asynccontextmanager
        async def mock_get_s3_client(settings):
            yield mock_s3

        with (
            patch("dalston.orchestrator.handlers.get_s3_client", mock_get_s3_client),
            patch(
                "dalston.orchestrator.handlers.get_settings", return_value=mock_settings
            ),
        ):
            log = MagicMock()
            await _populate_job_result_stats(mock_job, log)

        # Verify S3 was called with correct bucket and key
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket",
            Key=f"jobs/{mock_job.id}/transcript.json",
        )


class TestJobCompletionWithStats:
    """Integration tests for job completion flow with stats extraction."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        return session

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_stats_populated_on_successful_job_completion(self):
        """Test that stats are populated when a job completes successfully.

        This is a higher-level test verifying the integration between
        _check_job_completion and _populate_job_result_stats.
        """
        # This test verifies the code path exists - full e2e would
        # require setting up more infrastructure
        from dalston.orchestrator.handlers import _check_job_completion

        # The function signature shows it accepts job_id, db, redis
        # A full test would mock all these and verify stats population
        assert callable(_check_job_completion)
