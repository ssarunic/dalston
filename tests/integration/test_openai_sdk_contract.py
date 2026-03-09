"""Pinned OpenAI SDK contract tests for Dalston OpenAI compatibility (M61)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dalston.gateway.api.v1.openai_audio import OpenAIEndpoint, list_openai_models

openai = pytest.importorskip("openai", reason="openai package not installed")

FIXTURES_DIR = Path(__file__).parent / "openai_fixtures"


def test_contract_fixture_manifest_exists() -> None:
    manifest_path = FIXTURES_DIR / "manifest.json"
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["contract_date"] == "2026-03-08"
    assert manifest["sdk"]["package"] == "openai"
    assert manifest["sdk"]["version"] == "1.93.2"

    for fixture_name in manifest["fixtures"]:
        assert (FIXTURES_DIR / fixture_name).exists(), fixture_name


def test_contract_realtime_session_fixtures_have_rest_shapes() -> None:
    request_payload = json.loads(
        (
            FIXTURES_DIR / "realtime_transcription_session_create_request.json"
        ).read_text()
    )
    assert "input_audio_format" in request_payload
    assert "input_audio_transcription" in request_payload
    assert "session" not in request_payload

    response_payload = json.loads(
        (
            FIXTURES_DIR / "realtime_transcription_session_created_response.json"
        ).read_text()
    )
    assert "client_secret" in response_payload
    assert "input_audio_format" in response_payload


def test_capability_table_has_expected_phase0_models() -> None:
    transcription_models = list_openai_models(OpenAIEndpoint.TRANSCRIPTIONS)
    realtime_models = list_openai_models(OpenAIEndpoint.REALTIME)

    for model in [
        "whisper-1",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe-diarize",
        "gpt-4o-transcribe-latest",
    ]:
        assert model in transcription_models

    for model in ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]:
        assert model in realtime_models


@pytest.fixture()
def _sdk_contract_env() -> tuple[str, str, Path]:
    base_url = os.getenv("DALSTON_OPENAI_BASE_URL")
    api_key = os.getenv("DALSTON_API_KEY")

    audio_candidates = [
        Path(__file__).parent.parent / "audio" / "test_merged.wav",
        Path(__file__).parent.parent / "fixtures" / "test_audio.wav",
    ]
    audio_file = next((p for p in audio_candidates if p.exists()), None)

    if not base_url or not api_key or audio_file is None:
        pytest.skip(
            "Set DALSTON_OPENAI_BASE_URL, DALSTON_API_KEY and provide test audio fixture"
        )

    return base_url, api_key, audio_file


@pytest.fixture()
def _sdk_contract_realtime_env() -> tuple[str, str]:
    base_url = os.getenv("DALSTON_OPENAI_BASE_URL")
    api_key = os.getenv("DALSTON_API_KEY")
    if not base_url or not api_key:
        pytest.skip("Set DALSTON_OPENAI_BASE_URL and DALSTON_API_KEY")
    return base_url, api_key


def test_openai_sdk_batch_contract_live(
    _sdk_contract_env: tuple[str, str, Path],
) -> None:
    """Run pinned SDK against Dalston base_url when env is configured."""
    base_url, api_key, audio_file = _sdk_contract_env
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    with audio_file.open("rb") as f:
        result = client.audio.transcriptions.create(model="whisper-1", file=f)
    assert hasattr(result, "text")
    assert result.text

    with audio_file.open("rb") as f:
        verbose = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f,
            response_format="verbose_json",
        )
    assert verbose.task == "transcribe"

    with audio_file.open("rb") as f:
        raw = client.audio.transcriptions.with_raw_response.create(
            model="whisper-1",
            file=f,
        )
    assert raw.status_code == 200
    assert raw.headers.get("x-ratelimit-limit-requests") is not None


def test_openai_sdk_translation_contract_live(
    _sdk_contract_env: tuple[str, str, Path],
) -> None:
    base_url, api_key, audio_file = _sdk_contract_env
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    with audio_file.open("rb") as f:
        result = client.audio.translations.create(model="whisper-1", file=f)
    assert hasattr(result, "text")
    assert result.text


def test_openai_sdk_realtime_session_contract_live(
    _sdk_contract_realtime_env: tuple[str, str],
) -> None:
    base_url, api_key = _sdk_contract_realtime_env
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    realtime = getattr(getattr(client, "beta", None), "realtime", None)
    if realtime is None or not hasattr(realtime, "transcription_sessions"):
        pytest.skip(
            "Installed openai SDK does not expose realtime transcription sessions"
        )

    session = realtime.transcription_sessions.create(
        input_audio_format="pcm16",
        input_audio_transcription={
            "model": "gpt-4o-transcribe",
            "language": "en",
        },
        turn_detection={
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 500,
            "prefix_padding_ms": 300,
        },
    )

    assert getattr(session, "client_secret", None) is not None
    assert getattr(session.client_secret, "value", None)
    assert session.input_audio_format == "pcm16"
