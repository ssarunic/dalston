"""Unit tests for M72: Inference Server Sidecar Pattern.

Tests cover:
1. Proto round-trip: TranscriptionResult → proto → TranscriptionResult lossless
2. RemoteTranscribeCore with mocked stub
3. InferenceServer with mocked core (semaphore, error mapping, health check)
4. Engine adapters selecting remote core via DALSTON_INFERENCE_URI
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.proto import inference_pb2, inference_pb2_grpc


# ---------------------------------------------------------------------------
# T1: Proto round-trip tests
# ---------------------------------------------------------------------------


class TestProtoRoundTrip:
    """Verify proto serialization is lossless for transcription results."""

    def test_segment_round_trip(self) -> None:
        """Segment → proto → Segment preserves all fields."""
        original = inference_pb2.Segment(
            start=1.23,
            end=4.56,
            text="hello world",
            confidence=0.95,
            avg_logprob=-0.25,
            compression_ratio=1.5,
            no_speech_prob=0.01,
            words=[
                inference_pb2.Word(word="hello", start=1.23, end=2.0, probability=0.98),
                inference_pb2.Word(word="world", start=2.1, end=4.56, probability=0.96),
            ],
        )

        # Serialize to bytes and back
        data = original.SerializeToString()
        restored = inference_pb2.Segment()
        restored.ParseFromString(data)

        assert restored.start == pytest.approx(original.start, rel=1e-5)
        assert restored.end == pytest.approx(original.end, rel=1e-5)
        assert restored.text == original.text
        assert restored.confidence == pytest.approx(original.confidence, rel=1e-5)
        assert restored.avg_logprob == pytest.approx(original.avg_logprob, rel=1e-5)
        assert len(restored.words) == 2
        assert restored.words[0].word == "hello"
        assert restored.words[1].probability == pytest.approx(0.96, rel=1e-5)

    def test_transcribe_response_round_trip(self) -> None:
        """Full TranscribeResponse survives serialization."""
        response = inference_pb2.TranscribeResponse(
            segments=[
                inference_pb2.Segment(
                    start=0.0,
                    end=3.5,
                    text="test segment",
                    words=[
                        inference_pb2.Word(
                            word="test", start=0.0, end=1.5, probability=0.99
                        ),
                        inference_pb2.Word(
                            word="segment", start=1.6, end=3.5, probability=0.97
                        ),
                    ],
                ),
            ],
            language="en",
            language_probability=0.98,
            duration=3.5,
        )

        data = response.SerializeToString()
        restored = inference_pb2.TranscribeResponse()
        restored.ParseFromString(data)

        assert len(restored.segments) == 1
        assert restored.segments[0].text == "test segment"
        assert restored.language == "en"
        assert restored.language_probability == pytest.approx(0.98, rel=1e-5)
        assert restored.duration == pytest.approx(3.5, rel=1e-5)

    def test_transcribe_request_round_trip(self) -> None:
        """TranscribeRequest including config survives serialization."""
        audio_data = np.zeros(16000, dtype=np.float32).tobytes()

        request = inference_pb2.TranscribeRequest(
            audio=audio_data,
            format=inference_pb2.PCM_F32LE_16K,
            model_id="large-v3-turbo",
            config=inference_pb2.TranscribeConfig(
                language="en",
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                temperature=0.0,
                task="transcribe",
                initial_prompt="test prompt",
                hotwords="keyword1 keyword2",
            ),
        )

        data = request.SerializeToString()
        restored = inference_pb2.TranscribeRequest()
        restored.ParseFromString(data)

        assert len(restored.audio) == len(audio_data)
        assert restored.format == inference_pb2.PCM_F32LE_16K
        assert restored.model_id == "large-v3-turbo"
        assert restored.config.language == "en"
        assert restored.config.beam_size == 5
        assert restored.config.initial_prompt == "test prompt"
        assert restored.config.hotwords == "keyword1 keyword2"

    def test_status_response_round_trip(self) -> None:
        """StatusResponse survives serialization."""
        response = inference_pb2.StatusResponse(
            runtime="faster-whisper",
            device="cuda",
            loaded_models=["large-v3-turbo", "base"],
            total_capacity=4,
            available_capacity=2,
            healthy=True,
        )

        data = response.SerializeToString()
        restored = inference_pb2.StatusResponse()
        restored.ParseFromString(data)

        assert restored.runtime == "faster-whisper"
        assert restored.device == "cuda"
        assert list(restored.loaded_models) == ["large-v3-turbo", "base"]
        assert restored.total_capacity == 4
        assert restored.available_capacity == 2
        assert restored.healthy is True

    def test_audio_format_enum_values(self) -> None:
        """Audio format enum has expected values."""
        assert inference_pb2.PCM_S16LE_16K == 0
        assert inference_pb2.PCM_F32LE_16K == 1
        assert inference_pb2.FILE == 2

    def test_optional_fields_absent(self) -> None:
        """Optional fields default correctly when absent."""
        seg = inference_pb2.Segment(start=0.0, end=1.0, text="hi")
        assert not seg.HasField("avg_logprob")
        assert not seg.HasField("compression_ratio")
        assert not seg.HasField("no_speech_prob")


# ---------------------------------------------------------------------------
# T5: RemoteTranscribeCore tests with mocked stub
# ---------------------------------------------------------------------------


class TestRemoteTranscribeCore:
    """RemoteTranscribeCore with mocked gRPC stub."""

    def _make_mock_response(self) -> inference_pb2.TranscribeResponse:
        return inference_pb2.TranscribeResponse(
            segments=[
                inference_pb2.Segment(
                    start=0.0,
                    end=2.5,
                    text="hello world",
                    words=[
                        inference_pb2.Word(
                            word="hello", start=0.0, end=1.2, probability=0.98
                        ),
                        inference_pb2.Word(
                            word="world", start=1.3, end=2.5, probability=0.96
                        ),
                    ],
                    avg_logprob=-0.15,
                ),
            ],
            language="en",
            language_probability=0.99,
            duration=2.5,
        )

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_transcribe_numpy_array(self, mock_channel_fn: MagicMock) -> None:
        """Transcribe with numpy array sends PCM_F32LE_16K format."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel
        mock_stub = MagicMock()
        mock_stub.Transcribe.return_value = self._make_mock_response()

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
            return_value=mock_stub,
        ):
            core = RemoteTranscribeCore("localhost:50052")
            audio = np.zeros(16000, dtype=np.float32)
            result = core.transcribe(audio, "large-v3-turbo")

        assert result.text == "hello world"
        assert len(result.segments) == 1
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 2.5
        assert len(result.segments[0].words) == 2
        assert result.segments[0].words[0].word == "hello"
        assert result.language == "en"
        assert result.duration == pytest.approx(2.5)

        # Verify the request was sent with correct format
        call_args = mock_stub.Transcribe.call_args
        request = call_args[0][0]
        assert request.format == inference_pb2.PCM_F32LE_16K
        assert request.model_id == "large-v3-turbo"

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_transcribe_file_path(self, mock_channel_fn: MagicMock, tmp_path) -> None:
        """Transcribe with file path sends FILE format."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        # Create a temp audio file
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel
        mock_stub = MagicMock()
        mock_stub.Transcribe.return_value = self._make_mock_response()

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
            return_value=mock_stub,
        ):
            core = RemoteTranscribeCore("localhost:50052")
            result = core.transcribe(str(audio_file), "large-v3-turbo")

        assert result.text == "hello world"
        call_args = mock_stub.Transcribe.call_args
        request = call_args[0][0]
        assert request.format == inference_pb2.FILE

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_transcribe_with_config(self, mock_channel_fn: MagicMock) -> None:
        """Config parameters are forwarded to the gRPC request."""
        from dalston.engine_sdk.cores.remote_core import (
            RemoteTranscribeConfig,
            RemoteTranscribeCore,
        )

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel
        mock_stub = MagicMock()
        mock_stub.Transcribe.return_value = self._make_mock_response()

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
            return_value=mock_stub,
        ):
            core = RemoteTranscribeCore("localhost:50052")
            config = RemoteTranscribeConfig(
                language="en",
                beam_size=3,
                vad_filter=False,
                word_timestamps=True,
                temperature=0.2,
                task="translate",
            )
            audio = np.zeros(16000, dtype=np.float32)
            core.transcribe(audio, "large-v3-turbo", config=config)

        call_args = mock_stub.Transcribe.call_args
        request = call_args[0][0]
        assert request.config.language == "en"
        assert request.config.beam_size == 3
        assert request.config.vad_filter is False
        assert request.config.task == "translate"

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_get_status(self, mock_channel_fn: MagicMock) -> None:
        """get_status() returns server status."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel
        mock_stub = MagicMock()
        mock_stub.GetStatus.return_value = inference_pb2.StatusResponse(
            runtime="faster-whisper",
            device="cuda",
            loaded_models=["large-v3-turbo"],
            total_capacity=4,
            available_capacity=3,
            healthy=True,
        )

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
            return_value=mock_stub,
        ):
            core = RemoteTranscribeCore("localhost:50052")
            status = core.get_status()

        assert status["runtime"] == "faster-whisper"
        assert status["healthy"] is True
        assert status["loaded_models"] == ["large-v3-turbo"]
        assert status["available_capacity"] == 3

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_device_reports_remote(self, mock_channel_fn: MagicMock) -> None:
        """Remote core reports device as 'remote'."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        mock_channel_fn.return_value = MagicMock()
        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
        ):
            core = RemoteTranscribeCore("localhost:50052")

        assert core.device == "remote"
        assert core.compute_type == "remote"

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_shutdown_closes_channel(self, mock_channel_fn: MagicMock) -> None:
        """Shutdown closes the gRPC channel."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
        ):
            core = RemoteTranscribeCore("localhost:50052")
            core.shutdown()

        mock_channel.close.assert_called_once()

    @patch("dalston.engine_sdk.cores.remote_core.grpc.insecure_channel")
    def test_optional_segment_fields(self, mock_channel_fn: MagicMock) -> None:
        """Optional segment fields (avg_logprob etc.) are correctly handled."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        mock_channel = MagicMock()
        mock_channel_fn.return_value = mock_channel
        mock_stub = MagicMock()

        # Response with no optional fields set
        mock_stub.Transcribe.return_value = inference_pb2.TranscribeResponse(
            segments=[
                inference_pb2.Segment(start=0.0, end=1.0, text="hi"),
            ],
            language="en",
            language_probability=0.99,
            duration=1.0,
        )

        with patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub",
            return_value=mock_stub,
        ):
            core = RemoteTranscribeCore("localhost:50052")
            audio = np.zeros(16000, dtype=np.float32)
            result = core.transcribe(audio, "large-v3-turbo")

        assert result.segments[0].avg_logprob is None
        assert result.segments[0].compression_ratio is None
        assert result.segments[0].no_speech_prob is None


# ---------------------------------------------------------------------------
# T2: InferenceServer base tests with mocked core
# ---------------------------------------------------------------------------


class TestInferenceServerBase:
    """Test the InferenceServer base class with a mocked core."""

    def _make_server(self, max_concurrent: int = 4):
        from dalston.engine_sdk.inference_server import InferenceServer

        class MockServer(InferenceServer):
            def __init__(self, core, max_concurrent):
                super().__init__(core=core, port=50099, max_concurrent=max_concurrent)

            def get_runtime(self) -> str:
                return "mock-runtime"

            def _do_transcribe(self, audio, model_id, config):
                return inference_pb2.TranscribeResponse(
                    segments=[
                        inference_pb2.Segment(start=0.0, end=1.0, text="mocked"),
                    ],
                    language="en",
                    language_probability=0.99,
                    duration=1.0,
                )

            def _get_loaded_models(self) -> list[str]:
                return ["mock-model"]

        mock_core = MagicMock()
        mock_core.device = "cuda"
        mock_core.shutdown = MagicMock()

        return MockServer(mock_core, max_concurrent), mock_core

    def test_get_status(self) -> None:
        """GetStatus returns correct server info."""
        server, _ = self._make_server(max_concurrent=4)

        async def run():
            ctx = MagicMock()
            response = await server.GetStatus(
                inference_pb2.StatusRequest(), ctx
            )
            assert response.runtime == "mock-runtime"
            assert response.device == "cuda"
            assert list(response.loaded_models) == ["mock-model"]
            assert response.total_capacity == 4
            assert response.healthy is True

        asyncio.run(run())

    def test_proto_config_to_dict(self) -> None:
        """Proto config converts correctly to dict."""
        server, _ = self._make_server()

        config = inference_pb2.TranscribeConfig(
            language="en",
            beam_size=3,
            vad_filter=True,
            word_timestamps=True,
            temperature=0.2,
            task="translate",
            initial_prompt="test",
            hotwords="word1 word2",
        )

        result = server._proto_config_to_dict(config)
        assert result["language"] == "en"
        assert result["beam_size"] == 3
        assert result["vad_filter"] is True
        assert result["temperature"] == pytest.approx(0.2, rel=1e-5)
        assert result["task"] == "translate"
        assert result["initial_prompt"] == "test"
        assert result["hotwords"] == "word1 word2"

    def test_decode_audio_pcm_f32(self) -> None:
        """PCM_F32LE_16K audio is decoded to float32 numpy array."""
        server, _ = self._make_server()

        audio = np.array([0.5, -0.5, 0.1], dtype=np.float32)
        audio_bytes = audio.tobytes()

        result = server._decode_audio(audio_bytes, inference_pb2.PCM_F32LE_16K)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        np.testing.assert_array_almost_equal(result, audio)

    def test_decode_audio_pcm_s16(self) -> None:
        """PCM_S16LE_16K audio is decoded and normalized to float32."""
        server, _ = self._make_server()

        audio_s16 = np.array([16384, -16384, 0], dtype=np.int16)
        audio_bytes = audio_s16.tobytes()

        result = server._decode_audio(audio_bytes, inference_pb2.PCM_S16LE_16K)
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result[0] == pytest.approx(0.5, rel=1e-3)
        assert result[1] == pytest.approx(-0.5, rel=1e-3)

    def test_decode_audio_file(self, tmp_path) -> None:
        """FILE audio is written to temp file and path returned."""
        server, _ = self._make_server()

        audio_data = b"RIFF" + b"\x00" * 100
        result = server._decode_audio(audio_data, inference_pb2.FILE)

        assert isinstance(result, str)
        import os
        assert os.path.exists(result)
        # Clean up
        os.unlink(result)

    def test_semaphore_initial_value(self) -> None:
        """Semaphore starts with max_concurrent value."""
        server, _ = self._make_server(max_concurrent=3)
        assert server._semaphore._value == 3
        assert server._max_concurrent == 3


# ---------------------------------------------------------------------------
# T6/T7: Engine adapter sidecar mode tests
# ---------------------------------------------------------------------------


class TestEngineSidecarMode:
    """Test that engines select RemoteTranscribeCore when DALSTON_INFERENCE_URI is set."""

    def test_faster_whisper_batch_sidecar_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WhisperEngine creates RemoteTranscribeCore when URI is set."""
        monkeypatch.setenv("DALSTON_INFERENCE_URI", "localhost:50052")

        with patch(
            "dalston.engine_sdk.cores.remote_core.grpc.insecure_channel"
        ) as mock_ch, patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub"
        ):
            mock_ch.return_value = MagicMock()

            # Import after patching to avoid GPU imports
            import importlib
            import sys

            # Load the engine module
            engine_path = "engines/stt-transcribe/faster-whisper/engine.py"
            spec = importlib.util.spec_from_file_location(
                "test_fw_engine", f"/home/user/dalston/{engine_path}"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            engine = module.WhisperEngine()

            from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

            assert isinstance(engine._core, RemoteTranscribeCore)

    def test_faster_whisper_batch_standalone_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WhisperEngine creates local TranscribeCore when no URI set."""
        monkeypatch.delenv("DALSTON_INFERENCE_URI", raising=False)

        # Mock the local core creation to avoid GPU imports
        with patch(
            "dalston.engine_sdk.cores.faster_whisper_core.TranscribeCore.from_env"
        ) as mock_from_env:
            mock_core = MagicMock()
            mock_core.device = "cpu"
            mock_core.compute_type = "int8"
            mock_core.manager = MagicMock()
            mock_core.manager.ttl_seconds = 3600
            mock_core.manager.max_loaded = 2
            mock_from_env.return_value = mock_core

            import importlib

            engine_path = "engines/stt-transcribe/faster-whisper/engine.py"
            spec = importlib.util.spec_from_file_location(
                "test_fw_engine_standalone", f"/home/user/dalston/{engine_path}"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            engine = module.WhisperEngine()

            from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

            assert not isinstance(engine._core, RemoteTranscribeCore)

    def test_parakeet_batch_sidecar_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ParakeetEngine creates RemoteTranscribeCore when URI is set."""
        monkeypatch.setenv("DALSTON_INFERENCE_URI", "localhost:50053")

        with patch(
            "dalston.engine_sdk.cores.remote_core.grpc.insecure_channel"
        ) as mock_ch, patch(
            "dalston.engine_sdk.cores.remote_core.inference_pb2_grpc.InferenceServiceStub"
        ):
            mock_ch.return_value = MagicMock()

            import importlib

            engine_path = "engines/stt-transcribe/parakeet/engine.py"
            spec = importlib.util.spec_from_file_location(
                "test_pk_engine", f"/home/user/dalston/{engine_path}"
            )
            module = importlib.util.module_from_spec(spec)

            # Mock torch to avoid GPU dependency
            with patch.dict("sys.modules", {"torch": MagicMock()}):
                spec.loader.exec_module(module)
                engine = module.ParakeetEngine()

            from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

            assert isinstance(engine._core, RemoteTranscribeCore)


# ---------------------------------------------------------------------------
# Integration-style: RemoteTranscribeCore result type compatibility
# ---------------------------------------------------------------------------


class TestRemoteResultCompatibility:
    """Verify RemoteTranscriptionResult fields are compatible with engine output formatting."""

    def test_remote_word_result_has_both_probability_and_confidence(self) -> None:
        """RemoteWordResult exposes both probability and confidence for compatibility."""
        from dalston.engine_sdk.cores.remote_core import RemoteWordResult

        word = RemoteWordResult(
            word="test", start=0.0, end=1.0, probability=0.95, confidence=0.95
        )
        assert word.probability == 0.95
        assert word.confidence == 0.95

    def test_remote_segment_result_has_optional_fields(self) -> None:
        """RemoteSegmentResult optional fields match faster-whisper SegmentResult."""
        from dalston.engine_sdk.cores.remote_core import RemoteSegmentResult

        seg = RemoteSegmentResult(
            start=0.0,
            end=1.0,
            text="test",
            avg_logprob=-0.3,
            compression_ratio=1.5,
            no_speech_prob=0.01,
        )
        assert seg.avg_logprob == -0.3
        assert seg.compression_ratio == 1.5
        assert seg.no_speech_prob == 0.01

        # Also test with None (default)
        seg_default = RemoteSegmentResult(start=0.0, end=1.0, text="test")
        assert seg_default.avg_logprob is None
        assert seg_default.compression_ratio is None

    def test_remote_transcription_result_text_assembly(self) -> None:
        """RemoteTranscriptionResult.text is assembled from segments."""
        from dalston.engine_sdk.cores.remote_core import (
            RemoteSegmentResult,
            RemoteTranscriptionResult,
        )

        result = RemoteTranscriptionResult(
            text="hello world how are you",
            segments=[
                RemoteSegmentResult(start=0.0, end=2.0, text="hello world"),
                RemoteSegmentResult(start=2.1, end=4.0, text="how are you"),
            ],
            language="en",
            language_probability=0.99,
            duration=4.0,
        )

        assert result.text == "hello world how are you"
        assert len(result.segments) == 2
        assert result.duration == 4.0


# ---------------------------------------------------------------------------
# Config conversion tests
# ---------------------------------------------------------------------------


class TestConfigConversion:
    """Test config object → proto config conversion."""

    def test_none_config_defaults(self) -> None:
        """None config produces sensible defaults."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        proto_config = RemoteTranscribeCore._to_proto_config(None)
        assert proto_config.beam_size == 5
        assert proto_config.vad_filter is True
        assert proto_config.word_timestamps is True
        assert proto_config.task == "transcribe"

    def test_dict_config(self) -> None:
        """Dict config is correctly converted."""
        from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

        config = {
            "language": "fr",
            "beam_size": 3,
            "vad_filter": False,
            "temperature": 0.5,
        }
        proto_config = RemoteTranscribeCore._to_proto_config(config)
        assert proto_config.language == "fr"
        assert proto_config.beam_size == 3
        assert proto_config.vad_filter is False
        assert proto_config.temperature == pytest.approx(0.5)

    def test_dataclass_config(self) -> None:
        """Dataclass-style config is correctly converted via getattr."""
        from dalston.engine_sdk.cores.remote_core import (
            RemoteTranscribeConfig,
            RemoteTranscribeCore,
        )

        config = RemoteTranscribeConfig(
            language="de",
            beam_size=7,
            vad_filter=True,
            word_timestamps=False,
            task="translate",
        )
        proto_config = RemoteTranscribeCore._to_proto_config(config)
        assert proto_config.language == "de"
        assert proto_config.beam_size == 7
        assert proto_config.task == "translate"
        assert proto_config.word_timestamps is False
