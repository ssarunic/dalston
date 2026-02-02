"""Tests for batch transcription client."""

import json
from io import BytesIO
from uuid import UUID

import pytest

from dalston_sdk import (
    AsyncDalston,
    Dalston,
    Job,
    JobList,
    JobStatus,
    NotFoundError,
    SpeakerDetection,
    ValidationError,
)


@pytest.fixture
def client(httpx_mock):
    """Create a test client."""
    return Dalston(base_url="http://test")


@pytest.fixture
def async_client(httpx_mock):
    """Create an async test client."""
    return AsyncDalston(base_url="http://test")


class TestDalston:
    """Tests for synchronous Dalston client."""

    def test_transcribe_with_file_path(self, client, httpx_mock, tmp_path):
        """Test transcribe with file path."""
        # Create temp audio file
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio data")

        httpx_mock.add_response(
            method="POST",
            url="http://test/v1/audio/transcriptions",
            json={
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00Z",
            },
            status_code=201,
        )

        job = client.transcribe(file=str(audio_file))

        assert isinstance(job, Job)
        assert job.id == UUID("550e8400-e29b-41d4-a716-446655440000")
        assert job.status == JobStatus.PENDING

    def test_transcribe_with_file_object(self, client, httpx_mock):
        """Test transcribe with file-like object."""
        httpx_mock.add_response(
            method="POST",
            url="http://test/v1/audio/transcriptions",
            json={
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00Z",
            },
            status_code=201,
        )

        audio = BytesIO(b"fake audio data")
        audio.name = "test.mp3"

        job = client.transcribe(file=audio)

        assert job.status == JobStatus.PENDING

    def test_transcribe_with_speaker_detection(self, client, httpx_mock, tmp_path):
        """Test transcribe with speaker detection."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio data")

        httpx_mock.add_response(
            method="POST",
            url="http://test/v1/audio/transcriptions",
            json={
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00Z",
            },
            status_code=201,
        )

        job = client.transcribe(
            file=str(audio_file),
            speaker_detection=SpeakerDetection.DIARIZE,
            num_speakers=2,
        )

        # Check that request included speaker detection params
        request = httpx_mock.get_request()
        assert b"diarize" in request.content
        assert job.status == JobStatus.PENDING

    def test_transcribe_without_file_raises(self, client):
        """Test that transcribe without file or URL raises error."""
        with pytest.raises(ValidationError, match="Either file or audio_url"):
            client.transcribe()

    def test_get_job_completed(self, client, httpx_mock):
        """Test get_job with completed job."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        httpx_mock.add_response(
            method="GET",
            url=f"http://test/v1/audio/transcriptions/{job_id}",
            json={
                "id": job_id,
                "status": "completed",
                "created_at": "2024-01-01T00:00:00Z",
                "started_at": "2024-01-01T00:00:01Z",
                "completed_at": "2024-01-01T00:00:10Z",
                "text": "Hello world",
                "language_code": "en",
                "words": [
                    {"text": "Hello", "start": 0.0, "end": 0.5, "confidence": 0.95},
                    {"text": "world", "start": 0.6, "end": 1.0, "confidence": 0.98},
                ],
                "segments": [
                    {
                        "id": 0,
                        "text": "Hello world",
                        "start": 0.0,
                        "end": 1.0,
                        "speaker_id": "SPEAKER_00",
                    }
                ],
                "speakers": [
                    {"id": "SPEAKER_00", "label": "Speaker 1", "total_duration": 1.0}
                ],
            },
        )

        job = client.get_job(job_id)

        assert job.status == JobStatus.COMPLETED
        assert job.transcript is not None
        assert job.transcript.text == "Hello world"
        assert job.transcript.language_code == "en"
        assert len(job.transcript.words) == 2
        assert len(job.transcript.segments) == 1
        assert len(job.transcript.speakers) == 1

    def test_get_job_not_found(self, client, httpx_mock):
        """Test get_job with non-existent job."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        httpx_mock.add_response(
            method="GET",
            url=f"http://test/v1/audio/transcriptions/{job_id}",
            json={"detail": "Job not found"},
            status_code=404,
        )

        with pytest.raises(NotFoundError):
            client.get_job(job_id)

    def test_list_jobs(self, client, httpx_mock):
        """Test list_jobs."""
        httpx_mock.add_response(
            method="GET",
            url="http://test/v1/audio/transcriptions?limit=20&offset=0",
            json={
                "jobs": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "status": "completed",
                        "created_at": "2024-01-01T00:00:00Z",
                    },
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440001",
                        "status": "running",
                        "created_at": "2024-01-01T00:01:00Z",
                        "progress": 50,
                    },
                ],
                "total": 2,
                "limit": 20,
                "offset": 0,
            },
        )

        result = client.list_jobs()

        assert isinstance(result, JobList)
        assert len(result.jobs) == 2
        assert result.total == 2
        assert result.jobs[0].status == JobStatus.COMPLETED
        assert result.jobs[1].status == JobStatus.RUNNING
        assert result.jobs[1].progress == 50

    def test_list_jobs_with_filter(self, client, httpx_mock):
        """Test list_jobs with status filter."""
        httpx_mock.add_response(
            method="GET",
            url="http://test/v1/audio/transcriptions?limit=10&offset=0&status=completed",
            json={
                "jobs": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "status": "completed",
                        "created_at": "2024-01-01T00:00:00Z",
                    },
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            },
        )

        result = client.list_jobs(limit=10, status=JobStatus.COMPLETED)

        assert len(result.jobs) == 1
        assert result.jobs[0].status == JobStatus.COMPLETED

    def test_export_srt(self, client, httpx_mock):
        """Test export to SRT format."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        srt_content = """1
