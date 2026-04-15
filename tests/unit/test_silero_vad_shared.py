"""Unit tests for the shared Silero VAD module.

Covers the state-machine behaviour of :class:`SileroOnnxModel` — the
parts that actually have logic beyond "call onnxruntime" — using a fake
ORT session so the tests run without the real model file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from dalston.engine_sdk.silero_vad import (
    CONTEXT_SAMPLES_8K,
    CONTEXT_SAMPLES_16K,
    WINDOW_SAMPLES_8K,
    WINDOW_SAMPLES_16K,
    SileroOnnxModel,
    get_silero_onnx_path,
    load_silero_session,
)


class _FakeOrtSession:
    """Minimal stand-in for ``onnxruntime.InferenceSession``.

    Records every ``run`` call so tests can assert on the shapes that
    the wrapper passes through. Returns a monotonically increasing
    "probability" so tests can distinguish successive calls.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, np.ndarray]] = []
        self._counter = 0

    def run(
        self,
        output_names: list[str] | None,
        inputs: dict[str, np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray]:
        self.calls.append({k: np.asarray(v).copy() for k, v in inputs.items()})
        self._counter += 1
        prob = min(0.99, 0.1 * self._counter)
        new_state = np.zeros_like(inputs["state"])
        return np.array([[prob]], dtype=np.float32), new_state


class TestWindowSizeConstants:
    def test_16k_constants(self) -> None:
        assert WINDOW_SAMPLES_16K == 512
        assert CONTEXT_SAMPLES_16K == 64

    def test_8k_constants(self) -> None:
        assert WINDOW_SAMPLES_8K == 256
        assert CONTEXT_SAMPLES_8K == 32


class TestSileroOnnxModelInference:
    def test_first_call_initialises_context(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        wav = np.random.rand(WINDOW_SAMPLES_16K).astype(np.float32)
        prob = model(wav, 16000)

        assert 0.0 <= prob <= 1.0
        assert len(session.calls) == 1
        call = session.calls[0]
        # Silero v5 requires context + frame concat as the "input" tensor.
        assert call["input"].shape == (1, CONTEXT_SAMPLES_16K + WINDOW_SAMPLES_16K)
        # Initial context is zeros.
        np.testing.assert_array_equal(call["input"][:, :CONTEXT_SAMPLES_16K], 0.0)
        # Initial state is zeros of the documented shape.
        assert call["state"].shape == (2, 1, 128)
        assert call["sr"].item() == 16000

    def test_context_carries_over_between_calls(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        wav1 = np.full(WINDOW_SAMPLES_16K, 0.25, dtype=np.float32)
        wav2 = np.full(WINDOW_SAMPLES_16K, 0.75, dtype=np.float32)

        model(wav1, 16000)
        model(wav2, 16000)

        # Second call's context should be the last CONTEXT_SAMPLES_16K
        # samples of the first call's (context + wav1) concatenation —
        # which is all 0.25 because the full wav1 is larger than the
        # context window.
        second = session.calls[1]["input"]
        np.testing.assert_allclose(second[:, :CONTEXT_SAMPLES_16K], 0.25, atol=0)

    def test_8k_uses_smaller_context(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        wav = np.zeros(WINDOW_SAMPLES_8K, dtype=np.float32)
        model(wav, 8000)

        call = session.calls[0]
        assert call["input"].shape == (1, CONTEXT_SAMPLES_8K + WINDOW_SAMPLES_8K)
        assert call["sr"].item() == 8000

    def test_sample_rate_change_triggers_reset(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        model(np.full(WINDOW_SAMPLES_16K, 0.5, dtype=np.float32), 16000)
        # Second call at a different rate should clear context so the
        # new stream isn't contaminated with 16 kHz audio.
        model(np.zeros(WINDOW_SAMPLES_8K, dtype=np.float32), 8000)

        second = session.calls[1]["input"]
        np.testing.assert_array_equal(second[:, :CONTEXT_SAMPLES_8K], 0.0)

    def test_explicit_reset_clears_context(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        model(np.full(WINDOW_SAMPLES_16K, 0.9, dtype=np.float32), 16000)
        model.reset_states()
        model(np.zeros(WINDOW_SAMPLES_16K, dtype=np.float32), 16000)

        # After reset_states, the next call must see a zero context —
        # the same initial state as the very first call.
        after_reset = session.calls[1]["input"]
        np.testing.assert_array_equal(after_reset[:, :CONTEXT_SAMPLES_16K], 0.0)

    def test_1d_input_is_batched(self) -> None:
        session = _FakeOrtSession()
        model = SileroOnnxModel(session)

        wav_1d = np.random.rand(WINDOW_SAMPLES_16K).astype(np.float32)
        model(wav_1d, 16000)

        call = session.calls[0]
        # Wrapper should have added the batch axis.
        assert call["input"].ndim == 2
        assert call["input"].shape[0] == 1


class TestGetSileroOnnxPath:
    def test_env_override_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        baked = tmp_path / "silero.onnx"
        baked.write_bytes(b"")
        monkeypatch.setenv("DALSTON_SILERO_VAD_ONNX", str(baked))

        assert get_silero_onnx_path() == baked

    def test_env_override_missing_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DALSTON_SILERO_VAD_ONNX", str(tmp_path / "nope.onnx"))
        with pytest.raises(RuntimeError, match="does not exist"):
            get_silero_onnx_path()

    def test_cached_file_short_circuits_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DALSTON_SILERO_VAD_ONNX", raising=False)
        monkeypatch.setenv("DALSTON_MODEL_CACHE", str(tmp_path))
        cache_file = tmp_path / "silero_vad.onnx"
        cache_file.write_bytes(b"")

        with patch("urllib.request.urlretrieve") as mock_urlretrieve:
            result = get_silero_onnx_path()

        assert result == cache_file
        mock_urlretrieve.assert_not_called()


class TestLoadSileroSession:
    def test_uses_explicit_path_when_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        baked = tmp_path / "silero.onnx"
        baked.write_bytes(b"")

        from unittest.mock import MagicMock

        fake_session = MagicMock(name="ort.InferenceSession.instance")

        class _FakeOrt:
            InferenceSession = MagicMock(return_value=fake_session)

        with patch.dict("sys.modules", {"onnxruntime": _FakeOrt}):
            result = load_silero_session(baked)

        assert result is fake_session
        _FakeOrt.InferenceSession.assert_called_once()
        call_args = _FakeOrt.InferenceSession.call_args
        assert call_args.args[0] == str(baked)
        assert call_args.kwargs["providers"] == ["CPUExecutionProvider"]

    def test_missing_onnxruntime_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        baked = tmp_path / "silero.onnx"
        baked.write_bytes(b"")

        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def _fake_import(name: str, *args, **kwargs):
            if name == "onnxruntime":
                raise ImportError("onnxruntime not installed in this test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with pytest.raises(RuntimeError, match="onnxruntime is required"):
                load_silero_session(baked)
