"""Audio Redaction Engine using FFmpeg.

Replaces PII segments in audio with silence or beep tones based on
timing information from the PII detection stage.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from dalston.engine_sdk import (
    AudioRedactOutput,
    Engine,
    PIIRedactionMode,
    TaskInput,
    TaskOutput,
    io,
)


class AudioRedactionEngine(Engine):
    """Audio redaction engine using FFmpeg."""

    BEEP_FREQUENCY = 1000  # 1kHz beep tone

    def process(self, input: TaskInput) -> TaskOutput:
        """Redact PII from audio file.

        Args:
            input: Task input with PII detection output

        Returns:
            TaskOutput with AudioRedactOutput containing redacted audio URI
        """
        config = input.config
        job_id = input.job_id
        audio_path = input.audio_path

        # Get channel from config (set by DAG builder for per-channel mode)
        channel: int | None = config.get("channel")

        # Determine output filename and PII key based on channel
        if channel is not None:
            channel_suffix = f"_ch{channel}"
            pii_key = f"pii_detect_ch{channel}"
        else:
            channel_suffix = ""
            pii_key = "pii_detect"

        output_filename = f"redacted{channel_suffix}.wav"

        # Get config
        mode_str = config.get("redaction_mode", "silence")
        mode = PIIRedactionMode(mode_str)
        buffer_ms = config.get("buffer_ms", 50)

        self.logger.info(
            "audio_redaction_starting",
            job_id=job_id,
            mode=mode.value,
            buffer_ms=buffer_ms,
            channel=channel,
        )

        pii_output = input.get_pii_detect_output(pii_key)
        if not pii_output:
            # Try raw output with channel-specific key
            raw_pii = input.get_raw_output(pii_key) or {}
            entities = raw_pii.get("entities", [])
        else:
            entities = [e.model_dump() for e in pii_output.entities]

        if not entities:
            self.logger.info("no_entities_to_redact", job_id=job_id)
            # No entities - just copy the audio
            s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
            redacted_uri = f"s3://{s3_bucket}/jobs/{job_id}/audio/{output_filename}"
            io.upload_file(audio_path, redacted_uri)

            output = AudioRedactOutput(
                redacted_audio_uri=redacted_uri,
                redaction_mode=mode,
                buffer_ms=buffer_ms,
                entities_redacted=0,
                redaction_map=[],
                engine_id="audio-redactor",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )
            return TaskOutput(data=output)

        # Extract time ranges from entities
        ranges = self._extract_time_ranges(entities, buffer_ms)

        # Merge overlapping ranges
        merged_ranges = self._merge_ranges(ranges)

        self.logger.info(
            "redacting_ranges",
            job_id=job_id,
            entity_count=len(entities),
            range_count=len(merged_ranges),
        )

        # Build FFmpeg filter and execute
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            output_path = Path(tmp.name)

        try:
            self._apply_redaction(audio_path, output_path, merged_ranges, mode)

            # Upload to S3
            s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
            redacted_uri = f"s3://{s3_bucket}/jobs/{job_id}/audio/{output_filename}"
            io.upload_file(output_path, redacted_uri)

            self.logger.info(
                "audio_redaction_complete",
                job_id=job_id,
                redacted_uri=redacted_uri,
            )

            # Build redaction map
            redaction_map = self._build_redaction_map(merged_ranges, entities)

            output = AudioRedactOutput(
                redacted_audio_uri=redacted_uri,
                redaction_mode=mode,
                buffer_ms=buffer_ms,
                entities_redacted=len(entities),
                redaction_map=redaction_map,
                engine_id="audio-redactor",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskOutput(data=output)

        finally:
            if output_path.exists():
                output_path.unlink()

    def _extract_time_ranges(
        self, entities: list[dict], buffer_ms: int
    ) -> list[tuple[float, float, list[str]]]:
        """Extract time ranges from entities with buffer padding.

        Args:
            entities: List of PII entities
            buffer_ms: Buffer padding in milliseconds

        Returns:
            List of (start, end, entity_types) tuples
        """
        buffer_sec = buffer_ms / 1000.0
        ranges: list[tuple[float, float, list[str]]] = []

        for entity in entities:
            start = max(0, entity.get("start_time", 0) - buffer_sec)
            end = entity.get("end_time", 0) + buffer_sec
            entity_type = entity.get("entity_type", "unknown")
            ranges.append((start, end, [entity_type]))

        return ranges

    def _merge_ranges(
        self, ranges: list[tuple[float, float, list[str]]]
    ) -> list[tuple[float, float, list[str]]]:
        """Merge overlapping time ranges.

        Args:
            ranges: List of (start, end, entity_types) tuples

        Returns:
            Merged list of non-overlapping ranges
        """
        if not ranges:
            return []

        # Sort by start time
        sorted_ranges = sorted(ranges, key=lambda r: r[0])

        merged: list[tuple[float, float, list[str]]] = []
        current_start, current_end, current_types = sorted_ranges[0]

        for start, end, types in sorted_ranges[1:]:
            if start <= current_end:
                # Overlapping - extend current range
                current_end = max(current_end, end)
                current_types = list(set(current_types + types))
            else:
                # Non-overlapping - save current and start new
                merged.append((current_start, current_end, current_types))
                current_start, current_end, current_types = start, end, types

        # Don't forget the last range
        merged.append((current_start, current_end, current_types))

        return merged

    def _apply_redaction(
        self,
        input_path: Path,
        output_path: Path,
        ranges: list[tuple[float, float, list[str]]],
        mode: PIIRedactionMode,
    ) -> None:
        """Apply redaction to audio using FFmpeg.

        Args:
            input_path: Input audio file path
            output_path: Output audio file path
            ranges: Time ranges to redact
            mode: Redaction mode (silence or beep)
        """
        if not ranges:
            # No ranges - just copy
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(output_path)],
                check=True,
                capture_output=True,
            )
            return

        if mode == PIIRedactionMode.SILENCE:
            # Simple audio filter for silence
            filter_chain = self._build_silence_filter(ranges)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-af",
                filter_chain,
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output_path),
            ]
        else:
            # Complex filter graph for beep (requires -filter_complex)
            filter_graph = self._build_beep_filter(ranges)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-filter_complex",
                filter_graph,
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output_path),
            ]

        self.logger.debug("ffmpeg_command", cmd=" ".join(cmd))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            self.logger.error(
                "ffmpeg_failed",
                returncode=result.returncode,
                stderr=result.stderr,
            )
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    def _build_silence_filter(
        self, ranges: list[tuple[float, float, list[str]]]
    ) -> str:
        """Build FFmpeg filter for silence redaction.

        Args:
            ranges: Time ranges to redact

        Returns:
            FFmpeg audio filter string
        """
        # Use volume filter with enable expression for each range
        filters = []
        for start, end, _ in ranges:
            filters.append(f"volume=enable='between(t,{start:.3f},{end:.3f})':volume=0")

        return ",".join(filters)

    def _build_beep_filter(self, ranges: list[tuple[float, float, list[str]]]) -> str:
        """Build FFmpeg filter for beep redaction.

        Uses a complex filter graph to:
        1. Silence original audio during PII segments
        2. Generate a sine wave beep tone
        3. Enable the beep only during PII segments
        4. Mix both streams together

        Args:
            ranges: Time ranges to redact

        Returns:
            FFmpeg audio filter string with beep tone overlay
        """
        # Build enable expression for all ranges
        # Format: between(t,start1,end1)+between(t,start2,end2)+...
        enable_parts = [f"between(t,{start:.3f},{end:.3f})" for start, end, _ in ranges]
        enable_expr = "+".join(enable_parts)

        # Build inverse expression for silencing (1 when NOT in any range)
        # We use 1-(...) to invert the expression
        silence_expr = f"1-({enable_expr})"

        # Complex filter graph:
        # [0:a] -> silence PII segments -> [silenced]
        # sine wave -> enable only during PII -> [beep]
        # [silenced][beep] -> amix -> output
        filter_graph = (
            # Silence the original audio during PII segments
            f"[0:a]volume=enable='{enable_expr}':volume=0[silenced];"
            # Generate sine wave at beep frequency, enable only during PII
            f"sine=frequency={self.BEEP_FREQUENCY}:sample_rate=16000,"
            f"volume=enable='{silence_expr}':volume=0[beep];"
            # Mix silenced original with beep
            "[silenced][beep]amix=inputs=2:duration=first:normalize=0"
        )

        return filter_graph

    def _build_redaction_map(
        self,
        ranges: list[tuple[float, float, list[str]]],
        entities: list[dict],
    ) -> list[dict[str, Any]]:
        """Build redaction map for output.

        Args:
            ranges: Merged time ranges
            entities: Original entities

        Returns:
            List of redaction map entries
        """
        return [
            {
                "start_time": start,
                "end_time": end,
                "entity_types": types,
            }
            for start, end, types in ranges
        ]

    def health_check(self) -> dict[str, Any]:
        """Return health status including FFmpeg availability."""
        ffmpeg_available = False
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
            )
            ffmpeg_available = result.returncode == 0
        except FileNotFoundError:
            pass

        return {
            "status": "healthy" if ffmpeg_available else "degraded",
            "ffmpeg_available": ffmpeg_available,
        }


if __name__ == "__main__":
    engine = AudioRedactionEngine()
    engine.run()
