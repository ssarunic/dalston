"""Tests for M71: Parakeet RNNT/TDT cache-aware streaming inference.

Covers:
- NemoInference.decoder_type() and supports_native_streaming_decode()
- NemoInference.transcribe_streaming() with mock chunk iterator
- CTC variant raises RuntimeError if transcribe_streaming() called
- RT engine decoder-aware dispatch (use_streaming_decode, get_streaming_decode_fn)
- SessionHandler streaming decode integration
- Word ordering and timestamp continuity across chunk boundaries
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.common.pipeline_types import TranscribeInput
from dalston.engine_sdk.inference.nemo_inference import (
    NemoInference,
    NeMoSegmentResult,
    NeMoTranscriptionResult,
    NeMoWordResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_INJECTED_MODULE_NAME = "m71_parakeet_rt"


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    """Remove the explicitly injected module entry after each test.

    Only removes the injected name itself, not transitive imports.
    Transitive imports (torch C++ extensions, PyO3/Rust extensions like
    safetensors) cannot be safely removed: they register global state that
    can only be initialised once per interpreter process. Removing them from
    sys.modules causes re-import to crash in subsequent tests.
    """
    yield
    sys.modules.pop(_INJECTED_MODULE_NAME, None)


def _load_rt_engine_module():
    """Load the RT parakeet engine module via importlib."""
    engine_path = Path("engines/stt-unified/nemo/rt_engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet streaming engine not found")

    spec = importlib.util.spec_from_file_location("m71_parakeet_rt", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m71_parakeet_rt"] = module
    spec.loader.exec_module(module)
    return module


def _make_mock_core() -> MagicMock:
    """Create a mocked NemoInference with sensible defaults."""
    mock_core = MagicMock(spec=NemoInference)
    mock_core.device = "cpu"
    mock_core.manager = MagicMock()
    mock_core.transcribe.return_value = NeMoTranscriptionResult(
        text="hello world",
        segments=[
            NeMoSegmentResult(
                start=0.0,
                end=1.0,
                text="hello world",
                words=[
                    NeMoWordResult(word="hello", start=0.0, end=0.5),
                    NeMoWordResult(word="world", start=0.5, end=1.0),
                ],
            )
        ],
    )
    return mock_core


# ---------------------------------------------------------------------------
# T1: NemoInference decoder_type and supports_native_streaming_decode
# ---------------------------------------------------------------------------


class TestParakeetCoreDecoderType:
    """Verify decoder_type() returns correct architecture for each model."""

    def _make_core(self) -> NemoInference:
        """Create a NemoInference with a mocked manager."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture = MagicMock(
            side_effect=lambda model_id: (
                "rnnt" if "rnnt" in model_id else "tdt" if "tdt" in model_id else "ctc"
            )
        )
        return core

    def test_rnnt_model_returns_rnnt(self) -> None:
        core = self._make_core()
        assert core.decoder_type("parakeet-rnnt-1.1b") == "rnnt"

    def test_tdt_model_returns_tdt(self) -> None:
        core = self._make_core()
        assert core.decoder_type("parakeet-tdt-1.1b") == "tdt"

    def test_ctc_model_returns_ctc(self) -> None:
        core = self._make_core()
        assert core.decoder_type("parakeet-ctc-0.6b") == "ctc"

    def test_rnnt_supports_native_streaming(self) -> None:
        core = self._make_core()
        assert core.supports_native_streaming_decode("parakeet-rnnt-1.1b") is True

    def test_tdt_supports_native_streaming(self) -> None:
        core = self._make_core()
        assert core.supports_native_streaming_decode("parakeet-tdt-1.1b") is True

    def test_ctc_does_not_support_streaming(self) -> None:
        core = self._make_core()
        assert core.supports_native_streaming_decode("parakeet-ctc-0.6b") is False


# ---------------------------------------------------------------------------
# T1: NemoInference.transcribe_streaming()
# ---------------------------------------------------------------------------


