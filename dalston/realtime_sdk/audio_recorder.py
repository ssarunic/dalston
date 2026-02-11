"""Audio recorder for real-time sessions with S3 multipart upload.

Records audio to S3 during real-time transcription sessions,
using multipart upload for efficient handling of long recordings.
"""

from __future__ import annotations

import io
import wave
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

logger = structlog.get_logger()


class AudioRecorder:
    """Records audio to S3 using multipart upload.

    Buffers incoming audio chunks and uploads to S3 in parts
    when the buffer reaches a threshold. Finalizes the upload
    and adds WAV header on completion.

    Example:
        recorder = AudioRecorder(
            session_id="sess_abc123",
            s3_client=s3,
            bucket="my-bucket",
            sample_rate=16000,
        )

        await recorder.start()

        # During session
        for chunk in audio_chunks:
            await recorder.write(chunk)

        # On session end
        audio_uri = await recorder.finalize()
    """

    # Minimum part size for S3 multipart upload (5MB)
    MIN_PART_SIZE = 5 * 1024 * 1024

    # Flush threshold - upload when buffer reaches this size
    FLUSH_THRESHOLD = 5 * 1024 * 1024

    def __init__(
        self,
        session_id: str,
        s3_client: S3Client,
        bucket: str,
        sample_rate: int = 16000,
        channels: int = 1,
        bits_per_sample: int = 16,
    ) -> None:
        """Initialize audio recorder.

        Args:
            session_id: Session ID for S3 key construction
            s3_client: Async S3 client (aiobotocore)
            bucket: S3 bucket name
            sample_rate: Audio sample rate (default: 16000)
            channels: Number of audio channels (default: 1)
            bits_per_sample: Bits per sample (default: 16)
        """
        self.session_id = session_id
        self.s3_client = s3_client
        self.bucket = bucket
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits_per_sample = bits_per_sample

        # S3 multipart upload state
        self._upload_id: str | None = None
        self._parts: list[dict] = []
        self._key = f"sessions/{session_id}/audio.raw"
        self._final_key = f"sessions/{session_id}/audio.wav"

        # Buffer for accumulating audio
        self._buffer = io.BytesIO()
        self._total_bytes = 0

        # State
        self._started = False
        self._finalized = False

    async def start(self) -> None:
        """Start the multipart upload.

        Must be called before writing audio data.
        """
        if self._started:
            return

        response = await self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=self._key,
            ContentType="application/octet-stream",
        )
        self._upload_id = response["UploadId"]
        self._started = True

        logger.debug(
            "audio_recorder_started",
            session_id=self.session_id,
            upload_id=self._upload_id,
        )

    async def write(self, audio_data: bytes) -> None:
        """Write audio data to the recording.

        Buffers data and uploads to S3 when threshold is reached.

        Args:
            audio_data: Raw PCM audio bytes
        """
        if not self._started:
            await self.start()

        if self._finalized:
            raise RuntimeError("Cannot write to finalized recorder")

        self._buffer.write(audio_data)
        self._total_bytes += len(audio_data)

        # Check if we should flush to S3
        if self._buffer.tell() >= self.FLUSH_THRESHOLD:
            await self._flush_part()

    async def _flush_part(self) -> None:
        """Upload buffered data as a part."""
        if self._buffer.tell() == 0:
            return

        part_number = len(self._parts) + 1
        self._buffer.seek(0)
        data = self._buffer.read()

        # For S3 multipart, all parts except the last must be >= 5MB
        # We accumulate in buffer until threshold, then upload
        response = await self.s3_client.upload_part(
            Bucket=self.bucket,
            Key=self._key,
            UploadId=self._upload_id,
            PartNumber=part_number,
            Body=data,
        )

        self._parts.append(
            {
                "PartNumber": part_number,
                "ETag": response["ETag"],
            }
        )

        # Reset buffer
        self._buffer = io.BytesIO()

        logger.debug(
            "audio_recorder_part_uploaded",
            session_id=self.session_id,
            part_number=part_number,
            size=len(data),
        )

    async def finalize(self) -> str | None:
        """Finalize the recording and return S3 URI.

        Uploads any remaining data, completes the multipart upload,
        then converts to WAV format.

        Returns:
            S3 URI to the WAV file, or None if no audio was recorded
        """
        if self._finalized:
            return f"s3://{self.bucket}/{self._final_key}"

        if not self._started:
            return None

        self._finalized = True

        # Flush remaining buffer
        if self._buffer.tell() > 0:
            await self._flush_part()

        if not self._parts:
            # No data was uploaded, abort
            await self._abort()
            return None

        # Complete multipart upload
        await self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=self._key,
            UploadId=self._upload_id,
            MultipartUpload={"Parts": self._parts},
        )

        logger.info(
            "audio_recorder_raw_completed",
            session_id=self.session_id,
            total_bytes=self._total_bytes,
            parts=len(self._parts),
        )

        # Convert to WAV
        await self._convert_to_wav()

        return f"s3://{self.bucket}/{self._final_key}"

    async def _convert_to_wav(self) -> None:
        """Convert raw PCM to WAV format by adding header.

        Downloads raw file, prepends WAV header, uploads as new file.
        """
        # Download raw audio
        response = await self.s3_client.get_object(
            Bucket=self.bucket,
            Key=self._key,
        )
        raw_data = await response["Body"].read()

        # Create WAV file in memory
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(self.bits_per_sample // 8)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(raw_data)

        # Upload WAV file
        wav_buffer.seek(0)
        await self.s3_client.put_object(
            Bucket=self.bucket,
            Key=self._final_key,
            Body=wav_buffer.read(),
            ContentType="audio/wav",
        )

        # Delete raw file
        await self.s3_client.delete_object(
            Bucket=self.bucket,
            Key=self._key,
        )

        logger.info(
            "audio_recorder_wav_created",
            session_id=self.session_id,
            key=self._final_key,
        )

    async def abort(self) -> None:
        """Abort the recording without finalizing.

        Cleans up the incomplete multipart upload.
        """
        await self._abort()

    async def _abort(self) -> None:
        """Internal abort implementation."""
        if self._upload_id:
            try:
                await self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=self._key,
                    UploadId=self._upload_id,
                )
            except Exception as e:
                logger.warning(
                    "audio_recorder_abort_failed",
                    session_id=self.session_id,
                    error=str(e),
                )

        self._finalized = True

    @property
    def total_bytes(self) -> int:
        """Total bytes of audio recorded."""
        return self._total_bytes

    @property
    def duration_seconds(self) -> float:
        """Estimated duration in seconds."""
        bytes_per_sample = self.bits_per_sample // 8
        samples = self._total_bytes // (bytes_per_sample * self.channels)
        return samples / self.sample_rate


class TranscriptRecorder:
    """Records transcript data to S3.

    Saves the final transcript JSON to S3 on session end.

    Example:
        recorder = TranscriptRecorder(
            session_id="sess_abc123",
            s3_client=s3,
            bucket="my-bucket",
        )

        await recorder.save(transcript_data)
    """

    def __init__(
        self,
        session_id: str,
        s3_client: S3Client,
        bucket: str,
    ) -> None:
        """Initialize transcript recorder.

        Args:
            session_id: Session ID for S3 key construction
            s3_client: Async S3 client
            bucket: S3 bucket name
        """
        self.session_id = session_id
        self.s3_client = s3_client
        self.bucket = bucket
        self._key = f"sessions/{session_id}/transcript.json"

    async def save(self, transcript: dict) -> str:
        """Save transcript to S3.

        Args:
            transcript: Transcript data dictionary

        Returns:
            S3 URI to the transcript file
        """
        import json

        body = json.dumps(transcript, indent=2, ensure_ascii=False)

        await self.s3_client.put_object(
            Bucket=self.bucket,
            Key=self._key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            "transcript_saved",
            session_id=self.session_id,
            key=self._key,
        )

        return f"s3://{self.bucket}/{self._key}"
