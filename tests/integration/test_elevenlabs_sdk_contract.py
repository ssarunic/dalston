"""Pinned ElevenLabs SDK contract tests for Dalston ElevenLabs compatibility (M62)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dalston.gateway.api.v1.elevenlabs_stt import (
    ELEVENLABS_STT_CAPABILITIES,
    ElevenLabsEndpoint,
    list_elevenlabs_models,
)

FIXTURES_DIR = Path(__file__).parent / "elevenlabs_fixtures"


def test_contract_fixture_manifest_exists() -> None:
    manifest_path = FIXTURES_DIR / "manifest.json"
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["contract_date"] == "2026-03-08"
    assert manifest["sdk"]["package"] == "elevenlabs"
    assert manifest["sdk"]["version"] == "2.38.1"

    for fixture_name in manifest["fixtures"]:
        assert (FIXTURES_DIR / fixture_name).exists(), fixture_name


def test_capability_table_has_phase0_models() -> None:
    batch_models = list_elevenlabs_models(ElevenLabsEndpoint.BATCH)
    realtime_models = list_elevenlabs_models(ElevenLabsEndpoint.REALTIME)

    for model in ["scribe_v1", "scribe_v1_experimental", "scribe_v2"]:
        assert model in batch_models

    for model in ["scribe_v1", "scribe_v2"]:
        assert model in realtime_models


def test_capability_table_freezes_field_locations() -> None:
    batch = ELEVENLABS_STT_CAPABILITIES[ElevenLabsEndpoint.BATCH]
    realtime = ELEVENLABS_STT_CAPABILITIES[ElevenLabsEndpoint.REALTIME]

    assert "enable_logging" in batch.fields["query"]
    assert "enable_logging" not in batch.fields["multipart"]
    assert "model_id" in batch.fields["multipart"]
    assert "use_multi_channel" in batch.fields["multipart"]
    assert "token" in realtime.fields["ws_query"]
    assert "include_language_detection" in realtime.fields["ws_query"]
    assert "audio_base_64" in realtime.fields["message_body"]
    assert "ulaw_8000" in realtime.audio_formats


def test_frozen_response_schemas() -> None:
    async_payload = json.loads(
        (FIXTURES_DIR / "speech_to_text_convert_async_response.json").read_text()
    )
    assert set(async_payload.keys()) == {"message", "request_id", "transcription_id"}

    transcript_payload = json.loads(
        (FIXTURES_DIR / "speech_to_text_get_completed_response.json").read_text()
    )
    assert "words" in transcript_payload
    assert "transcription_id" in transcript_payload
    assert all("type" in w for w in transcript_payload["words"])

    session_started = json.loads(
        (FIXTURES_DIR / "realtime_session_started_message.json").read_text()
    )
    assert session_started["message_type"] == "session_started"
    assert session_started["config"]["commit_strategy"] == "manual"

    multichannel = json.loads(
        (FIXTURES_DIR / "speech_to_text_multichannel_response.json").read_text()
    )
    assert "transcripts" in multichannel
    assert len(multichannel["transcripts"]) >= 1
    assert all("channel_index" in chunk for chunk in multichannel["transcripts"])


@pytest.fixture()
def _sdk_contract_env() -> tuple[str, str, Path]:
    pytest.importorskip("elevenlabs", reason="elevenlabs package not installed")

    base_url = os.getenv("DALSTON_ELEVENLABS_BASE_URL")
    api_key = os.getenv("DALSTON_API_KEY")
    audio_candidates = [
        Path(__file__).parent.parent / "audio" / "test_merged.wav",
        Path(__file__).parent.parent / "fixtures" / "test_audio.wav",
    ]
    audio_file = next((p for p in audio_candidates if p.exists()), None)

    if not base_url or not api_key or audio_file is None:
        pytest.skip(
            "Set DALSTON_ELEVENLABS_BASE_URL, DALSTON_API_KEY and provide audio fixture"
        )

    return base_url, api_key, audio_file


def test_elevenlabs_sdk_batch_contract_live(
    _sdk_contract_env: tuple[str, str, Path],
) -> None:
    """Run pinned SDK against Dalston base_url when env is configured."""
    elevenlabs = pytest.importorskip(
        "elevenlabs", reason="elevenlabs package not installed"
    )
    base_url, api_key, audio_file = _sdk_contract_env
    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)

    with audio_file.open("rb") as f:
        result = client.speech_to_text.convert(file=f, model_id="scribe_v1")

    text = getattr(result, "text", None)
    if text is None and isinstance(result, dict):
        text = result.get("text")
    assert isinstance(text, str)
    assert text.strip()


def test_elevenlabs_sdk_batch_multichannel_contract_live(
    _sdk_contract_env: tuple[str, str, Path],
) -> None:
    """Validate pinned SDK can parse Dalston multichannel response model."""
    elevenlabs = pytest.importorskip(
        "elevenlabs", reason="elevenlabs package not installed"
    )
    base_url, api_key, _ = _sdk_contract_env
    stereo_audio = Path(__file__).parent.parent / "audio" / "test_stereo_speakers.wav"
    if not stereo_audio.exists():
        pytest.skip("Stereo fixture tests/audio/test_stereo_speakers.wav is missing")

    client = elevenlabs.ElevenLabs(api_key=api_key, base_url=base_url)
    with stereo_audio.open("rb") as f:
        result = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            use_multi_channel=True,
            timestamps_granularity="word",
        )

    transcripts = getattr(result, "transcripts", None)
    assert isinstance(transcripts, list)
    assert len(transcripts) >= 1
    assert all(
        getattr(chunk, "channel_index", None) is not None for chunk in transcripts
    )
