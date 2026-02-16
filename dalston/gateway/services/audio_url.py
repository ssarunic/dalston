"""Audio URL download service for fetching audio from remote URLs.

Supports:
- Direct HTTPS URLs (S3 presigned, GCS presigned, public URLs)
- Google Drive share links (automatic conversion to direct download)
- Dropbox share links (automatic conversion to direct download)

Security:
- HTTPS required (HTTP auto-upgraded)
- Maximum file size limit
- Download timeout
- Content-Type validation
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

logger = structlog.get_logger()

# Configuration
MAX_DOWNLOAD_SIZE_BYTES = 3 * 1024 * 1024 * 1024  # 3GB max
DOWNLOAD_TIMEOUT_SECONDS = 300  # 5 minutes
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# Supported audio content types
AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "audio/vorbis",
    "audio/opus",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/aac",
    "audio/webm",
    "audio/aiff",
    "audio/x-aiff",
    "video/mp4",  # May contain audio
    "video/webm",  # May contain audio
}

# Generic binary types that require extension validation
GENERIC_BINARY_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
}

# File extensions that indicate audio
AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".m4a",
    ".aac",
    ".webm",
    ".aiff",
    ".wma",
    ".mp4",
}


class AudioUrlError(Exception):
    """Base error for audio URL operations."""

    pass


class InvalidUrlError(AudioUrlError):
    """URL is invalid or not supported."""

    pass


class DownloadError(AudioUrlError):
    """Failed to download audio from URL."""

    pass


class FileTooLargeError(AudioUrlError):
    """Downloaded file exceeds size limit."""

    pass


class UnsupportedContentTypeError(AudioUrlError):
    """Content type is not a supported audio format."""

    pass


@dataclass
class DownloadedAudio:
    """Result of downloading audio from URL."""

    content: bytes
    filename: str
    content_type: str | None
    size: int


def _extract_google_drive_file_id(url: str) -> str | None:
    """Extract file ID from Google Drive URL.

    Supports:
    - https://drive.google.com/file/d/{file_id}/view
    - https://drive.google.com/open?id={file_id}
    - https://drive.google.com/uc?id={file_id}
    """
    parsed = urlparse(url)

    if parsed.netloc not in ("drive.google.com", "docs.google.com"):
        return None

    # Format: /file/d/{file_id}/view
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)

    # Format: ?id={file_id}
    query_params = parse_qs(parsed.query)
    if "id" in query_params:
        return query_params["id"][0]

    return None


def _convert_google_drive_url(url: str) -> str | None:
    """Convert Google Drive share link to direct download URL."""
    file_id = _extract_google_drive_file_id(url)
    if file_id:
        # Use export download endpoint for direct download
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return None


def _convert_dropbox_url(url: str) -> str | None:
    """Convert Dropbox share link to direct download URL."""
    parsed = urlparse(url)

    if parsed.netloc not in ("www.dropbox.com", "dropbox.com"):
        return None

    # Replace dl=0 with dl=1 for direct download
    if "dl=0" in url:
        return url.replace("dl=0", "dl=1")
    elif "dl=" not in url:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}dl=1"

    return url


def _normalize_url(url: str) -> str:
    """Normalize and validate URL, converting share links to direct downloads."""
    url = url.strip()

    # Basic validation
    if not url:
        raise InvalidUrlError("URL cannot be empty")

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise InvalidUrlError(f"Invalid URL format: {e}") from e

    # Require HTTP/HTTPS
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError(
            f"Unsupported URL scheme: {parsed.scheme}. Only HTTP/HTTPS allowed."
        )

    # Upgrade HTTP to HTTPS
    if parsed.scheme == "http":
        url = "https" + url[4:]
        logger.info("url_upgraded_to_https", original_scheme="http")

    # Convert Google Drive links
    gdrive_url = _convert_google_drive_url(url)
    if gdrive_url:
        logger.info("url_converted", source="google_drive", original=url[:50])
        return gdrive_url

    # Convert Dropbox links
    dropbox_url = _convert_dropbox_url(url)
    if dropbox_url:
        logger.info("url_converted", source="dropbox", original=url[:50])
        return dropbox_url

    return url


def _extract_filename_from_response(response: httpx.Response, url: str) -> str:
    """Extract filename from response headers or URL."""
    # Try Content-Disposition header
    content_disposition = response.headers.get("content-disposition", "")
    if "filename=" in content_disposition:
        # Extract filename from header
        match = re.search(r'filename[*]?=["\']?([^"\';]+)', content_disposition)
        if match:
            return match.group(1).strip()

    # Fall back to URL path
    parsed = urlparse(url)
    path = parsed.path
    if "/" in path:
        filename = path.rsplit("/", 1)[-1]
        # Remove query params if any leaked in
        if "?" in filename:
            filename = filename.split("?")[0]
        if filename and "." in filename:
            return filename

    # Default filename based on content type
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    ext_map = {
        "audio/mpeg": "audio.mp3",
        "audio/mp3": "audio.mp3",
        "audio/wav": "audio.wav",
        "audio/x-wav": "audio.wav",
        "audio/flac": "audio.flac",
        "audio/ogg": "audio.ogg",
        "audio/mp4": "audio.m4a",
        "audio/m4a": "audio.m4a",
        "audio/webm": "audio.webm",
    }
    return ext_map.get(content_type, "audio.bin")


def _validate_content_type(content_type: str | None, filename: str) -> None:
    """Validate that content type or filename indicates audio."""
    if content_type:
        # Normalize content type (remove charset, etc.)
        ct = content_type.split(";")[0].strip().lower()

        # Check if it's a known audio type
        if ct in AUDIO_CONTENT_TYPES:
            return

        # For generic binary types, fall through to extension check
        if ct not in GENERIC_BINARY_TYPES:
            raise UnsupportedContentTypeError(
                f"Unsupported content type: {content_type}. "
                "Expected audio file (MP3, WAV, FLAC, OGG, M4A, etc.)"
            )

    # Check file extension as fallback (required for generic binary types)
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in AUDIO_EXTENSIONS:
        raise UnsupportedContentTypeError(
            f"Could not verify audio format. Content-Type: {content_type}, "
            f"filename: {filename}. Supported: MP3, WAV, FLAC, OGG, M4A, etc."
        )


async def download_audio_from_url(
    url: str,
    max_size: int = MAX_DOWNLOAD_SIZE_BYTES,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> DownloadedAudio:
    """Download audio file from URL.

    Args:
        url: URL to download audio from (HTTPS, Google Drive, Dropbox, etc.)
        max_size: Maximum allowed file size in bytes
        timeout: Download timeout in seconds

    Returns:
        DownloadedAudio with content, filename, content_type, and size

    Raises:
        InvalidUrlError: URL is invalid or unsupported
        DownloadError: Failed to download (network error, 4xx/5xx response)
        FileTooLargeError: File exceeds max_size
        UnsupportedContentTypeError: File is not a supported audio format
    """
    # Normalize URL (convert share links, upgrade to HTTPS)
    normalized_url = _normalize_url(url)

    logger.info(
        "audio_url_download_started",
        url=normalized_url[:100],  # Truncate for logging
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            max_redirects=10,
        ) as client:
            # Start streaming download
            async with client.stream("GET", normalized_url) as response:
                # Check for HTTP errors
                if response.status_code >= 400:
                    raise DownloadError(
                        f"HTTP {response.status_code}: Failed to download from URL"
                    )

                # Check Content-Length if available
                content_length = response.headers.get("content-length")
                if content_length:
                    size = int(content_length)
                    if size > max_size:
                        raise FileTooLargeError(
                            f"File too large: {size / (1024**3):.2f} GB. "
                            f"Maximum: {max_size / (1024**3):.1f} GB"
                        )

                # Extract filename and content type
                content_type = response.headers.get("content-type")
                filename = _extract_filename_from_response(response, normalized_url)

                # Validate content type
                _validate_content_type(content_type, filename)

                # Download content with size check
                chunks: list[bytes] = []
                total_size = 0

                async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise FileTooLargeError(
                            f"Download exceeded maximum size: {max_size / (1024**3):.1f} GB"
                        )
                    chunks.append(chunk)

                content = b"".join(chunks)

                logger.info(
                    "audio_url_download_completed",
                    size_bytes=total_size,
                    filename=filename,
                    content_type=content_type,
                )

                return DownloadedAudio(
                    content=content,
                    filename=filename,
                    content_type=content_type,
                    size=total_size,
                )

    except httpx.TimeoutException as e:
        raise DownloadError(
            f"Download timed out after {timeout} seconds. "
            "Try uploading the file directly instead."
        ) from e
    except httpx.ConnectError as e:
        raise DownloadError(f"Failed to connect to URL: {e}") from e
    except httpx.TooManyRedirects as e:
        raise DownloadError("Too many redirects while downloading") from e
    except (FileTooLargeError, UnsupportedContentTypeError, DownloadError):
        raise
    except Exception as e:
        raise DownloadError(f"Unexpected error downloading audio: {e}") from e
