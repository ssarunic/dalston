"""E2E tests using the official ElevenLabs Python SDK against Dalston."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

elevenlabs = pytest.importorskip(
    "elevenlabs", reason="elevenlabs package not installed"
)


def _resolve_audio_fixture() -> Path:
    candidates = [
        Path(__file__).parent.parent / "audio" / "test_merged.wav",
        Path(__file__).parent.parent / "fixtures" / "test_audio.wav",
    ]
    audio_file = next((p for p in candidates if p.exists()), None)
    if audio_file is None:
        pytest.skip("No audio fixture found for ElevenLabs SDK e2e test")
    return audio_file


def _resolve_stereo_audio_fixture() -> Path:
    stereo = Path(__file__).parent.parent / "audio" / "test_stereo_speakers.wav"
    if not stereo.exists():
        pytest.skip(
            "Stereo audio fixture tests/audio/test_stereo_speakers.wav not found"
        )
    return stereo


def _resolve_sdk_env() -> tuple[str, str]:
    api_key = os.getenv("DALSTON_API_KEY")
    if not api_key:
        pytest.skip("DALSTON_API_KEY environment variable is not set")
    base_url = os.getenv("DALSTON_ELEVENLABS_BASE_URL", "http://localhost:8000")
    return api_key, base_url


@pytest.mark.e2e
def test_elevenlabs_sdk_batch_convert_get_delete():
    """Run convert/get/delete flow with real transcript output."""
    api_key, base_url = _resolve_sdk_env()
    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)
    audio_file = _resolve_audio_fixture()

    with audio_file.open("rb") as f:
        result = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            timestamps_granularity="word",
        )

    assert getattr(result, "text", None)

    transcription_id = getattr(result, "transcription_id", None)
    if transcription_id:
        fetched = client.speech_to_text.transcripts.get(transcription_id)
        assert getattr(fetched, "text", None)
        client.speech_to_text.transcripts.delete(transcription_id)


@pytest.mark.e2e
def test_elevenlabs_sdk_async_convert_polling():
    """Run async convert and poll until transcript is materialized."""
    api_key, base_url = _resolve_sdk_env()
    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)
    audio_file = _resolve_audio_fixture()

    with audio_file.open("rb") as f:
        submitted = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            webhook=True,
        )

    transcription_id = getattr(submitted, "transcription_id", None)
    assert transcription_id, "Async response did not include transcription_id"

    final = None
    for _ in range(20):
        try:
            final = client.speech_to_text.transcripts.get(transcription_id)
            if getattr(final, "text", None):
                break
        except Exception:
            pass
        time.sleep(1.0)

    assert final is not None
    assert getattr(final, "text", None)


@pytest.mark.e2e
def test_elevenlabs_sdk_single_use_token_contract():
    """Verify SDK single-use token helper works against Dalston endpoint."""
    api_key, base_url = _resolve_sdk_env()
    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)

    token = client.tokens.single_use.create("realtime_scribe")
    assert getattr(token, "token", None)


@pytest.mark.e2e
def test_elevenlabs_sdk_multichannel_convert():
    """Run multichannel convert and assert SDK parses transcripts[] shape."""
    api_key, base_url = _resolve_sdk_env()
    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)
    stereo_audio = _resolve_stereo_audio_fixture()

    with stereo_audio.open("rb") as f:
        result = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            use_multi_channel=True,
            timestamps_granularity="word",
        )

    transcripts = getattr(result, "transcripts", None)
    assert isinstance(transcripts, list)
    assert len(transcripts) >= 2
    channel_indexes = {
        getattr(chunk, "channel_index", None)
        for chunk in transcripts
        if getattr(chunk, "channel_index", None) is not None
    }
    assert channel_indexes >= {0, 1}
    assert all(getattr(chunk, "text", "").strip() for chunk in transcripts)
