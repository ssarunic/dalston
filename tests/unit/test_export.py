"""Unit tests for ExportService."""

import json

import pytest

from dalston.gateway.services.export import ExportFormat, ExportService


@pytest.fixture
def export_service() -> ExportService:
    """Create ExportService instance."""
    return ExportService()


@pytest.fixture
def sample_transcript() -> dict:
    """Sample transcript with segments and words."""
    return {
        "metadata": {"language": "en", "duration": 10.5},
        "text": "Welcome to the show. Thanks for having me.",
        "segments": [
            {
                "id": "seg_001",
                "start": 0.0,
                "end": 2.5,
                "text": "Welcome to the show.",
                "speaker_id": "SPEAKER_00",
            },
            {
                "id": "seg_002",
                "start": 2.8,
                "end": 5.2,
                "text": "Thanks for having me.",
                "speaker_id": "SPEAKER_01",
            },
        ],
        "words": [
            {"text": "Welcome", "start": 0.0, "end": 0.4, "speaker_id": "SPEAKER_00"},
            {"text": " ", "start": 0.4, "end": 0.45, "type": "spacing"},
            {"text": "to", "start": 0.45, "end": 0.6, "speaker_id": "SPEAKER_00"},
            {"text": " ", "start": 0.6, "end": 0.65, "type": "spacing"},
            {"text": "the", "start": 0.65, "end": 0.8, "speaker_id": "SPEAKER_00"},
            {"text": " ", "start": 0.8, "end": 0.85, "type": "spacing"},
            {"text": "show.", "start": 0.85, "end": 2.5, "speaker_id": "SPEAKER_00"},
            {"text": "Thanks", "start": 2.8, "end": 3.2, "speaker_id": "SPEAKER_01"},
            {"text": " ", "start": 3.2, "end": 3.25, "type": "spacing"},
            {"text": "for", "start": 3.25, "end": 3.5, "speaker_id": "SPEAKER_01"},
            {"text": " ", "start": 3.5, "end": 3.55, "type": "spacing"},
            {"text": "having", "start": 3.55, "end": 4.0, "speaker_id": "SPEAKER_01"},
            {"text": " ", "start": 4.0, "end": 4.05, "type": "spacing"},
            {"text": "me.", "start": 4.05, "end": 5.2, "speaker_id": "SPEAKER_01"},
        ],
    }


class TestExportFormat:
    """Tests for ExportFormat enum."""

    def test_srt_format(self):
        assert ExportFormat.SRT.value == "srt"

    def test_vtt_format(self):
        assert ExportFormat.VTT.value == "vtt"

    def test_webvtt_alias(self):
        assert ExportFormat.WEBVTT.value == "webvtt"

    def test_txt_format(self):
        assert ExportFormat.TXT.value == "txt"

    def test_json_format(self):
        assert ExportFormat.JSON.value == "json"


class TestTimestampFormatting:
    """Tests for timestamp formatting."""

    def test_srt_timestamp_zero(self, export_service: ExportService):
        assert export_service.format_timestamp_srt(0.0) == "00:00:00,000"

    def test_srt_timestamp_with_millis(self, export_service: ExportService):
        assert export_service.format_timestamp_srt(1.5) == "00:00:01,500"

    def test_srt_timestamp_with_minutes(self, export_service: ExportService):
        assert export_service.format_timestamp_srt(65.123) == "00:01:05,123"

    def test_srt_timestamp_with_hours(self, export_service: ExportService):
        # Use exact value to avoid floating-point precision issues
        assert export_service.format_timestamp_srt(3661.5) == "01:01:01,500"

    def test_vtt_timestamp_zero(self, export_service: ExportService):
        assert export_service.format_timestamp_vtt(0.0) == "00:00:00.000"

    def test_vtt_timestamp_with_millis(self, export_service: ExportService):
        assert export_service.format_timestamp_vtt(1.5) == "00:00:01.500"

    def test_vtt_uses_dot_separator(self, export_service: ExportService):
        # VTT uses dot, SRT uses comma
        srt = export_service.format_timestamp_srt(1.5)
        vtt = export_service.format_timestamp_vtt(1.5)
        assert "," in srt
        assert "." in vtt