class TestParakeetCoreTranscribeStreaming:
    """Verify transcribe_streaming() behavior with mocked NeMo model."""

    def _make_core_with_mock_streaming(self) -> NemoInference:
        """Create a core with a mock manager configured for RNNT streaming."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.device = "cpu"

        # Setup get_architecture to return rnnt
        core._manager.get_architecture = MagicMock(return_value="rnnt")

        return core

    def test_ctc_raises_engine_id_error(self) -> None:
        """CTC variant must raise RuntimeError if streaming is attempted."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture = MagicMock(return_value="ctc")

        chunks = iter([np.zeros(1600, dtype=np.float32)])

        with pytest.raises(RuntimeError, match="not supported for 'ctc'"):
            list(core.transcribe_streaming(chunks, "parakeet-ctc-0.6b"))

    def test_rnnt_acquires_and_releases_model(self) -> None:
        """Verify model acquire/release lifecycle during streaming."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture = MagicMock(return_value="rnnt")
        core._manager.device = "cpu"

        # Mock _run_streaming_inference to yield nothing
        with patch.object(
            NemoInference,
            "_run_streaming_inference",
            return_value=iter([]),
        ):
            list(
                core.transcribe_streaming(
                    iter([np.zeros(1600, dtype=np.float32)]),
                    "parakeet-rnnt-1.1b",
                )
            )

        core._manager.acquire.assert_called_once_with("parakeet-rnnt-1.1b")
        core._manager.release.assert_called_once_with("parakeet-rnnt-1.1b")

    def test_model_released_on_error(self) -> None:
        """Model must be released even if streaming raises an exception."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture = MagicMock(return_value="rnnt")
        core._manager.device = "cpu"

        def _failing_stream(*args, **kwargs):
            raise ValueError("test error")

        with patch.object(
            NemoInference,
            "_run_streaming_inference",
            side_effect=_failing_stream,
        ):
            with pytest.raises(ValueError, match="test error"):
                list(
                    core.transcribe_streaming(
                        iter([np.zeros(1600, dtype=np.float32)]),
                        "parakeet-rnnt-1.1b",
                    )
                )

        core._manager.release.assert_called_once_with("parakeet-rnnt-1.1b")

    def test_tdt_also_accepted(self) -> None:
        """TDT models should also be accepted for streaming."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture = MagicMock(return_value="tdt")
        core._manager.device = "cpu"

        with patch.object(
            NemoInference,
            "_run_streaming_inference",
            return_value=iter([]),
        ):
            result = list(
                core.transcribe_streaming(
                    iter([np.zeros(1600, dtype=np.float32)]),
                    "parakeet-tdt-1.1b",
                )
            )

        assert result == []
        core._manager.acquire.assert_called_once_with("parakeet-tdt-1.1b")

    def test_run_streaming_carries_previous_pred_out(self, monkeypatch) -> None:
        """RNNT stream steps must carry decoder state via previous_pred_out."""
        from types import SimpleNamespace

        import nemo.collections.asr.parts.utils.streaming_utils as streaming_utils

        class FakeStreamingBuffer:
            def __init__(self, model):
                self._pending = []

            def append_audio(self, audio_chunk, stream_id=-1):
                n_frames = max(1, int(len(audio_chunk) / 160))
                chunk_audio = torch.zeros((1, 80, n_frames), dtype=torch.float32)
                chunk_lengths = torch.tensor([n_frames], dtype=torch.long)
                self._pending.append((chunk_audio, chunk_lengths))

            def __iter__(self):
                while self._pending:
                    yield self._pending.pop(0)

            def is_buffer_empty(self):
                return len(self._pending) == 0

        monkeypatch.setattr(
            streaming_utils,
            "CacheAwareStreamingAudioBuffer",
            FakeStreamingBuffer,
        )

        class FakeEncoder:
            def __init__(self):
                self.subsampling_factor = 8
                self.max_audio_length = 5000
                self.last_set = None
                self.streaming_cfg = None

            def setup_streaming_params(
                self,
                chunk_size=None,
                shift_size=None,
                left_chunks=None,
            ):
                chunk = int(chunk_size or 16)
                shift = int(shift_size or chunk)
                left = int(left_chunks or 2)
                self.streaming_cfg = SimpleNamespace(
                    drop_extra_pre_encoded=2,
                    chunk_size=[chunk * 8 - 7, chunk * 8],
                    shift_size=[shift * 8 - 7, shift * 8],
                    valid_out_len=chunk,
                    last_channel_cache_size=left * chunk,
                )

            def set_max_audio_length(self, value):
                self.last_set = value
                self.max_audio_length = value

            def get_initial_cache_state(self, batch_size=1):
                c = torch.zeros((1, 1, 1), dtype=torch.float32)
                return c, c.clone(), c.clone()

        class FakeHyp:
            def __init__(self, text: str):
                self.text = text

        class FakeModel:
            def __init__(self):
                self.encoder = FakeEncoder()
                self.calls = []

            def conformer_stream_step(self, **kwargs):
                self.calls.append(kwargs)
                call_idx = len(self.calls)
                # In reality, transcribed_texts is often new-only
                new_text = [FakeHyp("hello")] if call_idx == 1 else [FakeHyp(" world")]
                # previous_hypotheses is cumulative
                full_text = "hello" if call_idx == 1 else "hello world"
                return (
                    f"pred-{call_idx}",
                    new_text,
                    kwargs["cache_last_channel"],
                    kwargs["cache_last_time"],
                    kwargs["cache_last_channel_len"],
                    [FakeHyp(full_text)],
                )

        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.device = "cpu"

        model = FakeModel()
        audio_iter = iter(
            [
                np.zeros(1600, dtype=np.float32),
                np.zeros(1600, dtype=np.float32),
            ]
        )
        with patch.dict("os.environ", {"DALSTON_RNNT_MIN_APPEND_SAMPLES": "1"}):
            words = list(core._run_streaming_inference(model, audio_iter, chunk_ms=160))

        assert [w.word for w in words] == ["hello", "world"]
        assert model.calls[0]["previous_pred_out"] is None
        assert model.calls[1]["previous_pred_out"] == "pred-1"
        assert model.calls[0]["keep_all_outputs"] is False
        assert model.calls[1]["keep_all_outputs"] is True

    def test_configures_bounded_streaming_params_from_chunk_ms(self) -> None:
        """Chunk ms and left-chunks env drive bounded streaming params."""
        from types import SimpleNamespace

        class FakeEncoder:
            def __init__(self):
                self.calls = []
                self.streaming_cfg = None

            def setup_streaming_params(self, **kwargs):
                self.calls.append(kwargs)
                self.streaming_cfg = SimpleNamespace(
                    chunk_size=[153, 160],
                    shift_size=[153, 160],
                    valid_out_len=16,
                    last_channel_cache_size=48,
                )

        class FakeModel:
            def __init__(self):
                self.encoder = FakeEncoder()

        model = FakeModel()
        with patch.dict("os.environ", {"DALSTON_RNNT_LEFT_CHUNKS": "3"}):
            NemoInference._configure_bounded_streaming_params(model, chunk_ms=160)

        assert model.encoder.calls == [
            {
                "chunk_size": 16,
                "shift_size": 16,
                "left_chunks": 3,
            }
        ]
        assert model.encoder.streaming_cfg.last_channel_cache_size == 48

    def test_streaming_min_append_samples_uses_stream_cfg_window(self) -> None:
        """Default coalescing window is derived from streaming chunk size."""
        from types import SimpleNamespace

        class FakeEncoder:
            def __init__(self):
                self.streaming_cfg = SimpleNamespace(chunk_size=[121, 128])

        class FakeModel:
            def __init__(self):
                self.encoder = FakeEncoder()

        with patch.dict("os.environ", {}, clear=True):
            min_samples = NemoInference._streaming_min_append_samples(
                FakeModel(),
                chunk_ms=160,
            )

        assert min_samples == 20480

    def test_streaming_min_append_samples_honors_env_override(self) -> None:
        """Environment override should take precedence over derived window."""
        with patch.dict("os.environ", {"DALSTON_RNNT_MIN_APPEND_SAMPLES": "8000"}):
            min_samples = NemoInference._streaming_min_append_samples(
                object(),
                chunk_ms=160,
            )

        assert min_samples == 8000

    def test_expands_positional_capacity_when_streaming_cache_exceeds_it(self) -> None:
        """Increase encoder max_audio_length when cache-aware context requires it."""
        from types import SimpleNamespace

        class FakeEncoder:
            def __init__(self):
                self.subsampling_factor = 8
                self.max_audio_length = 5000
                self.streaming_cfg = SimpleNamespace(
                    last_channel_cache_size=10000,
                    chunk_size=[577, 584],
                )
                self.last_set = None

            def set_max_audio_length(self, value):
                self.last_set = value
                self.max_audio_length = value

        class FakeModel:
            def __init__(self):
                self.encoder = FakeEncoder()

        model = FakeModel()
        NemoInference._ensure_streaming_positional_capacity(model)

        assert model.encoder.last_set is not None
        assert model.encoder.max_audio_length > 10000

    def test_does_not_expand_positional_capacity_when_already_sufficient(self) -> None:
        """No-op when encoder max_audio_length already covers cache + chunk."""
        from types import SimpleNamespace

        class FakeEncoder:
            def __init__(self):
                self.subsampling_factor = 8
                self.max_audio_length = 15000
                self.streaming_cfg = SimpleNamespace(
                    last_channel_cache_size=10000,
                    chunk_size=[577, 584],
                )
                self.last_set = None

            def set_max_audio_length(self, value):
                self.last_set = value
                self.max_audio_length = value

        class FakeModel:
            def __init__(self):
                self.encoder = FakeEncoder()

        model = FakeModel()
        NemoInference._ensure_streaming_positional_capacity(model)

        assert model.encoder.last_set is None
        assert model.encoder.max_audio_length == 15000


# ---------------------------------------------------------------------------
# T2: RT engine decoder-aware dispatch
# ---------------------------------------------------------------------------


torch = pytest.importorskip("torch")


class TestRTEngineStreamingDispatch:
    """Verify RT engine uses streaming decode for RNNT/TDT, not for CTC."""

    def _build_engine(
        self,
        mock_core: MagicMock | None = None,
    ):
        """Build a NemoRealtimeEngine with controlled env."""
        module = _load_rt_engine_module()

        if mock_core is None:
            mock_core = _make_mock_core()

        with patch.dict(
            "os.environ",
            {
                "DALSTON_RNNT_CHUNK_MS": "160",
            },
        ):
            engine = module.NemoRealtimeEngine(core=mock_core)
        return engine

    def test_use_streaming_decode_rnnt(self) -> None:
        """RNNT model with streaming enabled should use streaming decode."""
        mock_core = _make_mock_core()
        mock_core.supports_native_streaming_decode.return_value = True
        engine = self._build_engine(mock_core)

        assert engine.use_streaming_decode("parakeet-rnnt-1.1b") is True

    def test_use_streaming_decode_ctc(self) -> None:
        """CTC model should never use streaming decode."""
        mock_core = _make_mock_core()
        mock_core.supports_native_streaming_decode.return_value = False
        engine = self._build_engine(mock_core)

        assert engine.use_streaming_decode("parakeet-ctc-0.6b") is False

    def test_use_streaming_decode_tdt(self) -> None:
        """TDT model with streaming enabled should use streaming decode."""
        mock_core = _make_mock_core()
        mock_core.supports_native_streaming_decode.return_value = True
        engine = self._build_engine(mock_core)

        assert engine.use_streaming_decode("parakeet-tdt-1.1b") is True

    def test_get_streaming_decode_fn_rnnt(self) -> None:
        """get_streaming_decode_fn returns callback for RNNT."""
        mock_core = _make_mock_core()
        mock_core.supports_native_streaming_decode.return_value = True
        engine = self._build_engine(mock_core)
        engine._core = mock_core  # Ensure core is set

        fn = engine.get_streaming_decode_fn("parakeet-rnnt-1.1b")
        assert fn is not None

    def test_get_streaming_decode_fn_ctc_returns_none(self) -> None:
        """get_streaming_decode_fn returns None for CTC."""
        mock_core = _make_mock_core()
        mock_core.supports_native_streaming_decode.return_value = False
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("parakeet-ctc-0.6b")
        assert fn is None


# ---------------------------------------------------------------------------
# T2: RT engine transcribe_streaming() output shape
# ---------------------------------------------------------------------------


class TestRTEngineTranscribeStreaming:
    """Verify transcribe_streaming yields Transcript per word."""

    def _build_engine(self):
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        # Setup streaming to yield word results
        mock_core.supports_native_streaming_decode.return_value = True
        mock_core.decoder_type.return_value = "rnnt"
        mock_core.transcribe_streaming.return_value = iter(
            [
                NeMoWordResult(word="hello", start=0.0, end=0.5, confidence=0.95),
                NeMoWordResult(word="world", start=0.5, end=1.0, confidence=0.90),
            ]
        )

        with patch.dict(
            "os.environ",
            {
                "DALSTON_RNNT_CHUNK_MS": "160",
            },
        ):
            engine = module.NemoRealtimeEngine(core=mock_core)
        return engine

    def test_yields_transcribe_results(self) -> None:
        """Each yielded result should be a Transcript with one word."""
        engine = self._build_engine()
        audio_iter = iter([np.zeros(1600, dtype=np.float32)])

        results = list(
            engine.transcribe_streaming(audio_iter, "en", "parakeet-rnnt-1.1b")
        )

        assert len(results) == 2
        assert results[0].text == "hello"
        assert results[0].language == "en"
        assert len(results[0].segments[0].words) == 1
        assert results[0].segments[0].words[0].text == "hello"
        assert results[0].segments[0].words[0].start == 0.0
        assert results[0].segments[0].words[0].end == 0.5

        assert results[1].text == "world"
        assert results[1].segments[0].words[0].start == 0.5

    def test_word_ordering_preserved(self) -> None:
        """Words should arrive in correct order across chunks."""
        engine = self._build_engine()
        audio_iter = iter([np.zeros(1600, dtype=np.float32)])

        results = list(
            engine.transcribe_streaming(audio_iter, "en", "parakeet-rnnt-1.1b")
        )

        words = [r.text for r in results]
        assert words == ["hello", "world"]

    def test_timestamp_continuity(self) -> None:
        """Word end times should not exceed next word start times."""
        engine = self._build_engine()
        audio_iter = iter([np.zeros(1600, dtype=np.float32)])

        results = list(
            engine.transcribe_streaming(audio_iter, "en", "parakeet-rnnt-1.1b")
        )

        for i in range(len(results) - 1):
            current_end = results[i].segments[0].words[0].end
            next_start = results[i + 1].segments[0].words[0].start
            assert current_end <= next_start, (
                f"Word {i} end ({current_end}) > word {i + 1} start ({next_start})"
            )


# ---------------------------------------------------------------------------
# T3/T4: RT engine — existing transcribe path unchanged
# ---------------------------------------------------------------------------


class TestRTEngineExistingPathUnchanged:
    """Verify that the existing VAD-accumulate transcribe() path still works."""

    def _build_engine(self):
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        engine = module.NemoRealtimeEngine(core=mock_core)
        return engine, mock_core

    def test_transcribe_still_works(self) -> None:
        """The regular transcribe() method should be unaffected."""
        engine, mock_core = self._build_engine()
        audio = np.zeros(16000, dtype=np.float32)

        result = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-tdt-1.1b"),
        )

        assert result.text == "hello world"
        mock_core.transcribe.assert_called_once()

    def test_transcribe_with_ctc_uses_regular_path(self) -> None:
        """CTC models should always use regular transcribe()."""
        engine, mock_core = self._build_engine()
        audio = np.zeros(16000, dtype=np.float32)

        result = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-ctc-0.6b"),
        )

        assert result.text == "hello world"
        mock_core.transcribe.assert_called_once()


# ---------------------------------------------------------------------------
# T4: Chunk MS configuration
# ---------------------------------------------------------------------------


class TestChunkMSConfig:
    """Verify DALSTON_RNNT_CHUNK_MS is read and used."""

    def test_default_chunk_ms(self) -> None:
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        with patch.dict("os.environ", {}, clear=False):
            # Remove DALSTON_RNNT_CHUNK_MS if set
            import os

            os.environ.pop("DALSTON_RNNT_CHUNK_MS", None)
            engine = module.NemoRealtimeEngine(core=mock_core)

        assert engine._rnnt_chunk_ms == 160

    def test_custom_chunk_ms(self) -> None:
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        with patch.dict(
            "os.environ",
            {
                "DALSTON_RNNT_CHUNK_MS": "320",
            },
        ):
            engine = module.NemoRealtimeEngine(core=mock_core)

        assert engine._rnnt_chunk_ms == 320


# ---------------------------------------------------------------------------
# Regression: batch engine path is unaffected
# ---------------------------------------------------------------------------


class TestBatchEngineUnaffected:
    """Verify NemoInference.transcribe() is unchanged (batch path)."""

    def test_transcribe_unchanged(self) -> None:
        """NemoInference.transcribe() should work as before."""
        core = object.__new__(NemoInference)
        core._manager = MagicMock()

        mock_model = MagicMock()
        core._manager.acquire.return_value = mock_model
        core._manager.device = "cpu"

        # Mock the transcribe call to return a simple result
        hypothesis = MagicMock()
        hypothesis.text = "test"
        hypothesis.timestep = None
        mock_model.transcribe.return_value = [[hypothesis]]

        with patch("torch.inference_mode"):
            result = core.transcribe(
                np.zeros(16000, dtype=np.float32),
                "parakeet-rnnt-1.1b",
            )

        assert result.text == "test"
        core._manager.acquire.assert_called_once_with("parakeet-rnnt-1.1b")
        core._manager.release.assert_called_once_with("parakeet-rnnt-1.1b")
