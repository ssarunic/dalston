"""Unit tests for the naked engine's OpenAI / ElevenLabs compat endpoints.

Exercises ``POST /v1/audio/transcriptions`` and ``POST /v1/speech-to-text``
on the ``TranscribeHTTPServer`` FastAPI app with a mocked engine, so no
real models or running services are required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from dalston.engine_sdk.http_transcribe import TranscribeHTTPServer
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

_TRANSCRIPT = {
    "schema_version": "1",
    "text": "Hello world",
    "language": "en",
    "language_confidence": 0.97,
    "duration": 1.25,
    "engine_id": "nemo",
    "timestamp_granularity": "word",
    "alignment_method": "forced",
    "segments": [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "Hello world",
            "words": [
                {
                    "text": "Hello",
                    "start": 0.0,
                    "end": 0.45,
                    "confidence": 0.9,
                    "metadata": {"logprob": -0.1},
                },
                {
                    "text": "world",
                    "start": 0.55,
                    "end": 1.0,
                    "confidence": 0.92,
                    "metadata": {"logprob": -0.05},
                },
            ],
            "metadata": {
                "tokens": [1, 2, 3],
                "temperature": 0.2,
                "avg_logprob": -0.3,
                "compression_ratio": 1.1,
                "no_speech_prob": 0.01,
            },
        }
    ],
}


@pytest.fixture()
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.get_capabilities.return_value = EngineCapabilities(
        engine_id="nemo",
        version="1.0.0",
        stages=["transcribe"],
        supports_word_timestamps=True,
    )
    engine.health_check.return_value = {"status": "healthy"}
    engine.process.return_value = TaskResponse(data=_TRANSCRIPT)
    return engine


@pytest.fixture()
def client(mock_engine: MagicMock) -> TestClient:
    server = TranscribeHTTPServer(engine=mock_engine, port=9199)
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
# OpenAI: POST /v1/audio/transcriptions
# ---------------------------------------------------------------------------


class TestOpenAITranscriptions:
    def test_default_json_returns_text_only(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.json() == {"text": "Hello world"}

    def test_text_format_returns_plain_text(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1", "response_format": "text"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "Hello world"

    def test_verbose_json_includes_segments_and_language(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1", "response_format": "verbose_json"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task"] == "transcribe"
        assert body["language"] == "en"
        assert body["duration"] == pytest.approx(1.25)
        assert body["text"] == "Hello world"
        assert len(body["segments"]) == 1
        seg = body["segments"][0]
        assert seg["id"] == 0
        assert seg["start"] == 0.0
        assert seg["end"] == 1.0
        assert seg["tokens"] == [1, 2, 3]
        assert seg["avg_logprob"] == pytest.approx(-0.3)
        assert "words" not in body  # only when timestamp_granularities=["word"]

    def test_verbose_json_with_word_granularity_emits_words(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data=[
                    ("model", "whisper-1"),
                    ("response_format", "verbose_json"),
                    ("timestamp_granularities", "word"),
                ],
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["words"] == [
            {"word": "Hello", "start": 0.0, "end": 0.45},
            {"word": "world", "start": 0.55, "end": 1.0},
        ]

    def test_invalid_response_format_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1", "response_format": "srt"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["param"] == "response_format"

    def test_word_granularity_without_verbose_json_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/audio/transcriptions",
                data=[
                    ("model", "whisper-1"),
                    ("timestamp_granularities", "word"),
                ],
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400

    def test_language_forwarded_to_engine(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1", "language": "de"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.config["language"] == "de"
        assert task_request.stage == "transcribe"

    def test_missing_file_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/audio/transcriptions",
            data={"model": "whisper-1"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ElevenLabs: POST /v1/speech-to-text
# ---------------------------------------------------------------------------


class TestElevenLabsSpeechToText:
    def test_returns_elevenlabs_transcript_shape(
        self, client: TestClient, wav_file: Path
    ) -> None:
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
        assert body["language_probability"] == pytest.approx(0.97)
        assert "transcription_id" in body
        assert isinstance(body["words"], list)
        assert len(body["words"]) == 2
        first = body["words"][0]
        assert first["text"] == "Hello"
        assert first["start"] == 0.0
        assert first["type"] == "word"
        assert first["speaker_id"] is None
        assert first["logprob"] == pytest.approx(-0.1)

    def test_keyterms_forwarded_as_vocabulary(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/speech-to-text",
                data={
                    "model_id": "scribe_v1",
                    "keyterms": json.dumps(["PostgreSQL", "Kubernetes"]),
                },
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 200
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.config["vocabulary"] == ["PostgreSQL", "Kubernetes"]

    def test_language_code_forwarded(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1", "language_code": "fr"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.config["language"] == "fr"

    def test_diarize_rejected(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1", "diarize": "true"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400
        assert "diarize" in resp.json()["detail"].lower()

    def test_webhook_rejected(self, client: TestClient, wav_file: Path) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1", "webhook": "true"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400

    def test_invalid_keyterms_json_returns_400(
        self, client: TestClient, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            resp = client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1", "keyterms": "not-json{{{"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        assert resp.status_code == 400

    def test_timestamps_granularity_none_disables_word_timestamps(
        self, client: TestClient, mock_engine: MagicMock, wav_file: Path
    ) -> None:
        with wav_file.open("rb") as f:
            client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v1", "timestamps_granularity": "none"},
                files={"file": ("audio.wav", f, "audio/wav")},
            )
        task_request: TaskRequest = mock_engine.process.call_args[0][0]
        assert task_request.config["word_timestamps"] is False
