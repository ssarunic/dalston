"""Tests for M72: Nemotron full streaming via conformer_stream_step.

Covers:
- NeMoModelManager.is_cache_aware_streaming() predicate
- NemoInference.is_cache_aware_streaming() passthrough
- NemoInference.transcribe_streaming() routing: Nemotron → _run_cache_aware_streaming,
  offline RNNT → _run_streaming_inference
- _run_cache_aware_streaming with mocked model.conformer_stream_step
- RT engine: get_streaming_decode_fn returns callable for Nemotron, None for others
- DALSTON_RNNT_BUFFER_SECS env var wiring
- Model ID normalization for Nemotron aliases
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.engine_sdk.inference.nemo_inference import NemoInference
from dalston.engine_sdk.managers.nemo import NeMoModelManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INJECTED_MODULE_NAME = "m72_nemo_rt"


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    yield
    sys.modules.pop(_INJECTED_MODULE_NAME, None)


def _load_rt_engine_module():
    engine_path = Path("engines/stt-transcribe/nemo/rt_engine.py")
    if not engine_path.exists():
        pytest.skip("NeMo RT engine not found")
    spec = importlib.util.spec_from_file_location(_INJECTED_MODULE_NAME, engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_INJECTED_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _make_mock_core() -> MagicMock:
    mock_core = MagicMock(spec=NemoInference)
    mock_core.device = "cpu"
    mock_core.manager = MagicMock()
    return mock_core


# ---------------------------------------------------------------------------
# Step 1: NeMoModelManager.is_cache_aware_streaming()
# ---------------------------------------------------------------------------


class TestCacheAwareStreamingFlag:
    """NeMoModelManager.is_cache_aware_streaming() returns True only for Nemotron."""

    def _make_manager(self) -> NeMoModelManager:
        return object.__new__(NeMoModelManager)

    def test_nemotron_returns_true(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("nemotron-streaming-rnnt-0.6b") is True

    def test_parakeet_rnnt_06b_returns_false(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("parakeet-rnnt-0.6b") is False

    def test_parakeet_rnnt_11b_returns_false(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("parakeet-rnnt-1.1b") is False

    def test_parakeet_tdt_returns_false(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("parakeet-tdt-1.1b") is False

    def test_parakeet_ctc_returns_false(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("parakeet-ctc-0.6b") is False

    def test_unknown_model_returns_false(self) -> None:
        mgr = self._make_manager()
        assert mgr.is_cache_aware_streaming("some-unknown-model") is False


# ---------------------------------------------------------------------------
# Step 2a: NemoInference.is_cache_aware_streaming() passthrough
# ---------------------------------------------------------------------------


class TestNemoInferenceCacheAwarePassthrough:
    """NemoInference.is_cache_aware_streaming() delegates to the manager."""

    def _make_core(self, manager_returns: bool) -> NemoInference:
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.is_cache_aware_streaming.return_value = manager_returns
        return core

    def test_returns_true_when_manager_true(self) -> None:
        core = self._make_core(True)
        assert core.is_cache_aware_streaming("nemotron-streaming-rnnt-0.6b") is True
        core._manager.is_cache_aware_streaming.assert_called_once_with(
            "nemotron-streaming-rnnt-0.6b"
        )

    def test_returns_false_when_manager_false(self) -> None:
        core = self._make_core(False)
        assert core.is_cache_aware_streaming("parakeet-rnnt-1.1b") is False


# ---------------------------------------------------------------------------
# Step 2b: transcribe_streaming routing
# ---------------------------------------------------------------------------


class TestTranscribeStreamingRouting:
    """transcribe_streaming routes to _run_cache_aware_streaming vs _run_streaming_inference."""

    def _make_core(self, is_cache_aware: bool) -> NemoInference:
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture.return_value = "rnnt"
        core._manager.is_cache_aware_streaming.return_value = is_cache_aware
        return core

    def test_nemotron_routes_to_cache_aware_streaming(self) -> None:
        core = self._make_core(is_cache_aware=True)

        with (
            patch.object(
                NemoInference, "_run_cache_aware_streaming", return_value=iter([])
            ) as mock_cas,
            patch.object(
                NemoInference, "_run_streaming_inference", return_value=iter([])
            ) as mock_rsi,
        ):
            list(
                core.transcribe_streaming(
                    iter([np.zeros(1600, dtype=np.float32)]),
                    "nemotron-streaming-rnnt-0.6b",
                    chunk_ms=160,
                )
            )

        mock_cas.assert_called_once()
        mock_rsi.assert_not_called()

    def test_offline_rnnt_routes_to_run_streaming_inference(self) -> None:
        core = self._make_core(is_cache_aware=False)

        with (
            patch.object(
                NemoInference, "_run_cache_aware_streaming", return_value=iter([])
            ) as mock_cas,
            patch.object(
                NemoInference, "_run_streaming_inference", return_value=iter([])
            ) as mock_rsi,
        ):
            list(
                core.transcribe_streaming(
                    iter([np.zeros(1600, dtype=np.float32)]),
                    "parakeet-rnnt-1.1b",
                )
            )

        mock_rsi.assert_called_once()
        mock_cas.assert_not_called()


# ---------------------------------------------------------------------------
# Step 2c: _run_cache_aware_streaming behaviour
# ---------------------------------------------------------------------------


torch_mod = pytest.importorskip("torch")


class TestRunCacheAwareStreaming:
    """_run_cache_aware_streaming uses model.conformer_stream_step per chunk."""

    def _make_core(self) -> NemoInference:
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        return core

    _BUFFER_PATCH = "nemo.collections.asr.parts.utils.streaming_utils.CacheAwareStreamingAudioBuffer"

    def _make_mock_buffer(self) -> MagicMock:
        """Mock CacheAwareStreamingAudioBuffer whose append_audio returns dummy features."""
        import torch

        mock_buf = MagicMock()
        mock_buf.append_audio.return_value = (
            torch.zeros(1, 80, 10),  # processed_signal
            torch.tensor([10]),  # processed_signal_length
            0,  # stream_id
        )
        return mock_buf

    def _make_mock_model(self, hyp_texts: list[str]) -> MagicMock:
        """Mock model whose conformer_stream_step returns successive cumulative hypotheses.

        Each response tuple matches the return signature of conformer_stream_step:
        (greedy_predictions, all_hyps, cache_channel, cache_time, cache_len, best_hyp)
        """
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        responses = []
        for i, text in enumerate(hyp_texts):
            mock_hyp = MagicMock()
            mock_hyp.text = text
            responses.append(([], [], f"ch{i}", f"ct{i}", f"cl{i}", [mock_hyp]))

        mock_model.conformer_stream_step.side_effect = responses
        return mock_model

    def test_yields_new_words_per_chunk(self) -> None:
        """Words emitted when hypothesis grows across chunks."""
        core = self._make_core()
        mock_model = self._make_mock_model(["hello", "hello world"])
        chunks = [np.zeros(2560, dtype=np.float32), np.zeros(2560, dtype=np.float32)]

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            words = list(core._run_cache_aware_streaming(mock_model, iter(chunks), 160))

        assert [w.word for w in words] == ["hello", "world"]

    def test_no_output_when_hypothesis_unchanged(self) -> None:
        """No words emitted if consecutive hypotheses are identical."""
        core = self._make_core()
        mock_model = self._make_mock_model(["hello", "hello"])
        chunks = [np.zeros(2560, dtype=np.float32), np.zeros(2560, dtype=np.float32)]

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            words = list(core._run_cache_aware_streaming(mock_model, iter(chunks), 160))

        assert [w.word for w in words] == ["hello"]

    def test_empty_iterator_yields_nothing(self) -> None:
        core = self._make_core()
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            words = list(core._run_cache_aware_streaming(mock_model, iter([]), 160))

        assert words == []
        mock_model.conformer_stream_step.assert_not_called()

    def test_cache_carried_between_chunks(self) -> None:
        """Cache tensors from step N are passed to conformer_stream_step at step N+1."""
        core = self._make_core()
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        ch0, ct0, cl0 = MagicMock(), MagicMock(), MagicMock()
        hyp0, hyp1 = MagicMock(), MagicMock()
        hyp0.text, hyp1.text = "hello", "hello world"

        mock_model.conformer_stream_step.side_effect = [
            ([], [], ch0, ct0, cl0, [hyp0]),
            ([], [], MagicMock(), MagicMock(), MagicMock(), [hyp1]),
        ]

        chunks = [np.zeros(2560, dtype=np.float32), np.zeros(2560, dtype=np.float32)]

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            list(core._run_cache_aware_streaming(mock_model, iter(chunks), 160))

        _, kwargs = mock_model.conformer_stream_step.call_args_list[1]
        assert kwargs["cache_last_channel"] is ch0
        assert kwargs["cache_last_time"] is ct0
        assert kwargs["cache_last_channel_len"] is cl0

    def test_previous_hypotheses_carried_between_chunks(self) -> None:
        """RNNT decoder hypothesis from step N passed as previous_hypotheses to step N+1."""
        core = self._make_core()
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        hyp0, hyp1 = MagicMock(), MagicMock()
        hyp0.text, hyp1.text = "hello", "hello world"
        best_hyp_list_0 = [hyp0]

        mock_model.conformer_stream_step.side_effect = [
            ([], [], MagicMock(), MagicMock(), MagicMock(), best_hyp_list_0),
            ([], [], MagicMock(), MagicMock(), MagicMock(), [hyp1]),
        ]

        chunks = [np.zeros(2560, dtype=np.float32), np.zeros(2560, dtype=np.float32)]

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            list(core._run_cache_aware_streaming(mock_model, iter(chunks), 160))

        _, kwargs = mock_model.conformer_stream_step.call_args_list[1]
        assert kwargs["previous_hypotheses"] is best_hyp_list_0

    def test_empty_best_hyp_skipped(self) -> None:
        """No words emitted if conformer_stream_step returns empty best_hyp."""
        core = self._make_core()
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_model.conformer_stream_step.return_value = ([], [], None, None, None, [])

        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            words = list(
                core._run_cache_aware_streaming(
                    mock_model, iter([np.zeros(2560, dtype=np.float32)]), 160
                )
            )

        assert words == []

    def test_words_not_repeated_when_best_hyp_flickers_empty(self) -> None:
        """Bug regression: prev_text should not be cleared when best_hyp is empty."""
        core = self._make_core()
        mock_model = MagicMock()
        mock_model.encoder.get_initial_cache_state.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        hyp_hello = MagicMock()
        hyp_hello.text = "hello"

        # Step 1: "hello"
        # Step 2: [] (empty)
        # Step 3: "hello world"
        mock_model.conformer_stream_step.side_effect = [
            ([], [], "c1", "t1", "l1", [hyp_hello]),
            ([], [], "c2", "t2", "l2", []),
            ([], [], "c3", "t3", "l3", [MagicMock(text="hello world")]),
        ]

        chunks = [np.zeros(2560), np.zeros(2560), np.zeros(2560)]
        with patch(self._BUFFER_PATCH, return_value=self._make_mock_buffer()):
            words = list(core._run_cache_aware_streaming(mock_model, iter(chunks), 160))

        # Should only yield "hello" and "world", NOT "hello", "hello", "world"
        assert [w.word for w in words] == ["hello", "world"]


# ---------------------------------------------------------------------------
# Step 3: RT engine get_streaming_decode_fn routing
# ---------------------------------------------------------------------------


torch = pytest.importorskip("torch")


class TestRTEngineStreamingDecodeRoute:
    """get_streaming_decode_fn returns callable for Nemotron, None for others."""

    def _build_engine(self, mock_core: MagicMock | None = None):
        module = _load_rt_engine_module()
        if mock_core is None:
            mock_core = _make_mock_core()
        engine = module.NemoRealtimeEngine(core=mock_core)
        return engine

    def test_nemotron_returns_callable(self) -> None:
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = True
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("nemotron-streaming-rnnt-0.6b")
        assert fn is not None
        assert callable(fn)

    def test_nemotron_fn_is_transcribe_streaming(self) -> None:
        """The returned callable should be the engine's transcribe_streaming method."""
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = True
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("nemotron-streaming-rnnt-0.6b")
        assert fn == engine.transcribe_streaming

    def test_offline_rnnt_returns_none(self) -> None:
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = False
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("parakeet-rnnt-1.1b")
        assert fn is None

    def test_offline_tdt_returns_none(self) -> None:
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = False
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("parakeet-tdt-1.1b")
        assert fn is None

    def test_ctc_returns_none(self) -> None:
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = False
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("parakeet-ctc-0.6b")
        assert fn is None

    def test_no_core_returns_none(self) -> None:
        module = _load_rt_engine_module()
        engine = module.NemoRealtimeEngine(core=None)

        fn = engine.get_streaming_decode_fn("nemotron-streaming-rnnt-0.6b")
        assert fn is None

    def test_short_alias_nemotron_0_6b_activates_streaming(self) -> None:
        """'nemotron-0.6b' short alias should also activate streaming."""
        mock_core = _make_mock_core()

        def _is_cache_aware(model_id: str) -> bool:
            return model_id == "nemotron-streaming-rnnt-0.6b"

        mock_core.is_cache_aware_streaming.side_effect = _is_cache_aware
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("nemotron-0.6b")
        assert fn is not None

    def test_hf_path_activates_streaming(self) -> None:
        """nvidia/nemotron-speech-streaming-en-0.6b should normalize and activate streaming."""
        mock_core = _make_mock_core()

        def _is_cache_aware(model_id: str) -> bool:
            return model_id == "nemotron-streaming-rnnt-0.6b"

        mock_core.is_cache_aware_streaming.side_effect = _is_cache_aware
        engine = self._build_engine(mock_core)

        fn = engine.get_streaming_decode_fn("nvidia/nemotron-speech-streaming-en-0.6b")
        assert fn is not None


