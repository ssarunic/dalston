"""Unit tests for display name generation."""

from datetime import UTC, datetime
from unittest.mock import patch

from dalston.common.utils import generate_display_name


class TestGenerateDisplayName:
    """Tests for generate_display_name utility."""

    def test_filename_used_when_provided(self):
        result = generate_display_name(filename="meeting-2025-03-03.wav")

        assert result == "meeting-2025-03-03.wav"

    def test_filename_stripped_of_whitespace(self):
        result = generate_display_name(filename="  recording.mp3  ")

        assert result == "recording.mp3"

    def test_filename_takes_priority_over_url(self):
        result = generate_display_name(
            filename="meeting.wav",
            url="https://example.com/other-file.wav",
        )

        assert result == "meeting.wav"

    def test_url_last_segment_used(self):
        result = generate_display_name(
            url="https://example.com/uploads/quarterly-report.wav"
        )

        assert result == "quarterly-report.wav"

    def test_url_query_params_excluded(self):
        result = generate_display_name(
            url="https://cdn.example.com/audio/file.mp3?token=abc123&expires=9999"
        )

        assert result == "file.mp3"

    def test_url_encoded_segments_decoded(self):
        result = generate_display_name(
            url="https://example.com/audio/my%20meeting%20recording.wav"
        )

        assert result == "my meeting recording.wav"

    def test_url_trailing_slash_handled(self):
        result = generate_display_name(url="https://example.com/audio/recording.wav/")

        assert result == "recording.wav"

    def test_url_root_path_falls_through_to_untitled(self):
        """URL with no meaningful path segment produces untitled."""
        result = generate_display_name(url="https://example.com/")

        assert result.startswith("Untitled")

    def test_untitled_fallback_when_no_filename_or_url(self):
        fixed_time = datetime(2026, 3, 3, 14, 32, tzinfo=UTC)
        with patch("dalston.common.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = generate_display_name()

        assert result == "Untitled \u2014 Mar 03, 2026 14:32"

    def test_untitled_fallback_when_empty_filename(self):
        result = generate_display_name(filename="")

        assert result.startswith("Untitled")

    def test_untitled_fallback_when_whitespace_only_filename(self):
        result = generate_display_name(filename="   ")

        assert result.startswith("Untitled")

    def test_long_filename_truncated_to_255(self):
        long_name = "a" * 300 + ".wav"
        result = generate_display_name(filename=long_name)

        assert len(result) == 255

    def test_url_with_no_path(self):
        result = generate_display_name(url="https://example.com")

        assert result.startswith("Untitled")

    def test_url_with_empty_segment(self):
        result = generate_display_name(url="https://example.com///")

        assert result.startswith("Untitled")

    def test_url_with_presigned_s3(self):
        result = generate_display_name(
            url="https://bucket.s3.amazonaws.com/uploads/audio.wav?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=..."
        )

        assert result == "audio.wav"

    def test_untitled_date_format_single_digit_day_zero_padded(self):
        """Verify single-digit days are zero-padded for cross-platform consistency."""
        fixed_time = datetime(2026, 1, 5, 9, 7, tzinfo=UTC)
        with patch("dalston.common.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = generate_display_name()

        assert result == "Untitled \u2014 Jan 05, 2026 09:07"

    def test_untitled_date_format_double_digit_day(self):
        """Verify double-digit days display correctly."""
        fixed_time = datetime(2026, 12, 25, 15, 45, tzinfo=UTC)
        with patch("dalston.common.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = generate_display_name()

        assert result == "Untitled \u2014 Dec 25, 2026 15:45"
