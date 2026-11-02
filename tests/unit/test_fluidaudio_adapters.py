"""M104: FluidAudio adapters against a mocked fluidaudio-server.

The adapters do no inference, so these tests stand up an ``httpx``
MockTransport that plays the Swift server's HTTP contract and check the
Dalston side: request shape, Transcript / DiarizationResponse conversion,
health and capabilities, and error propagation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dalston.common.pipeline_types import DiarizationResponse
from dalston.engine_sdk import TaskRequest
from dalston.engine_sdk.sidecar_http import SidecarError, SidecarHTTPClient

_REPO = Path(__file__).parent.parent.parent
_TRANSCRIBE_ENGINE = _REPO / "engines" / "stt-transcribe" / "fluidaudio" / "engine.py"
_DIARIZE_ENGINE = _REPO / "engines" / "stt-diarize" / "fluidaudio" / "engine.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transcribe_mod = _load(_TRANSCRIBE_ENGINE, "fluidaudio_transcribe_engine")
diarize_mod = _load(_DIARIZE_ENGINE, "fluidaudio_diarize_engine")


class FakeServer:
    """Records requests and answers like fluidaudio-server."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.transcribe_response: dict[str, Any] = {
            "model": transcribe_mod.PARAKEET_V3_REPO,
            "duration": 3.5,
            "language": None,
            "text": "Hello world. How are you?",
            "words": [
                {"text": "Hello", "start": 0.1, "end": 0.4, "confidence": None},
                {"text": "world.", "start": 0.5, "end": 0.9, "confidence": None},
                {"text": "How", "start": 1.2, "end": 1.4, "confidence": None},
                {"text": "are", "start": 1.5, "end": 1.6, "confidence": None},
                {"text": "you?", "start": 1.7, "end": 2.1, "confidence": None},
            ],
            "timings": {"load_model_s": 0.0, "transcribe_s": 0.2},
        }
        self.diarize_response: dict[str, Any] = {
            "model": diarize_mod.SPEAKER_DIARIZATION_REPO,
            "duration": 10.0,
            "segments": [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0},
                {"speaker": "SPEAKER_01", "start": 3.0, "end": 7.0},
                {"speaker": "SPEAKER_00", "start": 7.5, "end": 10.0},
            ],
            "warnings": ["min_speakers ignored"],
            "timings": {"load_model_s": 0.0, "diarize_s": 1.0},
        }
        self.status_code = 200
        self.health_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            if not self.health_ok:
                return httpx.Response(503, json={"status": "starting"})
            return httpx.Response(
                200, json={"status": "ok", "asr_loaded": None, "queue_depth": 0}
            )
        if path == "/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "asr_models": [transcribe_mod.PARAKEET_ULTRA_REPO],
                    "diarizer_models": [diarize_mod.SPEAKER_DIARIZATION_REPO],
                },
            )
        if self.status_code != 200:
            return httpx.Response(self.status_code, text="queue full")
        if path == "/v1/transcribe":
            return httpx.Response(200, json=self.transcribe_response)
        if path == "/v1/diarize":
            return httpx.Response(200, json=self.diarize_response)
        return httpx.Response(404)

    def client(self) -> SidecarHTTPClient:
        return SidecarHTTPClient(
            "http://fluidaudio.test", transport=httpx.MockTransport(self.handler)
        )

    def last_form(self) -> dict[str, str]:
        """Decode the multipart form fields of the last request."""
        req = self.requests[-1]
        body = req.content.decode("latin-1")
        boundary = req.headers["content-type"].split("boundary=")[1]
        fields: dict[str, str] = {}
        for part in body.split("--" + boundary):
            if 'name="' not in part or "filename=" in part:
                continue
            header, _, value = part.partition("\r\n\r\n")
            name = header.split('name="')[1].split('"')[0]
            fields[name] = value.rstrip("\r\n-")
        return fields


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 60)
    return p


# -- Transcribe adapter --------------------------------------------------------