# ---------------------------------------------------------------------------
# Buffer secs env var wiring
# ---------------------------------------------------------------------------


class TestBufferSecsConfig:
    """DALSTON_RNNT_BUFFER_SECS is read in __init__ and threaded through."""

    def test_default_buffer_secs(self) -> None:
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        import os

        os.environ.pop("DALSTON_RNNT_BUFFER_SECS", None)
        engine = module.NemoRealtimeEngine(core=mock_core)

        assert engine._rnnt_buffer_secs == pytest.approx(4.0)

    def test_custom_buffer_secs(self) -> None:
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()

        with patch.dict("os.environ", {"DALSTON_RNNT_BUFFER_SECS": "6.5"}):
            engine = module.NemoRealtimeEngine(core=mock_core)

        assert engine._rnnt_buffer_secs == pytest.approx(6.5)

    def test_buffer_secs_passed_to_core_transcribe_streaming(self) -> None:
        """transcribe_streaming forwards _rnnt_buffer_secs to core."""
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()
        mock_core.decoder_type.return_value = "rnnt"
        mock_core.transcribe_streaming.return_value = iter([])

        with patch.dict("os.environ", {"DALSTON_RNNT_BUFFER_SECS": "5.0"}):
            engine = module.NemoRealtimeEngine(core=mock_core)

        list(
            engine.transcribe_streaming(
                iter([np.zeros(1600, dtype=np.float32)]),
                "en",
                "nemotron-streaming-rnnt-0.6b",
            )
        )

        _, kwargs = mock_core.transcribe_streaming.call_args
        assert kwargs.get("buffer_secs") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Regression: offline RNNT tests from M71 remain valid
