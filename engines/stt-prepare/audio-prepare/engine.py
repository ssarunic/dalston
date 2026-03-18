"""Audio preparation engine for converting audio to standardized format.

Converts any audio format to 16kHz, 16-bit, mono WAV using ffmpeg.
Extracts duration and metadata using ffprobe.
"""

import json
import subprocess
from pathlib import Path

from dalston.common.artifacts import build_task_artifact_id
from dalston.engine_sdk import (
    AudioMedia,
    BatchTaskContext,
    Engine,
    PreparationResponse,
    TaskRequest,
    TaskResponse,
)


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

    def process(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Convert audio to standardized format.

        Args:
            task_request: Task input with audio file path

        Returns:
            TaskResponse with PreparationResponse containing artifact IDs and metadata
        """
        audio_path = task_request.audio_path
        params = task_request.get_prepare_params()

        # Get config options from typed params
        target_sample_rate = params.target_sample_rate
        split_channels = params.split_channels

        self.logger.info("processing_audio", audio_path=str(audio_path))

        # Step 1: Probe original audio metadata
        original_metadata = self._probe_audio(audio_path)
        self.logger.info("original_audio_metadata", metadata=original_metadata)

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
                original_metadata=original_metadata,
                target_sample_rate=target_sample_rate,
                task_id=task_request.task_id,
                ctx=ctx,
            )

        # Standard processing: convert to mono
        target_channels = params.target_channels

        # Step 2: Convert to standardized format
        prepared_path = audio_path.parent / "prepared.wav"
        self._convert_audio(
            input_path=audio_path,
            output_path=prepared_path,
            sample_rate=target_sample_rate,
            channels=target_channels,
        )
        self.logger.info("converted_audio_saved", prepared_path=str(prepared_path))

        # Step 3: Probe converted audio to verify
        prepared_metadata = self._probe_audio(prepared_path)
        self.logger.info("prepared_audio_metadata", metadata=prepared_metadata)

        logical_name = "prepared_audio"
        artifact_id = build_task_artifact_id(task_request.task_id, logical_name)
        produced = ctx.describe_artifact(
            logical_name=logical_name,
            local_path=prepared_path,
            kind="audio",
            channel=0,
            role="prepared",
            media_type="audio/wav",
        )

        # Build typed output with AudioMedia (single-element array for mono)
        prepared = AudioMedia(
            artifact_id=artifact_id,
            format="wav",
            duration=prepared_metadata["duration"],
            sample_rate=prepared_metadata["sample_rate"],
            channels=prepared_metadata["channels"],
            bit_depth=prepared_metadata["bit_depth"],
        )

        output = PreparationResponse(
            channel_files=[prepared],
            split_channels=False,
            engine_id="audio-prepare",
        )

        return TaskResponse(data=output, produced_artifacts=[produced])

    def _process_split_channels(
        self,
        *,
        audio_path: Path,
        original_metadata: dict,
        target_sample_rate: int,
        task_id: str,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Process audio by splitting into separate channel files.

        Used for per_channel speaker detection where each channel
        represents a different speaker.

        Args:
            audio_path: Path to input audio
            original_metadata: Metadata from original audio probe
            target_sample_rate: Target sample rate for output
            task_id: Task identifier
            ctx: Task execution context

        Returns:
            TaskResponse with PreparationResponse containing channel_files array
        """
        num_channels = original_metadata["channels"]
        if num_channels > 2:
            raise ValueError(
                f"per_channel mode supports stereo (2 channels), but input has "
                f"{num_channels} channels. Use speaker_detection=diarize instead."
            )
        self.logger.info("splitting_audio_into_channels", num_channels=num_channels)

        channel_files: list[AudioMedia] = []
        produced_artifacts = []

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
            self.logger.info(
                "channel_metadata", channel=channel_idx, metadata=channel_metadata
            )

            logical_name = f"prepared_audio_ch{channel_idx}"
            artifact_id = build_task_artifact_id(task_id, logical_name)
            produced_artifacts.append(
                ctx.describe_artifact(
                    logical_name=logical_name,
                    local_path=channel_path,
                    kind="audio",
                    channel=channel_idx,
                    role="prepared",
                    media_type="audio/wav",
                )
            )
            self.logger.info("prepared_channel_file", channel=channel_idx)

            channel_files.append(
                AudioMedia(
                    artifact_id=artifact_id,
                    format="wav",
                    duration=channel_metadata["duration"],
                    sample_rate=channel_metadata["sample_rate"],
                    channels=channel_metadata["channels"],
                    bit_depth=channel_metadata["bit_depth"],
                )
            )

        # Build typed output
        output = PreparationResponse(
            channel_files=channel_files,
            split_channels=True,
            engine_id="audio-prepare",
        )

        return TaskResponse(data=output, produced_artifacts=produced_artifacts)

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

        self.logger.debug("extracting_channel", channel=channel, cmd=" ".join(cmd))

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

        # Get bit depth (bits_per_sample or bits_per_raw_sample)
        bit_depth = stream.get("bits_per_sample") or stream.get("bits_per_raw_sample")
        if bit_depth:
            bit_depth = int(bit_depth)

        return {
            "duration": duration,
            "sample_rate": sample_rate,
            "channels": channels,
            "bit_depth": bit_depth,
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

        self.logger.debug("running_ffmpeg", cmd=" ".join(cmd))

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
