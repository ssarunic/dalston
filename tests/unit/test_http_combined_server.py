"""Unit tests for CombinedHTTPServer (whisper-pyannote composite engine HTTP interface).

Tests the HTTP contract for the combined engine which exposes:
  - GET  /health
  - GET  /v1/capabilities
  - GET  /metrics
  - POST /v1/transcribe
  - POST /v1/diarize
  - POST /v1/transcribe_and_diarize   (only when both stages are present)

All tests use FastAPI's TestClient with a mocked CompositeEngine so no
real ML models or child services need to be running.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from dalston.engine_sdk.http_combined import CombinedHTTPServer
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_TRANSCRIBE_RESULT = {
    "engine_id": "whisper-pyannote",
    "text": "Hello world",
    "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
    "language": "en",
}

_DIARIZE_RESULT = {
    "engine_id": "whisper-pyannote",
    "segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0}],
}

_COMBINED_RESULT = {
    "engine_id": "whisper-pyannote",
    "stages_completed": ["transcribe", "diarize"],
    "transcribe": _TRANSCRIBE_RESULT,
    "diarize": _DIARIZE_RESULT,
}


def _make_engine(stages: list[str] | None = None) -> MagicMock:
    if stages is None:
        stages = ["transcribe", "diarize"]
    engine = MagicMock()
    engine.get_capabilities.return_value = EngineCapabilities(
        engine_id="whisper-pyannote",
        version="1.0.0",
        stages=stages,
        supports_word_timestamps=True,
        includes_diarization="diarize" in stages,
    )
    engine.health_check.return_value = {
        "status": "healthy",
        "engine_id": "whisper-pyannote",
        "children": {
            "faster-whisper": {"status": "healthy"},
            "pyannote-4.0": {"status": "healthy"},
        },
    }
    engine.process.return_value = TaskResponse(data=_COMBINED_RESULT)
    return engine


@pytest.fixture()
def engine() -> MagicMock:
    return _make_engine()


@pytest.fixture()
def client(engine: MagicMock) -> TestClient:
    server = CombinedHTTPServer(engine=engine, port=9103)
    return TestClient(server.app)


@pytest.fixture()
def wav_file(tmp_path: Path) -> Path:
    path = tmp_path / "test.wav"
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
        assert client.get("/health").status_code == 200

    def test_returns_healthy_status(self, client: TestClient) -> None:
        assert client.get("/health").json()["status"] == "healthy"

    def test_returns_children(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "children" in data
        assert "faster-whisper" in data["children"]
        assert "pyannote-4.0" in data["children"]


# ---------------------------------------------------------------------------
# GET /v1/capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/v1/capabilities").status_code == 200

    def test_engine_id(self, client: TestClient) -> None:
        assert client.get("/v1/capabilities").json()["engine_id"] == "whisper-pyannote"

    def test_stages_include_transcribe_and_diarize(self, client: TestClient) -> None:
        stages = client.get("/v1/capabilities").json()["stages"]
        assert "transcribe" in stages
        assert "diarize" in stages

    def test_includes_diarization_flag(self, client: TestClient) -> None:
        data = client.get("/v1/capabilities").json()
        assert data["includes_diarization"] is True


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/metrics").status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/transcribe
# ---------------------------------------------------------------------------


class TestTranscribeEndpoint:
    def test_returns_200(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/transcribe",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200

    def test_stage_set_to_transcribe(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("_stage") == "transcribe"

    def test_language_forwarded(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe",
                data={"language": "fr"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("language") == "fr"

    def test_model_forwarded_as_loaded_model_id(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe",
                data={"model": "faster-whisper-large"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("loaded_model_id") == "faster-whisper-large"

    def test_missing_audio_returns_400(self, client: TestClient) -> None:
        assert client.post("/v1/transcribe").status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/diarize
# ---------------------------------------------------------------------------


class TestDiarizeEndpoint:
    def test_returns_200(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/diarize",
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200

    def test_stage_set_to_diarize(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/diarize",
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("_stage") == "diarize"

    def test_num_speakers_forwarded(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/diarize",
                data={"num_speakers": "2"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("num_speakers") == 2

    def test_missing_audio_returns_400(self, client: TestClient) -> None:
        assert client.post("/v1/diarize").status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/transcribe_and_diarize
# ---------------------------------------------------------------------------


class TestTranscribeAndDiarizeEndpoint:
    def test_returns_200(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/transcribe_and_diarize",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200

    def test_returns_stages_completed(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            data = client.post(
                "/v1/transcribe_and_diarize",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            ).json()
        assert "stages_completed" in data
        assert set(data["stages_completed"]) == {"transcribe", "diarize"}

    def test_stage_set_to_combined(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe_and_diarize",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("_stage") == "combined"

    def test_language_forwarded(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe_and_diarize",
                data={"language": "de"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("language") == "de"

    def test_model_diarize_forwarded_as_diarize_model_id(
        self, client: TestClient, engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/transcribe_and_diarize",
                data={"model_diarize": "pyannote/speaker-diarization-3.1"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        req: TaskRequest = engine.process.call_args[0][0]
        assert req.config.get("diarize_model_id") == "pyannote/speaker-diarization-3.1"

    def test_missing_audio_returns_400(self, client: TestClient) -> None:
        assert client.post("/v1/transcribe_and_diarize").status_code == 400

    def test_both_file_and_url_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/transcribe_and_diarize",
                data={"audio_url": "https://example.com/audio.wav"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# combined endpoint absent when only one stage
# ---------------------------------------------------------------------------


class TestCombinedEndpointNotRegisteredForSingleStage:
    """If the engine covers only one stage, /v1/transcribe_and_diarize is not registered."""

    def test_transcribe_only_has_no_combined_endpoint(self, wav_file: Path) -> None:
        engine = _make_engine(stages=["transcribe"])
        client = TestClient(CombinedHTTPServer(engine=engine, port=9103).app)
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/transcribe_and_diarize",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 404

    def test_transcribe_only_still_has_transcribe_endpoint(
        self, wav_file: Path
    ) -> None:
        engine = _make_engine(stages=["transcribe"])
        client = TestClient(CombinedHTTPServer(engine=engine, port=9103).app)
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/transcribe",
                data={"language": "en"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# OpenAI / ElevenLabs compat routes
# ---------------------------------------------------------------------------


class TestCompatEndpoints:
    def test_openai_transcriptions_registered_when_transcribe_stage(
        self, wav_file: Path
    ) -> None:
        engine = _make_engine(stages=["transcribe"])
        engine.process.return_value = TaskResponse(data=_TRANSCRIBE_RESULT)
        client = TestClient(CombinedHTTPServer(engine=engine, port=9103).app)
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.json() == {"text": "Hello world"}

    def test_elevenlabs_speech_to_text_registered_when_transcribe_stage(
        self, wav_file: Path
    ) -> None:
        engine = _make_engine(stages=["transcribe"])
        engine.process.return_value = TaskResponse(data=_TRANSCRIBE_RESULT)
        client = TestClient(CombinedHTTPServer(engine=engine, port=9103).app)
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "Hello world"
        assert body["language_code"] == "en"

    def test_compat_not_registered_for_diarize_only_engine(
        self, wav_file: Path
    ) -> None:
        engine = _make_engine(stages=["diarize"])
        client = TestClient(CombinedHTTPServer(engine=engine, port=9103).app)
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 404