# ---------------------------------------------------------------------------


class TestOfflineRNNTUnchanged:
    """Offline Parakeet RNNT uses VAD-accumulate (get_streaming_decode_fn=None)."""

    def test_get_streaming_decode_fn_parakeet_rnnt_still_none(self) -> None:
        module = _load_rt_engine_module()
        mock_core = _make_mock_core()
        mock_core.is_cache_aware_streaming.return_value = False

        engine = module.NemoRealtimeEngine(core=mock_core)
        fn = engine.get_streaming_decode_fn("parakeet-rnnt-1.1b")
        assert fn is None

    def test_transcribe_streaming_offline_rnnt_still_routes_to_run_streaming_inference(
        self,
    ) -> None:
        core = object.__new__(NemoInference)
        core._manager = MagicMock()
        core._manager.get_architecture.return_value = "rnnt"
        core._manager.is_cache_aware_streaming.return_value = False

        with (
            patch.object(
                NemoInference, "_run_streaming_inference", return_value=iter([])
            ) as mock_rsi,
            patch.object(
                NemoInference, "_run_cache_aware_streaming", return_value=iter([])
            ) as mock_cas,
        ):
            list(
                core.transcribe_streaming(
                    iter([np.zeros(1600, dtype=np.float32)]),
                    "parakeet-rnnt-1.1b",
                )
            )

        mock_rsi.assert_called_once()
        mock_cas.assert_not_called()
