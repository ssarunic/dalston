"""Unit tests for realtime session storage (AudioRecorder, TranscriptRecorder).

Tests the M24 audio and transcript persistence functionality.
"""

import json
from unittest.mock import AsyncMock

import pytest

from dalston.realtime_sdk.audio_recorder import AudioRecorder, TranscriptRecorder
from dalston.realtime_sdk.protocol import SessionEndMessage


class TestSessionEndMessageWithURIs:
    """Tests for SessionEndMessage with audio_uri and transcript_uri fields."""

    def test_to_dict_with_uris(self):
        """SessionEndMessage includes URIs when set."""
        msg = SessionEndMessage(
            session_id="sess_abc123",
            total_duration=10.0,
            total_speech_duration=5.0,
            transcript="Hello world",
            segments=[],
            audio_uri="s3://bucket/sessions/sess_abc123/audio.wav",
            transcript_uri="s3://bucket/sessions/sess_abc123/transcript.json",
        )

        result = msg.to_dict()

        assert result["type"] == "session.end"
        assert result["audio_uri"] == "s3://bucket/sessions/sess_abc123/audio.wav"
        assert (
            result["transcript_uri"]
            == "s3://bucket/sessions/sess_abc123/transcript.json"
        )

    def test_to_dict_without_uris(self):
        """SessionEndMessage omits URIs when None."""
        msg = SessionEndMessage(
            session_id="sess_abc123",
            total_duration=10.0,
            total_speech_duration=5.0,
            transcript="Hello world",
            segments=[],
        )

        result = msg.to_dict()

        assert result["type"] == "session.end"
        assert "audio_uri" not in result
        assert "transcript_uri" not in result

    def test_to_json_with_uris(self):
        """SessionEndMessage JSON includes URIs."""
        msg = SessionEndMessage(
            session_id="sess_abc123",
            total_duration=10.0,
            total_speech_duration=5.0,
            transcript="Test",
            segments=[],
            audio_uri="s3://bucket/audio.wav",
            transcript_uri="s3://bucket/transcript.json",
        )

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["audio_uri"] == "s3://bucket/audio.wav"
        assert parsed["transcript_uri"] == "s3://bucket/transcript.json"


class TestAudioRecorder:
    """Tests for AudioRecorder class."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client."""
        client = AsyncMock()
        client.create_multipart_upload.return_value = {"UploadId": "upload-123"}
        client.upload_part.return_value = {"ETag": '"abc123"'}
        client.complete_multipart_upload.return_value = {}
        client.get_object.return_value = {
            "Body": AsyncMock(read=AsyncMock(return_value=b"\x00" * 100))
        }
        client.put_object.return_value = {}
        client.delete_object.return_value = {}
        return client

    @pytest.mark.asyncio
    async def test_start_creates_multipart_upload(self, mock_s3_client):
        """start() creates an S3 multipart upload."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )

        await recorder.start()

        mock_s3_client.create_multipart_upload.assert_called_once_with(
            Bucket="test-bucket",
            Key="sessions/sess_123/audio.raw",
            ContentType="application/octet-stream",
        )
        assert recorder._started is True
        assert recorder._upload_id == "upload-123"

    @pytest.mark.asyncio
    async def test_write_buffers_small_data(self, mock_s3_client):
        """write() buffers data until threshold."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )
        await recorder.start()

        # Write small amount of data (below flush threshold)
        await recorder.write(b"\x00" * 1000)

        # No parts uploaded yet
        mock_s3_client.upload_part.assert_not_called()
        assert recorder.total_bytes == 1000

    @pytest.mark.asyncio
    async def test_write_flushes_at_threshold(self, mock_s3_client):
        """write() flushes to S3 when buffer reaches threshold."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )
        # Lower threshold for testing
        recorder.FLUSH_THRESHOLD = 1000
        await recorder.start()

        # Write enough data to trigger flush
        await recorder.write(b"\x00" * 1500)

        mock_s3_client.upload_part.assert_called_once()
        assert len(recorder._parts) == 1

    @pytest.mark.asyncio
    async def test_finalize_completes_upload(self, mock_s3_client):
        """finalize() completes multipart upload and converts to WAV."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )
        recorder.FLUSH_THRESHOLD = 100
        await recorder.start()
        await recorder.write(b"\x00" * 200)

        uri = await recorder.finalize()

        assert uri == "s3://test-bucket/sessions/sess_123/audio.wav"
        mock_s3_client.complete_multipart_upload.assert_called_once()
        mock_s3_client.put_object.assert_called_once()  # WAV file
        mock_s3_client.delete_object.assert_called_once()  # Raw file deleted

    @pytest.mark.asyncio
    async def test_finalize_returns_none_if_no_data(self, mock_s3_client):
        """finalize() returns None if no data was written."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )

        uri = await recorder.finalize()

        assert uri is None

    @pytest.mark.asyncio
    async def test_abort_cleans_up_upload(self, mock_s3_client):
        """abort() cleans up incomplete multipart upload."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )
        await recorder.start()

        await recorder.abort()

        mock_s3_client.abort_multipart_upload.assert_called_once_with(
            Bucket="test-bucket",
            Key="sessions/sess_123/audio.raw",
            UploadId="upload-123",
        )

    def test_duration_seconds_calculation(self, mock_s3_client):
        """duration_seconds calculates from total bytes."""
        recorder = AudioRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
            sample_rate=16000,
            channels=1,
            bits_per_sample=16,
        )
        recorder._total_bytes = 32000  # 1 second at 16kHz, 16-bit mono

        assert recorder.duration_seconds == 1.0


class TestTranscriptRecorder:
    """Tests for TranscriptRecorder class."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client."""
        client = AsyncMock()
        client.put_object.return_value = {}
        return client

    @pytest.mark.asyncio
    async def test_save_uploads_json(self, mock_s3_client):
        """save() uploads transcript JSON to S3."""
        recorder = TranscriptRecorder(
            session_id="sess_123",
            s3_client=mock_s3_client,
            bucket="test-bucket",
        )

        transcript = {
            "session_id": "sess_123",
            "language": "en",
            "text": "Hello world",
            "utterances": [],
        }

        uri = await recorder.save(transcript)

        assert uri == "s3://test-bucket/sessions/sess_123/transcript.json"
        mock_s3_client.put_object.assert_called_once()

        # Verify JSON content
        call_args = mock_s3_client.put_object.call_args
        body = call_args.kwargs["Body"]
        saved_transcript = json.loads(body.decode("utf-8"))
        assert saved_transcript["text"] == "Hello world"
