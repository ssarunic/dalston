"""Audio ingestion service for handling file uploads and URL downloads.

Consolidates the common audio acquisition logic used by both the native
transcription API and the ElevenLabs-compatible speech-to-text API.
"""

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

from dalston.config import Settings
from dalston.gateway.services.audio_probe import (
    AudioMetadata,
    InvalidAudioError,
    probe_audio,
)
from dalston.gateway.services.audio_url import (
    AudioUrlError,
    download_audio_from_url,
)


@dataclass
class IngestedAudio:
    """Result of ingesting audio from file upload or URL."""

    content: bytes
    filename: str
    metadata: AudioMetadata


class AudioIngestionService:
    """Service for ingesting audio from file uploads or URLs.

    Handles:
    - Input validation (exactly one of file/url required)
    - URL downloading with size limits and timeouts
    - File content reading from uploads
    - Audio probing for metadata extraction and validation
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def ingest(
        self,
        file: UploadFile | None,
        url: str | None,
    ) -> IngestedAudio:
        """Ingest audio from either a file upload or URL.

        Args:
            file: Uploaded file from FastAPI (mutually exclusive with url)
            url: URL to download audio from (mutually exclusive with file)

        Returns:
            IngestedAudio with content, filename, and probed metadata

        Raises:
            HTTPException: On validation errors (400) or invalid audio (400)
        """
        # Validate input: exactly one of file or url required
        if file is None and url is None:
            raise HTTPException(
                status_code=400,
                detail="Either 'file' or 'audio_url' must be provided",
            )
        if file is not None and url is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'file' or 'audio_url', not both",
            )

        # Acquire content from URL or file
        if url is not None:
            content, filename = await self._download_from_url(url)
        else:
            content, filename = await self._read_from_file(file)  # type: ignore[arg-type]

        # Probe audio to extract metadata and validate
        # Uses to_thread() because probe_audio uses tinytag synchronously
        try:
            metadata = await asyncio.to_thread(probe_audio, content, filename)
        except InvalidAudioError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        return IngestedAudio(
            content=content,
            filename=filename,
            metadata=metadata,
        )

    async def _download_from_url(self, url: str) -> tuple[bytes, str]:
        """Download audio from URL.

        Args:
            url: URL to download from

        Returns:
            Tuple of (content bytes, filename)

        Raises:
            HTTPException: On download errors
        """
        try:
            max_size = int(self.settings.audio_url_max_size_gb * 1024 * 1024 * 1024)
            downloaded = await download_audio_from_url(
                url=url,
                max_size=max_size,
                timeout=self.settings.audio_url_timeout_seconds,
            )
            return downloaded.content, downloaded.filename
        except AudioUrlError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    async def _read_from_file(self, file: UploadFile) -> tuple[bytes, str]:
        """Read content from uploaded file.

        Args:
            file: Uploaded file from FastAPI

        Returns:
            Tuple of (content bytes, filename)

        Raises:
            HTTPException: If file has no filename
        """
        if not file.filename:
            raise HTTPException(status_code=400, detail="File must have a filename")
        content = await file.read()
        return content, file.filename
