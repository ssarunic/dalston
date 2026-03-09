"""Unit tests for audio ingestion service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

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