class TestSRTExport:
    """Tests for SRT format export."""

    def test_srt_basic_structure(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_srt(sample_transcript)
        lines = result.strip().split("\n")

        # First subtitle block
        assert lines[0] == "1"
        assert "-->" in lines[1]
        assert "Welcome to the show." in lines[2]

    def test_srt_with_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_srt(sample_transcript, include_speakers=True)
        assert "[SPEAKER_00]" in result
        assert "[SPEAKER_01]" in result

    def test_srt_without_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_srt(sample_transcript, include_speakers=False)
        assert "[SPEAKER_00]" not in result
        assert "[SPEAKER_01]" not in result

    def test_srt_timestamp_format(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_srt(sample_transcript)
        # SRT uses comma separator
        assert "00:00:00,000 --> 00:00:02,500" in result

    def test_srt_empty_transcript(self, export_service: ExportService):
        result = export_service.export_srt({})
        assert result == ""

    def test_srt_sequential_numbering(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_srt(sample_transcript)
        lines = result.strip().split("\n")
        # Find all subtitle numbers (lines that are just digits)
        numbers = [line for line in lines if line.isdigit()]
        assert numbers == ["1", "2"]


class TestVTTExport:
    """Tests for VTT format export."""

    def test_vtt_header(self, export_service: ExportService, sample_transcript: dict):
        result = export_service.export_vtt(sample_transcript)
        assert result.startswith("WEBVTT")

    def test_vtt_with_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_vtt(sample_transcript, include_speakers=True)
        assert "<v SPEAKER_00>" in result
        assert "<v SPEAKER_01>" in result

    def test_vtt_without_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_vtt(sample_transcript, include_speakers=False)
        assert "<v " not in result

    def test_vtt_timestamp_format(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_vtt(sample_transcript)
        # VTT uses dot separator
        assert "00:00:00.000 --> 00:00:02.500" in result

    def test_vtt_empty_transcript(self, export_service: ExportService):
        result = export_service.export_vtt({})
        assert result.strip() == "WEBVTT"


class TestTXTExport:
    """Tests for TXT format export."""

    def test_txt_with_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_txt(sample_transcript, include_speakers=True)
        assert "SPEAKER_00:" in result
        assert "SPEAKER_01:" in result

    def test_txt_without_speakers(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_txt(sample_transcript, include_speakers=False)
        assert "SPEAKER_00" not in result
        assert "SPEAKER_01" not in result

    def test_txt_uses_words_when_available(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_txt(sample_transcript)
        # Words should be joined with proper spacing
        assert "Welcome to the show." in result

    def test_txt_falls_back_to_segments(self, export_service: ExportService):
        transcript = {
            "segments": [
                {"text": "Hello world.", "speaker_id": "SPEAKER_00"},
            ]
        }
        result = export_service.export_txt(transcript, include_speakers=True)
        assert "SPEAKER_00: Hello world." in result

    def test_txt_empty_transcript(self, export_service: ExportService):
        result = export_service.export_txt({})
        assert result == ""

    def test_txt_speaker_change_creates_paragraph(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_txt(sample_transcript, include_speakers=False)
        # Should have blank line between speakers
        lines = result.split("\n")
        assert "" in lines  # blank line exists

    def test_txt_word_wrap_long_text(self, export_service: ExportService):
        """Test that long text is wrapped at max_line_length."""
        long_text = "This is a very long sentence that should be wrapped across multiple lines when exported to plain text format."
        transcript = {"segments": [{"text": long_text, "speaker_id": "SPEAKER_00"}]}
        result = export_service.export_txt(
            transcript, include_speakers=False, max_line_length=40
        )
        lines = result.split("\n")
        # All lines should be <= 40 chars
        for line in lines:
            assert len(line) <= 40, f"Line too long: {len(line)} chars"

    def test_txt_word_wrap_with_speaker_prefix(self, export_service: ExportService):
        """Test word wrapping maintains speaker prefix alignment."""
        long_text = "This is a very long sentence that should be wrapped with proper indentation for speaker labels."
        transcript = {"segments": [{"text": long_text, "speaker_id": "SPEAKER_00"}]}
        result = export_service.export_txt(
            transcript, include_speakers=True, max_line_length=50
        )
        lines = result.split("\n")
        # First line should have speaker prefix
        assert lines[0].startswith("SPEAKER_00:")
        # Subsequent lines should be indented
        if len(lines) > 1:
            # Check that continuation lines are indented to align with text after speaker
            assert lines[1].startswith(" " * len("SPEAKER_00: "))

    def test_txt_default_line_length_is_80(self, export_service: ExportService):
        """Test that default max_line_length for TXT is 80."""
        # Create text that would wrap at 42 but not at 80
        text = "A" * 60  # 60 chars, would wrap at 42 but not at 80
        transcript = {"segments": [{"text": text}]}
        result = export_service.export_txt(transcript, include_speakers=False)
        # Should NOT be wrapped since 60 < 80
        assert "\n" not in result.strip()


class TestJSONExport:
    """Tests for JSON format export."""

    def test_json_valid_output(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_json(sample_transcript)
        parsed = json.loads(result)
        assert parsed == sample_transcript

    def test_json_empty_transcript(self, export_service: ExportService):
        result = export_service.export_json({})
        assert json.loads(result) == {}

    def test_json_pretty_printed(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export_json(sample_transcript)
        # Should have newlines (pretty printed)
        assert "\n" in result


class TestExportMethod:
    """Tests for the main export() method."""

    def test_export_srt(self, export_service: ExportService, sample_transcript: dict):
        result = export_service.export(sample_transcript, "srt")
        assert "-->" in result
        assert "[SPEAKER_00]" in result

    def test_export_vtt(self, export_service: ExportService, sample_transcript: dict):
        result = export_service.export(sample_transcript, "vtt")
        assert result.startswith("WEBVTT")

    def test_export_webvtt_alias(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export(sample_transcript, "webvtt")
        assert result.startswith("WEBVTT")

    def test_export_txt(self, export_service: ExportService, sample_transcript: dict):
        result = export_service.export(sample_transcript, "txt")
        assert "SPEAKER_00:" in result

    def test_export_json(self, export_service: ExportService, sample_transcript: dict):
        result = export_service.export(sample_transcript, "json")
        parsed = json.loads(result)
        assert "metadata" in parsed

    def test_export_case_insensitive(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result_lower = export_service.export(sample_transcript, "srt")
        result_upper = export_service.export(sample_transcript, "SRT")
        assert result_lower == result_upper

    def test_export_invalid_format(
        self, export_service: ExportService, sample_transcript: dict
    ):
        with pytest.raises(ValueError, match="Unsupported format"):
            export_service.export(sample_transcript, "invalid")

    def test_export_with_enum(
        self, export_service: ExportService, sample_transcript: dict
    ):
        result = export_service.export(sample_transcript, ExportFormat.SRT)
        assert "-->" in result


class TestContentType:
    """Tests for content type detection."""

    def test_srt_content_type(self, export_service: ExportService):
        assert export_service.get_content_type("srt") == "text/plain; charset=utf-8"

    def test_vtt_content_type(self, export_service: ExportService):
        assert export_service.get_content_type("vtt") == "text/vtt; charset=utf-8"

    def test_webvtt_content_type(self, export_service: ExportService):
        assert export_service.get_content_type("webvtt") == "text/vtt; charset=utf-8"

    def test_txt_content_type(self, export_service: ExportService):
        assert export_service.get_content_type("txt") == "text/plain; charset=utf-8"

    def test_json_content_type(self, export_service: ExportService):
        assert (
            export_service.get_content_type("json") == "application/json; charset=utf-8"
        )


class TestFileExtension:
    """Tests for file extension detection."""

    def test_srt_extension(self, export_service: ExportService):
        assert export_service.get_file_extension("srt") == "srt"

    def test_vtt_extension(self, export_service: ExportService):
        assert export_service.get_file_extension("vtt") == "vtt"

    def test_webvtt_extension_is_vtt(self, export_service: ExportService):
        # webvtt should return .vtt extension
        assert export_service.get_file_extension("webvtt") == "vtt"

    def test_txt_extension(self, export_service: ExportService):
        assert export_service.get_file_extension("txt") == "txt"

    def test_json_extension(self, export_service: ExportService):
        assert export_service.get_file_extension("json") == "json"


class TestTextWrapping:
    """Tests for subtitle text wrapping."""

    def test_short_text_not_wrapped(self, export_service: ExportService):
        result = export_service.wrap_text("Short text", max_line_length=42)
        assert "\n" not in result

    def test_long_text_wrapped(self, export_service: ExportService):
        long_text = "This is a very long piece of text that should be wrapped across multiple lines"
        result = export_service.wrap_text(long_text, max_line_length=30)
        assert "\n" in result

    def test_max_lines_limit(self, export_service: ExportService):
        very_long = " ".join(["word"] * 50)
        result = export_service.wrap_text(very_long, max_line_length=20, max_lines=2)
        lines = result.split("\n")
        assert len(lines) <= 2

    def test_truncation_adds_ellipsis(self, export_service: ExportService):
        very_long = " ".join(["word"] * 50)
        result = export_service.wrap_text(very_long, max_line_length=20, max_lines=2)
        assert result.endswith("...")


class TestValidateFormat:
    """Tests for format validation helper."""

    @pytest.fixture
    def export_service(self) -> ExportService:
        return ExportService()

    def test_validate_format_srt(self, export_service: ExportService):
        fmt = export_service.validate_format("srt")
        assert fmt == ExportFormat.SRT

    def test_validate_format_case_insensitive(self, export_service: ExportService):
        fmt = export_service.validate_format("SRT")
        assert fmt == ExportFormat.SRT

    def test_validate_format_vtt(self, export_service: ExportService):
        fmt = export_service.validate_format("vtt")
        assert fmt == ExportFormat.VTT

    def test_validate_format_webvtt(self, export_service: ExportService):
        fmt = export_service.validate_format("webvtt")
        assert fmt == ExportFormat.WEBVTT

    def test_validate_format_invalid_raises_http_exception(
        self, export_service: ExportService
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            export_service.validate_format("invalid")
        assert exc_info.value.status_code == 400
        assert "Unsupported format" in exc_info.value.detail


class TestCreateExportResponse:
    """Tests for export response creation helper."""

    @pytest.fixture
    def export_service(self) -> ExportService:
        return ExportService()

    @pytest.fixture
    def sample_transcript(self) -> dict:
        return {
            "text": "Hello world",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Hello"},
                {"start": 1.0, "end": 2.0, "text": "world"},
            ],
        }

    def test_create_export_response_srt(
        self, export_service: ExportService, sample_transcript: dict
    ):
        response = export_service.create_export_response(
            transcript=sample_transcript,
            export_format=ExportFormat.SRT,
        )
        assert response.media_type == "text/plain; charset=utf-8"
        assert b"-->" in response.body

    def test_create_export_response_json(
        self, export_service: ExportService, sample_transcript: dict
    ):
        response = export_service.create_export_response(
            transcript=sample_transcript,
            export_format=ExportFormat.JSON,
        )
        assert response.media_type == "application/json; charset=utf-8"
        assert b"Hello world" in response.body

    def test_create_export_response_none_transcript(
        self, export_service: ExportService
    ):
        """Test that None transcript is handled gracefully."""
        response = export_service.create_export_response(
            transcript=None,
            export_format=ExportFormat.JSON,
        )
        assert response.status_code == 200
        assert b"{}" in response.body

    def test_create_export_response_content_disposition(
        self, export_service: ExportService, sample_transcript: dict
    ):
        response = export_service.create_export_response(
            transcript=sample_transcript,
            export_format=ExportFormat.SRT,
        )
        assert "Content-Disposition" in response.headers
        assert "transcript.srt" in response.headers["Content-Disposition"]
