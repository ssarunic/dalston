"""Export service for transcript format conversion."""

import json
import textwrap
from enum import Enum
from typing import Any

from fastapi import HTTPException, Response


class ExportFormat(str, Enum):
    """Supported export formats."""

    SRT = "srt"
    VTT = "vtt"
    WEBVTT = "webvtt"  # Alias for VTT
    TXT = "txt"
    JSON = "json"


class ExportService:
    """Service for exporting transcripts in various formats."""

    @staticmethod
    def format_timestamp_srt(seconds: float) -> str:
        """Format seconds as SRT timestamp: HH:MM:SS,mmm (comma separator)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def format_timestamp_vtt(seconds: float) -> str:
        """Format seconds as VTT timestamp: HH:MM:SS.mmm (dot separator)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    @staticmethod
    def wrap_text(text: str, max_line_length: int, max_lines: int = 2) -> str:
        """Wrap text to fit within subtitle constraints.

        Args:
            text: Text to wrap
            max_line_length: Maximum characters per line
            max_lines: Maximum number of lines per subtitle block

        Returns:
            Wrapped text with newlines
        """
        if len(text) <= max_line_length:
            return text

        # Use textwrap for intelligent word wrapping
        lines = textwrap.wrap(text, width=max_line_length)

        # Limit to max_lines
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            # Add ellipsis if truncated
            if len(lines[-1]) > max_line_length - 3:
                lines[-1] = lines[-1][: max_line_length - 3] + "..."
            else:
                lines[-1] = lines[-1] + "..."

        return "\n".join(lines)

    def export_srt(
        self,
        transcript: dict[str, Any],
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> str:
        """Export transcript as SRT subtitle format.

        Uses segments for natural subtitle boundaries.

        Format:
            1
            00:00:00,000 --> 00:00:02,500
            [SPEAKER_00] Welcome to the show.

        Args:
            transcript: Transcript dict with segments
            include_speakers: Whether to include speaker labels
            max_line_length: Max chars per subtitle line
            max_lines: Max lines per subtitle block

        Returns:
            SRT formatted string
        """
        segments = transcript.get("segments", [])
        if not segments:
            return ""

        lines = []
        for idx, segment in enumerate(segments, start=1):
            start = segment.get("start", 0.0)
            end = segment.get("end", 0.0)
            text = segment.get("text", "").strip()

            if not text:
                continue

            # Add speaker prefix if enabled
            if include_speakers and segment.get("speaker_id"):
                text = f"[{segment['speaker_id']}] {text}"

            # Wrap text
            wrapped_text = self.wrap_text(text, max_line_length, max_lines)

            # Format SRT block
            lines.append(str(idx))
            lines.append(
                f"{self.format_timestamp_srt(start)} --> {self.format_timestamp_srt(end)}"
            )
            lines.append(wrapped_text)
            lines.append("")  # Blank line separator

        return "\n".join(lines)

    def export_vtt(
        self,
        transcript: dict[str, Any],
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> str:
        """Export transcript as WebVTT subtitle format.

        Uses segments for natural subtitle boundaries.

        Format:
            WEBVTT

            00:00:00.000 --> 00:00:02.500
            <v SPEAKER_00>Welcome to the show.

        Args:
            transcript: Transcript dict with segments
            include_speakers: Whether to include speaker voice tags
            max_line_length: Max chars per subtitle line
            max_lines: Max lines per subtitle block

        Returns:
            WebVTT formatted string
        """
        segments = transcript.get("segments", [])

        lines = ["WEBVTT", ""]  # Header and blank line

        if not segments:
            return "\n".join(lines)

        for segment in segments:
            start = segment.get("start", 0.0)
            end = segment.get("end", 0.0)
            text = segment.get("text", "").strip()

            if not text:
                continue

            # Add timestamp line
            lines.append(
                f"{self.format_timestamp_vtt(start)} --> {self.format_timestamp_vtt(end)}"
            )

            # Add text with optional voice tag
            if include_speakers and segment.get("speaker_id"):
                text = f"<v {segment['speaker_id']}>{text}"

            # Wrap text
            wrapped_text = self.wrap_text(text, max_line_length, max_lines)
            lines.append(wrapped_text)
            lines.append("")  # Blank line separator

        return "\n".join(lines)

    def export_txt(
        self,
        transcript: dict[str, Any],
        include_speakers: bool = True,
        max_line_length: int = 80,
    ) -> str:
        """Export transcript as plain text.

        Uses word-level precision when available, with speaker labels
        on speaker change. Text is word-wrapped at max_line_length.

        Format:
            SPEAKER_00: Welcome to the show. Thanks for having me.

            SPEAKER_01: It's great to be here.

        Args:
            transcript: Transcript dict with words or segments
            include_speakers: Whether to include speaker labels on change
            max_line_length: Max characters per line (default 80)

        Returns:
            Plain text formatted string
        """
        # Prefer words for precision, fall back to segments
        words = transcript.get("words", [])

        if words:
            return self._export_txt_from_words(words, include_speakers, max_line_length)

        # Fall back to segments
        return self._export_txt_from_segments(
            transcript.get("segments", []), include_speakers, max_line_length
        )

    def _export_txt_from_words(
        self,
        words: list[dict[str, Any]],
        include_speakers: bool,
        max_line_length: int = 80,
    ) -> str:
        """Generate plain text from word-level data."""
        if not words:
            return ""

        lines: list[str] = []
        current_speaker = None
        current_text_parts: list[str] = []

        def flush_paragraph() -> None:
            """Flush current paragraph with word-wrapping."""
            nonlocal current_text_parts
            if not current_text_parts:
                return

            text = "".join(current_text_parts).strip()
            if not text:
                current_text_parts = []
                return

            if include_speakers and current_speaker:
                prefix = f"{current_speaker}: "
                # Wrap with hanging indent for speaker prefix
                wrapped = textwrap.fill(
                    text,
                    width=max_line_length,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            else:
                wrapped = textwrap.fill(text, width=max_line_length)

            lines.append(wrapped)
            lines.append("")  # Blank line between paragraphs
            current_text_parts = []

        for word in words:
            word_text = word.get("text", "")
            speaker = word.get("speaker_id")

            # Skip audio events for plain text
            if word.get("type") == "audio_event":
                continue

            # Check for speaker change (always track, but only label if include_speakers)
            if speaker != current_speaker and speaker is not None:
                flush_paragraph()
                current_speaker = speaker

            current_text_parts.append(word_text)

        # Flush remaining text
        flush_paragraph()

        # Remove trailing blank line
        if lines and lines[-1] == "":
            lines.pop()

        return "\n".join(lines)

    def _export_txt_from_segments(
        self,
        segments: list[dict[str, Any]],
        include_speakers: bool,
        max_line_length: int = 80,
    ) -> str:
        """Generate plain text from segment-level data."""
        if not segments:
            return ""

        lines: list[str] = []
        current_speaker = None

        for segment in segments:
            text = segment.get("text", "").strip()
            speaker = segment.get("speaker_id")

            if not text:
                continue

            # Check for speaker change
            if include_speakers and speaker != current_speaker and speaker is not None:
                if lines:
                    lines.append("")  # Blank line between speakers
                current_speaker = speaker

            if include_speakers and current_speaker:
                prefix = f"{current_speaker}: "
                # Wrap with hanging indent for speaker prefix
                wrapped = textwrap.fill(
                    text,
                    width=max_line_length,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            else:
                wrapped = textwrap.fill(text, width=max_line_length)

            lines.append(wrapped)

        return "\n".join(lines)

    def export_json(self, transcript: dict[str, Any]) -> str:
        """Export transcript as JSON.

        Args:
            transcript: Transcript dict

        Returns:
            JSON formatted string
        """
        return json.dumps(transcript, indent=2, ensure_ascii=False)

    def export(
        self,
        transcript: dict[str, Any],
        fmt: ExportFormat | str,
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> str:
        """Export transcript in specified format.

        Args:
            transcript: Transcript dict from S3
            fmt: Export format (srt, vtt, txt, json)
            include_speakers: Whether to include speaker labels
            max_line_length: Max chars per line (42 for subtitles, 80 for TXT)
            max_lines: Max lines per subtitle block (SRT/VTT only)

        Returns:
            Formatted transcript string

        Raises:
            ValueError: If format is not supported
        """
        # Normalize format
        export_fmt: ExportFormat
        if isinstance(fmt, str):
            fmt_lower = fmt.lower()
            try:
                export_fmt = ExportFormat(fmt_lower)
            except ValueError as exc:
                valid = ", ".join(
                    f.value for f in ExportFormat if f != ExportFormat.WEBVTT
                )
                raise ValueError(
                    f"Unsupported format: {fmt}. Supported: {valid}"
                ) from exc
        else:
            export_fmt = fmt

        if export_fmt == ExportFormat.SRT:
            return self.export_srt(
                transcript, include_speakers, max_line_length, max_lines
            )
        if export_fmt in (ExportFormat.VTT, ExportFormat.WEBVTT):
            return self.export_vtt(
                transcript, include_speakers, max_line_length, max_lines
            )
        if export_fmt == ExportFormat.TXT:
            # Use larger line length for plain text (80) unless explicitly set smaller
            txt_line_length = max_line_length if max_line_length > 42 else 80
            return self.export_txt(transcript, include_speakers, txt_line_length)
        if export_fmt == ExportFormat.JSON:
            return self.export_json(transcript)

        raise ValueError(f"Unsupported format: {export_fmt}")

    def get_content_type(self, fmt: ExportFormat | str) -> str:
        """Get the Content-Type header for a format.

        Args:
            fmt: Export format

        Returns:
            Content-Type string
        """
        export_fmt = ExportFormat(fmt.lower()) if isinstance(fmt, str) else fmt

        content_types = {
            ExportFormat.SRT: "text/plain; charset=utf-8",
            ExportFormat.VTT: "text/vtt; charset=utf-8",
            ExportFormat.WEBVTT: "text/vtt; charset=utf-8",
            ExportFormat.TXT: "text/plain; charset=utf-8",
            ExportFormat.JSON: "application/json; charset=utf-8",
        }
        return content_types.get(export_fmt, "application/octet-stream")

    def get_file_extension(self, fmt: ExportFormat | str) -> str:
        """Get the file extension for a format.

        Args:
            fmt: Export format

        Returns:
            File extension without dot
        """
        export_fmt = ExportFormat(fmt.lower()) if isinstance(fmt, str) else fmt

        # webvtt alias uses .vtt extension
        if export_fmt == ExportFormat.WEBVTT:
            return "vtt"

        return export_fmt.value

    def validate_format(self, format_str: str) -> ExportFormat:
        """Validate and parse export format string.

        Args:
            format_str: Format string (e.g., "srt", "vtt", "json")

        Returns:
            Validated ExportFormat enum

        Raises:
            HTTPException: If format is not supported (400)
        """
        format_lower = format_str.lower()
        try:
            return ExportFormat(format_lower)
        except ValueError:
            valid_formats = ", ".join(
                f.value for f in ExportFormat if f != ExportFormat.WEBVTT
            )
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: {format_str}. Supported formats: {valid_formats}",
            ) from None

    def create_export_response(
        self,
        transcript: dict[str, Any] | None,
        export_format: ExportFormat,
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> Response:
        """Create a FastAPI Response with exported transcript.

        Args:
            transcript: Transcript dict from storage (or None)
            export_format: Target export format
            include_speakers: Whether to include speaker labels
            max_line_length: Max characters per subtitle line
            max_lines: Max lines per subtitle block

        Returns:
            FastAPI Response with appropriate content type and headers
        """
        # Handle empty transcript
        if transcript is None:
            transcript = {}

        # Generate export
        content = self.export(
            transcript=transcript,
            fmt=export_format,
            include_speakers=include_speakers,
            max_line_length=max_line_length,
            max_lines=max_lines,
        )

        # Get content type and file extension
        content_type = self.get_content_type(export_format)
        file_ext = self.get_file_extension(export_format)
        filename = f"transcript.{file_ext}"

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
