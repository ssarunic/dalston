"""Unit tests for audio URL download service."""

import pytest

from dalston.gateway.services.audio_url import (
    InvalidUrlError,
    UnsupportedContentTypeError,
    _convert_dropbox_url,
    _convert_google_drive_url,
    _extract_filename_from_response,
    _extract_google_drive_file_id,
    _normalize_url,
    _validate_content_type,
)


class TestGoogleDriveUrlParsing:
    """Tests for Google Drive URL parsing."""

    def test_extract_file_id_from_view_url(self) -> None:
        """Extract file ID from /file/d/{id}/view format."""
        url = "https://drive.google.com/file/d/1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX/view?usp=share_link"
        file_id = _extract_google_drive_file_id(url)
        assert file_id == "1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"

    def test_extract_file_id_from_open_url(self) -> None:
        """Extract file ID from ?id= format."""
        url = "https://drive.google.com/open?id=1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"
        file_id = _extract_google_drive_file_id(url)
        assert file_id == "1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"

    def test_extract_file_id_from_uc_url(self) -> None:
        """Extract file ID from uc?id= format."""
        url = "https://drive.google.com/uc?id=1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"
        file_id = _extract_google_drive_file_id(url)
        assert file_id == "1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"

    def test_returns_none_for_non_gdrive_url(self) -> None:
        """Return None for non-Google Drive URLs."""
        url = "https://example.com/audio.mp3"
        file_id = _extract_google_drive_file_id(url)
        assert file_id is None

    def test_convert_gdrive_url_to_direct_download(self) -> None:
        """Convert Google Drive share link to direct download URL."""
        url = "https://drive.google.com/file/d/1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX/view"
        direct_url = _convert_google_drive_url(url)
        assert (
            direct_url
            == "https://drive.google.com/uc?export=download&id=1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX"
        )

    def test_convert_gdrive_returns_none_for_non_gdrive(self) -> None:
        """Return None for non-Google Drive URLs."""
        url = "https://example.com/audio.mp3"
        direct_url = _convert_google_drive_url(url)
        assert direct_url is None


class TestDropboxUrlParsing:
    """Tests for Dropbox URL parsing."""

    def test_convert_dropbox_dl0_to_dl1(self) -> None:
        """Convert dl=0 to dl=1 for direct download."""
        url = "https://www.dropbox.com/s/abc123/audio.mp3?dl=0"
        direct_url = _convert_dropbox_url(url)
        assert direct_url == "https://www.dropbox.com/s/abc123/audio.mp3?dl=1"

    def test_add_dl1_if_missing(self) -> None:
        """Add dl=1 if no dl parameter present."""
        url = "https://www.dropbox.com/s/abc123/audio.mp3"
        direct_url = _convert_dropbox_url(url)
        assert direct_url == "https://www.dropbox.com/s/abc123/audio.mp3?dl=1"

    def test_add_dl1_with_existing_params(self) -> None:
        """Add dl=1 when other params exist but no dl."""
        url = "https://www.dropbox.com/s/abc123/audio.mp3?foo=bar"
        direct_url = _convert_dropbox_url(url)
        assert direct_url == "https://www.dropbox.com/s/abc123/audio.mp3?foo=bar&dl=1"

    def test_returns_none_for_non_dropbox_url(self) -> None:
        """Return None for non-Dropbox URLs."""
        url = "https://example.com/audio.mp3"
        direct_url = _convert_dropbox_url(url)
        assert direct_url is None


class TestUrlNormalization:
    """Tests for URL normalization."""

    def test_empty_url_raises_error(self) -> None:
        """Empty URL raises InvalidUrlError."""
        with pytest.raises(InvalidUrlError, match="cannot be empty"):
            _normalize_url("")

    def test_unsupported_scheme_raises_error(self) -> None:
        """Non-HTTP(S) scheme raises InvalidUrlError."""
        with pytest.raises(InvalidUrlError, match="Unsupported URL scheme"):
            _normalize_url("ftp://example.com/audio.mp3")

    def test_http_upgraded_to_https(self) -> None:
        """HTTP URLs are upgraded to HTTPS."""
        url = "http://example.com/audio.mp3"
        result = _normalize_url(url)
        assert result.startswith("https://")

    def test_https_url_unchanged(self) -> None:
        """HTTPS URLs pass through unchanged."""
        url = "https://example.com/audio.mp3"
        result = _normalize_url(url)
        assert result == url

    def test_gdrive_url_converted(self) -> None:
        """Google Drive URLs are converted to direct download."""
        url = "https://drive.google.com/file/d/abc123/view"
        result = _normalize_url(url)
        assert "export=download" in result

    def test_dropbox_url_converted(self) -> None:
        """Dropbox URLs are converted to direct download."""
        url = "https://www.dropbox.com/s/abc123/audio.mp3?dl=0"
        result = _normalize_url(url)
        assert "dl=1" in result


class TestContentTypeValidation:
    """Tests for content type validation."""

    def test_valid_audio_content_types(self) -> None:
        """Valid audio content types pass validation."""
        valid_types = [
            "audio/mpeg",
            "audio/wav",
            "audio/flac",
            "audio/ogg",
            "audio/mp4",
            "audio/webm",
        ]
        for ct in valid_types:
            _validate_content_type(ct, "audio.mp3")  # Should not raise

    def test_octet_stream_with_audio_extension(self) -> None:
        """application/octet-stream allowed with audio extension."""
        _validate_content_type(
            "application/octet-stream", "audio.mp3"
        )  # Should not raise

    def test_invalid_content_type_raises_error(self) -> None:
        """Invalid content type raises UnsupportedContentTypeError."""
        with pytest.raises(
            UnsupportedContentTypeError, match="Unsupported content type"
        ):
            _validate_content_type("text/html", "page.html")

    def test_octet_stream_without_audio_extension_raises(self) -> None:
        """application/octet-stream without audio extension raises error."""
        with pytest.raises(UnsupportedContentTypeError, match="Could not verify"):
            _validate_content_type("application/octet-stream", "file.txt")

    def test_content_type_with_charset_normalized(self) -> None:
        """Content type with charset is properly normalized."""
        _validate_content_type(
            "audio/mpeg; charset=utf-8", "audio.mp3"
        )  # Should not raise


class TestFilenameExtraction:
    """Tests for filename extraction from HTTP responses."""

    def test_extract_from_content_disposition(self) -> None:
        """Extract filename from Content-Disposition header."""

        class MockResponse:
            headers = {"content-disposition": 'attachment; filename="meeting.mp3"'}

        filename = _extract_filename_from_response(
            MockResponse(), "https://example.com/download"
        )
        assert filename == "meeting.mp3"

    def test_extract_from_url_path(self) -> None:
        """Extract filename from URL path."""

        class MockResponse:
            headers = {"content-type": "audio/mpeg"}

        filename = _extract_filename_from_response(
            MockResponse(), "https://example.com/path/audio.mp3"
        )
        assert filename == "audio.mp3"

    def test_fallback_to_content_type(self) -> None:
        """Fall back to content type when no filename available."""

        class MockResponse:
            headers = {"content-type": "audio/wav"}

        filename = _extract_filename_from_response(
            MockResponse(), "https://example.com/download"
        )
        assert filename == "audio.wav"

    def test_default_filename(self) -> None:
        """Return default filename when nothing else available."""

        class MockResponse:
            headers = {}

        filename = _extract_filename_from_response(
            MockResponse(), "https://example.com/"
        )
        assert filename == "audio.bin"
