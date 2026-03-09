"""Unit tests for audio ingestion service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from dalston.gateway.services.audio_url import DownloadedAudio
from dalston.gateway.services.ingestion import AudioIngestionService


@pytest.mark.asyncio
async def test_download_from_url_uses_explicit_max_bytes_override() -> None:
    settings = SimpleNamespace(audio_url_max_size_gb=3.0, audio_url_timeout_seconds=10)
    service = AudioIngestionService(settings)

    with patch(
        "dalston.gateway.services.ingestion.download_audio_from_url",
        new=AsyncMock(
            return_value=DownloadedAudio(
                content=b"data",
                filename="test.wav",
                content_type="audio/wav",
                size=4,
            )
        ),
    ) as mock_download:
        content, filename = await service._download_from_url(
            "https://example.com/audio.wav",
            max_bytes=1024,
        )

    assert content == b"data"
    assert filename == "test.wav"
    assert mock_download.await_args.kwargs["max_size"] == 1024


@pytest.mark.asyncio
async def test_download_from_url_uses_settings_limit_by_default() -> None:
    settings = SimpleNamespace(audio_url_max_size_gb=1.5, audio_url_timeout_seconds=10)
    service = AudioIngestionService(settings)

    with patch(
        "dalston.gateway.services.ingestion.download_audio_from_url",
        new=AsyncMock(
            return_value=DownloadedAudio(
                content=b"data",
                filename="test.wav",
                content_type="audio/wav",
                size=4,
            )
        ),
    ) as mock_download:
        await service._download_from_url("https://example.com/audio.wav")

    assert mock_download.await_args.kwargs["max_size"] == int(1.5 * 1024 * 1024 * 1024)


class _FakeUploadFile:
    def __init__(
        self,
        *,
        filename: str,
        chunks: list[bytes] | None = None,
        size: int | None = None,
    ) -> None:
        self.filename = filename
        self.size = size
        self._chunks = list(chunks or [])

    async def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


@pytest.mark.asyncio
async def test_read_from_file_streaming_enforces_max_bytes() -> None:
    settings = SimpleNamespace(audio_url_max_size_gb=1.0, audio_url_timeout_seconds=10)
    service = AudioIngestionService(settings)
    upload = _FakeUploadFile(
        filename="audio.wav",
        chunks=[b"a" * 700, b"b" * 700],
    )

    with pytest.raises(HTTPException) as exc_info:
        await service._read_from_file(upload, max_bytes=1024)

    assert exc_info.value.status_code == 413
    assert "File too large" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_read_from_file_uses_reported_size_for_early_reject() -> None:
    settings = SimpleNamespace(audio_url_max_size_gb=1.0, audio_url_timeout_seconds=10)
    service = AudioIngestionService(settings)
    upload = _FakeUploadFile(filename="audio.wav", size=2048, chunks=[b"data"])
    upload.read = AsyncMock(return_value=b"")  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await service._read_from_file(upload, max_bytes=1024)

    assert exc_info.value.status_code == 413
    upload.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_from_file_streaming_reads_all_chunks() -> None:
    settings = SimpleNamespace(audio_url_max_size_gb=1.0, audio_url_timeout_seconds=10)
    service = AudioIngestionService(settings)
    upload = _FakeUploadFile(
        filename="audio.wav",
        chunks=[b"abc", b"def", b""],
    )

    content, filename = await service._read_from_file(upload, max_bytes=1024)

    assert filename == "audio.wav"
    assert content == b"abcdef"