00:00:00,000 --> 00:00:01,000
Hello world
"""

        httpx_mock.add_response(
            method="GET",
            url=f"http://test/v1/audio/transcriptions/{job_id}/export/srt?include_speakers=true&max_line_length=42&max_lines=2",
            text=srt_content,
        )

        result = client.export(job_id, format="srt")

        assert isinstance(result, str)
        assert "Hello world" in result

    def test_export_json(self, client, httpx_mock):
        """Test export to JSON format."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        httpx_mock.add_response(
            method="GET",
            url=f"http://test/v1/audio/transcriptions/{job_id}/export/json?include_speakers=true&max_line_length=42&max_lines=2",
            json={"text": "Hello world", "words": []},
        )

        result = client.export(job_id, format="json")

        assert isinstance(result, dict)
        assert result["text"] == "Hello world"


class TestAsyncDalston:
    """Tests for asynchronous Dalston client."""

    @pytest.mark.asyncio
    async def test_transcribe(self, async_client, httpx_mock, tmp_path):
        """Test async transcribe."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"fake audio data")

        httpx_mock.add_response(
            method="POST",
            url="http://test/v1/audio/transcriptions",
            json={
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00Z",
            },
            status_code=201,
        )

        job = await async_client.transcribe(file=str(audio_file))

        assert job.status == JobStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_job(self, async_client, httpx_mock):
        """Test async get_job."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        httpx_mock.add_response(
            method="GET",
            url=f"http://test/v1/audio/transcriptions/{job_id}",
            json={
                "id": job_id,
                "status": "running",
                "created_at": "2024-01-01T00:00:00Z",
                "progress": 75,
                "current_stage": "diarize",
            },
        )

        job = await async_client.get_job(job_id)

        assert job.status == JobStatus.RUNNING
        assert job.progress == 75
        assert job.current_stage == "diarize"

    @pytest.mark.asyncio
    async def test_context_manager(self, httpx_mock):
        """Test async context manager."""
        httpx_mock.add_response(
            method="GET",
            url="http://test/v1/audio/transcriptions?limit=20&offset=0",
            json={"jobs": [], "total": 0, "limit": 20, "offset": 0},
        )

        async with AsyncDalston(base_url="http://test") as client:
            result = await client.list_jobs()
            assert result.total == 0
