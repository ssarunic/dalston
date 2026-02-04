"""Audio preparation engine for converting audio to standardized format.

Converts any audio format to 16kHz, 16-bit, mono WAV using ffmpeg.
Extracts duration and metadata using ffprobe.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from uuid import UUID

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk import io as s3_io

logger = logging.getLogger(__name__)


class AudioPrepareEngine(Engine):
    """Audio preparation engine that standardizes audio format.

    Converts input audio to 16kHz, 16-bit, mono WAV suitable for
    downstream transcription engines.
    """

    # Default output parameters
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1

    # Subprocess timeouts (seconds)
    FFPROBE_TIMEOUT = 60  # 1 minute for probing metadata
    FFMPEG_TIMEOUT = 1800  # 30 minutes for conversion (handles long audio)

    def __init__(self) -> None:
        super().__init__()
        self._verify_ffmpeg_installed()

    def _verify_ffmpeg_installed(self) -> None:
        """Verify ffmpeg and ffprobe are available."""
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["ffprobe", "-version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                "ffmpeg and ffprobe must be installed. "
                "Install with: apt-get install ffmpeg"
            ) from e

    def process(self, input: TaskInput) -> TaskOutput:
        """Convert audio to standardized format.

        Args:
            input: Task input with audio file path

        Returns:
            TaskOutput with prepared audio URI and metadata
        """
        audio_path = input.audio_path
        job_id = input.job_id
        config = input.config

        # Get config options with defaults
        target_sample_rate = config.get("target_sample_rate", self.DEFAULT_SAMPLE_RATE)
        split_channels = config.get("split_channels", False)

        logger.info(f"Processing audio: {audio_path}")

        # Step 1: Probe original audio metadata
        original_metadata = self._probe_audio(audio_path)
        logger.info(f"Original audio: {original_metadata}")

        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")

        # Handle channel splitting for per_channel speaker detection
        if split_channels:
            if original_metadata["channels"] < 2:
                raise RuntimeError(
                    f"per_channel mode requires stereo audio, but input has "
                    f"{original_metadata['channels']} channel(s). Use speaker_detection=diarize "
                    f"for mono audio."
                )
        if split_channels and original_metadata["channels"] >= 2:
            return self._process_split_channels(
                audio_path=audio_path,
                job_id=job_id,
                original_metadata=original_metadata,
                target_sample_rate=target_sample_rate,
                s3_bucket=s3_bucket,
            )

        # Standard processing: convert to mono
        target_channels = config.get("target_channels", self.DEFAULT_CHANNELS)

        # Step 2: Convert to standardized format
        prepared_path = audio_path.parent / "prepared.wav"
        self._convert_audio(
            input_path=audio_path,
            output_path=prepared_path,
            sample_rate=target_sample_rate,
            channels=target_channels,
        )
        logger.info(f"Converted audio saved to: {prepared_path}")

        # Step 3: Probe converted audio to verify
        prepared_metadata = self._probe_audio(prepared_path)
        logger.info(f"Prepared audio: {prepared_metadata}")

        # Step 4: Upload prepared audio to S3
        audio_uri = f"s3://{s3_bucket}/jobs/{job_id}/audio/prepared.wav"
        s3_io.upload_file(prepared_path, audio_uri)
        logger.info(f"Uploaded prepared audio to: {audio_uri}")

        # Step 5: Clean up local temp file to prevent accumulation
        try:
            prepared_path.unlink()
            logger.debug(f"Cleaned up temporary file: {prepared_path}")
        except OSError as e:
            logger.warning(f"Failed to clean up temp file {prepared_path}: {e}")

        # Build output data
        output_data = {
            "audio_uri": audio_uri,
            "duration": prepared_metadata["duration"],
            "sample_rate": prepared_metadata["sample_rate"],
            "channels": prepared_metadata["channels"],
            "original_format": original_metadata.get("codec_name", "unknown"),
            "original_duration": original_metadata["duration"],
            "original_sample_rate": original_metadata["sample_rate"],
            "original_channels": original_metadata["channels"],
            "split_channels": False,
        }

        return TaskOutput(data=output_data)

    def _process_split_channels(
        self,
        audio_path: Path,
        job_id: UUID,
        original_metadata: dict,
        target_sample_rate: int,
        s3_bucket: str,
    ) -> TaskOutput:
        """Process audio by splitting into separate channel files.

        Used for per_channel speaker detection where each channel
        represents a different speaker.

        Args:
            audio_path: Path to input audio
            job_id: Job identifier
            original_metadata: Metadata from original audio probe
            target_sample_rate: Target sample rate for output
            s3_bucket: S3 bucket for uploads

        Returns:
            TaskOutput with channel_files array
        """
        num_channels = min(original_metadata["channels"], 2)  # Limit to stereo
        logger.info(f"Splitting audio into {num_channels} channels")

        channel_files = []
        channel_uris = []

        for channel_idx in range(num_channels):
            # Extract single channel to mono WAV
            channel_path = audio_path.parent / f"prepared_ch{channel_idx}.wav"
            self._extract_channel(
                input_path=audio_path,
                output_path=channel_path,
                channel=channel_idx,
                sample_rate=target_sample_rate,
            )

            # Probe the channel file
            channel_metadata = self._probe_audio(channel_path)
            logger.info(f"Channel {channel_idx}: {channel_metadata}")

            # Upload to S3
            audio_uri = (
                f"s3://{s3_bucket}/jobs/{job_id}/audio/prepared_ch{channel_idx}.wav"
            )
            s3_io.upload_file(channel_path, audio_uri)
            logger.info(f"Uploaded channel {channel_idx} to: {audio_uri}")

            channel_files.append(
                {
                    "channel": channel_idx,
                    "audio_uri": audio_uri,
                    "duration": channel_metadata["duration"],
                }
            )
            channel_uris.append(audio_uri)

            # Clean up temp file
            try:
                channel_path.unlink()
            except OSError as e:
                logger.warning(f"Failed to clean up {channel_path}: {e}")

        # Build output data
        output_data = {
            "audio_uri": channel_uris[0],  # Primary audio for compatibility
            "duration": original_metadata["duration"],
            "sample_rate": target_sample_rate,
            "channels": 1,  # Each output file is mono
            "original_format": original_metadata.get("codec_name", "unknown"),
            "original_duration": original_metadata["duration"],
            "original_sample_rate": original_metadata["sample_rate"],
            "original_channels": original_metadata["channels"],
            "split_channels": True,
            "channel_count": num_channels,
            "channel_files": channel_files,
        }

        return TaskOutput(data=output_data)

    def _extract_channel(
        self,
        input_path: Path,
        output_path: Path,
        channel: int,
        sample_rate: int,
    ) -> None:
        """Extract a single channel from audio file.

        Args:
            input_path: Path to input audio file
            output_path: Path for output mono WAV file
            channel: Channel index (0=left, 1=right)
            sample_rate: Target sample rate
        """
        # Use ffmpeg's pan filter to extract specific channel
        # pan=mono|c0=c{channel} extracts channel N to mono output
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            f"pan=mono|c0=c{channel}",
            "-ar",
            str(sample_rate),
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            str(output_path),
        ]

        logger.debug(f"Extracting channel {channel}: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.FFMPEG_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg channel extraction timed out after {self.FFMPEG_TIMEOUT}s"
            ) from None

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg channel extraction failed: {result.stderr}")

        if not output_path.exists():
            raise RuntimeError(f"ffmpeg did not produce output file: {output_path}")

    def _probe_audio(self, audio_path: Path) -> dict:
        """Probe audio file to extract metadata using ffprobe.

        Args:
            audio_path: Path to audio file

        Returns:
            Dictionary with duration, sample_rate, channels, codec_name
        """
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "-select_streams",
            "a:0",  # First audio stream
            str(audio_path),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.FFPROBE_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffprobe timed out after {self.FFPROBE_TIMEOUT}s for {audio_path}"
            ) from None

        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed for {audio_path}: {result.stderr}")

        try:
            probe_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse ffprobe output: {e}") from e

        # Extract from streams (preferred) or format (fallback)
        streams = probe_data.get("streams", [])
        format_info = probe_data.get("format", {})

        if not streams:
            raise RuntimeError(
                f"No audio stream found in {audio_path}. "
                "File may be corrupted or contain no audio."
            )

        stream = streams[0]

        # Get duration - try stream first, then format
        duration_str = stream.get("duration") or format_info.get("duration")
        if duration_str is None:
            raise RuntimeError(f"Could not determine duration for {audio_path}")

        duration = float(duration_str)

        # Get sample rate
        sample_rate_str = stream.get("sample_rate")
        if sample_rate_str is None:
            raise RuntimeError(f"Could not determine sample rate for {audio_path}")

        sample_rate = int(sample_rate_str)

        # Get channels
        channels = stream.get("channels")
        if channels is None:
            raise RuntimeError(f"Could not determine channels for {audio_path}")

        return {
            "duration": duration,
            "sample_rate": sample_rate,
            "channels": channels,
            "codec_name": stream.get("codec_name", "unknown"),
        }

    def _convert_audio(
        self,
        input_path: Path,
        output_path: Path,
        sample_rate: int,
        channels: int,
    ) -> None:
        """Convert audio to target format using ffmpeg.

        Args:
            input_path: Path to input audio file
            output_path: Path for output WAV file
            sample_rate: Target sample rate (e.g., 16000)
            channels: Target number of channels (1=mono, 2=stereo)
        """
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output without asking
            "-i",
            str(input_path),
            "-ar",
            str(sample_rate),  # Resample
            "-ac",
            str(channels),  # Convert channels
            "-sample_fmt",
            "s16",  # 16-bit signed PCM
            "-f",
            "wav",  # Force WAV format
            str(output_path),
        ]

        logger.debug(f"Running ffmpeg: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.FFMPEG_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ffmpeg conversion timed out after {self.FFMPEG_TIMEOUT}s"
            ) from None

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")

        if not output_path.exists():
            raise RuntimeError(f"ffmpeg did not produce output file: {output_path}")


if __name__ == "__main__":
    engine = AudioPrepareEngine()
    engine.run()
