"""Unit tests for AlignHTTPServer (phoneme-align engine HTTP interface).

Tests the ``/health``, ``/v1/capabilities``, and ``/v1/align`` endpoints
using FastAPI's TestClient with a mocked engine, so no real ML models
or running services are required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from dalston.engine_sdk.http_align import AlignHTTPServer
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TRANSCRIPT = {
    "text": "Hello world",
    "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
    "language": "en",
}

_ALIGN_RESULT = {
    "engine_id": "phoneme-align",
    "words": [
        {"word": "Hello", "start": 0.0, "end": 0.45},
        {"word": "world", "start": 0.55, "end": 1.0},
    ],
}


@pytest.fixture()
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.get_capabilities.return_value = EngineCapabilities(
        engine_id="phoneme-align",
        version="1.0.0",
        stages=["align"],
        supports_word_timestamps=True,
    )
    engine.health_check.return_value = {"status": "healthy"}
    engine.process.return_value = TaskResponse(data=_ALIGN_RESULT)
    return engine


@pytest.fixture()
def client(mock_engine: MagicMock) -> TestClient:
    server = AlignHTTPServer(engine=mock_engine, port=9104)
    return TestClient(server.app)


@pytest.fixture()
def wav_file(tmp_path: Path) -> Path:
    """Minimal valid WAV file in a temp directory."""
    path = tmp_path / "test.wav"
    # Minimal RIFF/WAV header (0-sample, mono, 16 kHz, 16-bit)
    path.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt "
        b"\x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00"
        b"\x00\x7d\x00\x00\x02\x00\x10\x00"
        b"data\x00\x00\x00\x00"
    )
    return path


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_returns_status_field(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# GET /v1/capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/v1/capabilities")
        assert resp.status_code == 200

    def test_returns_engine_id(self, client: TestClient) -> None:
        data = client.get("/v1/capabilities").json()
        assert data["engine_id"] == "phoneme-align"

    def test_stages_contains_align(self, client: TestClient) -> None:
        data = client.get("/v1/capabilities").json()
        assert "align" in data["stages"]

    def test_supports_word_timestamps(self, client: TestClient) -> None:
        data = client.get("/v1/capabilities").json()
        assert data["supports_word_timestamps"] is True


# ---------------------------------------------------------------------------
# POST /v1/align
# ---------------------------------------------------------------------------


class TestAlignEndpoint:
    def test_align_with_file_upload_returns_200(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT)},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200

    def test_align_returns_engine_id(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            data = client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT)},
                files={"file": ("audio.wav", f, "audio/wav")},
            ).json()
        assert data["engine_id"] == "phoneme-align"

    def test_align_returns_words(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            data = client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT)},
                files={"file": ("audio.wav", f, "audio/wav")},
            ).json()
        assert "words" in data
        assert len(data["words"]) == 2

    def test_align_forwards_transcript_to_engine(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        """The transcript JSON must be injected into previous_responses."""
        with wav_file.open("rb") as f:
            client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT)},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        call_args = mock_engine.process.call_args
        task_request: TaskRequest = call_args[0][0]
        assert task_request.previous_responses.get("transcribe") == _TRANSCRIPT

    def test_align_stage_is_set(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT)},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.stage == "align"

    def test_align_model_forwarded(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/align",
                data={"transcript": json.dumps(_TRANSCRIPT), "model": "wav2vec2-en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.config.get("loaded_model_id") == "wav2vec2-en"

    def test_missing_transcript_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/align",
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400
        assert "transcript" in resp.json()["detail"].lower()

    def test_invalid_transcript_json_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/align",
                data={"transcript": "not-valid-json{{{"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400

    def test_missing_audio_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/align",
            data={"transcript": json.dumps(_TRANSCRIPT)},
        )
        assert resp.status_code == 400

    def test_both_file_and_url_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/align",
                data={
                    "transcript": json.dumps(_TRANSCRIPT),
                    "audio_url": "https://example.com/audio.wav",
                },
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400