class TestFluidAudioBatchEngine:
    def test_builds_transcript_with_sentence_segments(
        self, server: FakeServer, audio: Path
    ) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(
            task_id="t1",
            job_id="j1",
            stage="transcribe",
            config={"language": "en", "word_timestamps": True},
            audio_path=audio,
        )

        transcript = engine.transcribe_audio(req, MagicMock())

        assert transcript.text == "Hello world. How are you?"
        assert transcript.language == "en"
        assert transcript.engine_id == "fluidaudio"
        assert transcript.duration == pytest.approx(3.5)
        assert [s.text for s in transcript.segments] == ["Hello world.", "How are you?"]
        first = transcript.segments[0]
        assert first.start == pytest.approx(0.1)
        assert first.end == pytest.approx(0.9)
        assert first.words is not None and [w.text for w in first.words] == [
            "Hello",
            "world.",
        ]

    def test_forwards_model_and_language_as_form_fields(
        self, server: FakeServer, audio: Path
    ) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(
            task_id="t1",
            job_id="j1",
            stage="transcribe",
            config={
                "language": "de",
                "loaded_model_id": transcribe_mod.PARAKEET_ULTRA_REPO,
            },
            audio_path=audio,
        )
        engine.transcribe_audio(req, MagicMock())

        request = server.requests[-1]
        assert request.url.path == "/v1/transcribe"
        assert request.headers["content-type"].startswith("multipart/form-data")
        form = server.last_form()
        assert form["model"] == transcribe_mod.PARAKEET_ULTRA_REPO
        assert form["language"] == "de"
        assert b'filename="clip.wav"' in request.content

    def test_default_model_used_when_task_pins_none(
        self, server: FakeServer, audio: Path
    ) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="transcribe", audio_path=audio)
        engine.transcribe_audio(req, MagicMock())
        assert server.last_form()["model"] == transcribe_mod.PARAKEET_V3_REPO
        # language omitted entirely when not requested
        assert "language" not in server.last_form()

    def test_detected_language_wins_over_requested(
        self, server: FakeServer, audio: Path
    ) -> None:
        server.transcribe_response["language"] = "hr"
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(
            task_id="t", job_id="j", stage="transcribe",
            config={"language": "en"}, audio_path=audio,
        )  # fmt: skip
        transcript = engine.transcribe_audio(req, MagicMock())
        assert transcript.language == "hr"

    def test_no_words_yields_single_segment(
        self, server: FakeServer, audio: Path
    ) -> None:
        server.transcribe_response["words"] = []
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="transcribe", audio_path=audio)
        transcript = engine.transcribe_audio(req, MagicMock())
        assert len(transcript.segments) == 1
        assert transcript.segments[0].text == "Hello world. How are you?"

    def test_server_error_raises_sidecar_error(
        self, server: FakeServer, audio: Path
    ) -> None:
        server.status_code = 503
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="transcribe", audio_path=audio)
        with pytest.raises(SidecarError) as excinfo:
            engine.transcribe_audio(req, MagicMock())
        assert excinfo.value.status_code == 503
        assert "queue full" in str(excinfo.value)

    def test_missing_audio_path_rejected(self, server: FakeServer) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="transcribe")
        with pytest.raises(ValueError, match="audio_path"):
            engine.transcribe_audio(req, MagicMock())

    def test_health_mirrors_server(self, server: FakeServer) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        assert engine.health_check()["status"] == "healthy"
        server.health_ok = False
        assert engine.health_check()["status"] == "unhealthy"

    def test_health_unreachable_does_not_raise(self) -> None:
        def down(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = SidecarHTTPClient(
            "http://down.test", transport=httpx.MockTransport(down)
        )
        engine = transcribe_mod.FluidAudioBatchEngine(client=client)
        health = engine.health_check()
        assert health["status"] == "unhealthy"
        assert health["server"]["status"] == "unreachable"

    def test_capabilities_take_model_variants_from_server(
        self, server: FakeServer
    ) -> None:
        engine = transcribe_mod.FluidAudioBatchEngine(client=server.client())
        caps = engine.get_capabilities()
        assert caps.engine_id == "fluidaudio"
        assert caps.stages == ["transcribe"]
        assert caps.supports_word_timestamps is True
        assert caps.model_variants == [transcribe_mod.PARAKEET_ULTRA_REPO]
        assert caps.gpu_required is False
        assert caps.max_concurrency == 1

    def test_capabilities_fall_back_when_server_down(self) -> None:
        def down(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = SidecarHTTPClient(
            "http://down.test", transport=httpx.MockTransport(down)
        )
        engine = transcribe_mod.FluidAudioBatchEngine(client=client)
        assert engine.get_capabilities().model_variants == list(
            transcribe_mod.ASR_MODELS
        )


# -- Diarize adapter -----------------------------------------------------------


class TestFluidAudioDiarizeEngine:
    def test_builds_diarization_response(self, server: FakeServer, audio: Path) -> None:
        engine = diarize_mod.FluidAudioDiarizeEngine(client=server.client())
        req = TaskRequest(
            task_id="t", job_id="j", stage="diarize",
            config={"num_speakers": 2}, audio_path=audio,
        )  # fmt: skip

        response = engine.process(req, MagicMock())

        data = response.data
        assert isinstance(data, DiarizationResponse)
        assert data.engine_id == "fluidaudio-pyannote"
        assert data.speakers == ["SPEAKER_00", "SPEAKER_01"]
        assert data.num_speakers == 2
        assert len(data.turns) == 3
        assert data.turns[1].speaker == "SPEAKER_01"
        # 3.0-4.0 overlaps: 1 s out of 9.5 s of speech (union of turns)
        assert data.overlap_duration == pytest.approx(1.0)
        assert data.overlap_ratio == pytest.approx(1.0 / 9.5)
        assert "min_speakers ignored" in data.warnings
        assert server.last_form()["num_speakers"] == "2"
        assert server.last_form()["model"] == diarize_mod.SPEAKER_DIARIZATION_REPO

    def test_ignored_params_are_reported(self, server: FakeServer, audio: Path) -> None:
        engine = diarize_mod.FluidAudioDiarizeEngine(client=server.client())
        req = TaskRequest(
            task_id="t", job_id="j", stage="diarize",
            config={"exclusive": True, "max_chunk_s": 600, "min_speakers": 1, "max_speakers": 3},
            audio_path=audio,
        )  # fmt: skip
        response = engine.process(req, MagicMock())
        assert isinstance(response.data, DiarizationResponse)
        joined = " ".join(response.data.warnings)
        assert "exclusive" in joined and "max_chunk_s" in joined
        form = server.last_form()
        assert form["min_speakers"] == "1" and form["max_speakers"] == "3"
        assert "num_speakers" not in form

    def test_empty_segments(self, server: FakeServer, audio: Path) -> None:
        server.diarize_response["segments"] = []
        engine = diarize_mod.FluidAudioDiarizeEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="diarize", audio_path=audio)
        response = engine.process(req, MagicMock())
        assert isinstance(response.data, DiarizationResponse)
        assert response.data.num_speakers == 0
        assert response.data.turns == []

    def test_server_error_raises_sidecar_error(
        self, server: FakeServer, audio: Path
    ) -> None:
        server.status_code = 500
        engine = diarize_mod.FluidAudioDiarizeEngine(client=server.client())
        req = TaskRequest(task_id="t", job_id="j", stage="diarize", audio_path=audio)
        with pytest.raises(SidecarError):
            engine.process(req, MagicMock())

    def test_capabilities(self, server: FakeServer) -> None:
        engine = diarize_mod.FluidAudioDiarizeEngine(client=server.client())
        caps = engine.get_capabilities()
        assert caps.stages == ["diarize"]
        assert caps.model_variants == [diarize_mod.SPEAKER_DIARIZATION_REPO]
        assert caps.supports_word_timestamps is False


# -- Sidecar client ------------------------------------------------------------


class TestSidecarHTTPClient:
    def test_non_json_body_is_an_error(self) -> None:
        client = SidecarHTTPClient(
            "http://x.test",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="nope")),
        )
        with pytest.raises(SidecarError, match="non-JSON"):
            client.get_json("/v1/capabilities")

    def test_non_object_json_is_an_error(self) -> None:
        client = SidecarHTTPClient(
            "http://x.test",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1, 2])),
        )
        with pytest.raises(SidecarError, match="expected object"):
            client.get_json("/v1/capabilities")

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DALSTON_FLUIDAUDIO_URL", "http://10.0.0.5:9300/")
        monkeypatch.setenv("DALSTON_FLUIDAUDIO_TIMEOUT_S", "42")
        client = SidecarHTTPClient.from_env(
            "DALSTON_FLUIDAUDIO_URL",
            default_url="http://127.0.0.1:9200",
            timeout_var="DALSTON_FLUIDAUDIO_TIMEOUT_S",
        )
        assert client.base_url == "http://10.0.0.5:9300"
        assert client.timeout_s == 42.0

    def test_none_fields_are_omitted(self, tmp_path: Path) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json={})

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"\x00" * 8)
        client = SidecarHTTPClient(
            "http://x.test", transport=httpx.MockTransport(handler)
        )
        client.post_audio(
            "/v1/transcribe", audio, fields={"model": "m", "language": None}
        )
        body = seen[0]
        assert b'name="model"' in body
        assert b'name="language"' not in body
