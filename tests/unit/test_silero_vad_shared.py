"""Unit tests for the shared Silero VAD module.

Covers the state-machine behaviour of :class:`SileroOnnxModel` — the
parts that actually have logic beyond "call onnxruntime" — using a fake
ORT session so the tests run without the real model file.
"""

from __future__ import annotations

import builtins
import re
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from dalston.engine_sdk.silero_vad import (
    _SILERO_VAD_ONNX_URL,
    _SILERO_VAD_VERSION,
    CONTEXT_SAMPLES_8K,
    CONTEXT_SAMPLES_16K,
    WINDOW_SAMPLES_8K,
    WINDOW_SAMPLES_16K,
    SileroOnnxModel,
    SileroTorchModel,
    get_silero_onnx_path,
    load_silero_model,
    load_silero_session,
)


def _block_import(*names: str):
    """Patch ``__import__`` so importing any of ``names`` raises ImportError.

    The VAD loaders choose a backend by attempting imports, so simulating
    a missing runtime is the only way to exercise the fallback paths on a
    machine that has neither torch nor onnxruntime installed.
    """
    real_import = builtins.__import__

    def _fake_import(name: str, *args, **kwargs):
        if name in names:
            raise ImportError(f"{name} not installed in this test")
        return real_import(name, *args, **kwargs)

    return patch("builtins.__import__", side_effect=_fake_import)


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
        # The cache filename carries the release, so only a matching-version
        # entry may short-circuit the download (see M100.6).
        cache_file = tmp_path / f"silero_vad-{_SILERO_VAD_VERSION}.onnx"
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

        with _block_import("onnxruntime"):
            with pytest.raises(RuntimeError, match="onnxruntime is required"):
                load_silero_session(baked)


class _FakeTensor:
    """Stand-in for the tensor the TorchScript module returns.

    Deliberately does **not** implement ``__float__``. Silero's module
    returns a grad-tracking tensor, and calling ``float()`` on one emits
    ``UserWarning: Converting a tensor with requires_grad=True to a
    scalar``. Omitting ``__float__`` here means the wrapper must go
    through ``.item()`` or these tests fail with ``TypeError``.
    """

    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class _NoGradTracker:
    """Tracks whether code is currently inside a ``torch.no_grad()`` block."""

    def __init__(self) -> None:
        self.depth = 0
        self.entered = 0


class _FakeNoGrad:
    """Context manager stand-in for ``torch.no_grad()``."""

    def __init__(self, tracker: _NoGradTracker) -> None:
        self._tracker = tracker

    def __enter__(self) -> _FakeNoGrad:
        self._tracker.depth += 1
        self._tracker.entered += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        self._tracker.depth -= 1
        return False


class _FakeSileroModule:
    """Stand-in for the TorchScript module from ``load_silero_vad()``.

    Records the no-grad depth at call time so tests can assert inference
    actually ran inside ``torch.no_grad()``. The real module is recurrent
    and stateful, so running it with autograd live accumulates graph
    history across a realtime stream.
    """

    def __init__(
        self, prob: float = 0.42, tracker: _NoGradTracker | None = None
    ) -> None:
        self.calls: list[tuple[object, int]] = []
        self.reset_count = 0
        self.no_grad_depth_at_call: list[int] = []
        self._prob = prob
        self._tracker = tracker

    def __call__(self, tensor: object, sample_rate: int) -> _FakeTensor:
        self.calls.append((tensor, sample_rate))
        if self._tracker is not None:
            self.no_grad_depth_at_call.append(self._tracker.depth)
        return _FakeTensor(self._prob)

    def reset_states(self) -> None:
        self.reset_count += 1


def _make_fake_torch(tracker: _NoGradTracker | None = None):
    """Build a minimal ``torch`` stand-in for :class:`SileroTorchModel`."""
    resolved = tracker if tracker is not None else _NoGradTracker()

    class _FakeTorch:
        @staticmethod
        def from_numpy(array: np.ndarray) -> np.ndarray:
            return array

        @staticmethod
        def no_grad() -> _FakeNoGrad:
            return _FakeNoGrad(resolved)

    return _FakeTorch


#: Default stand-in for tests that don't care about no-grad bookkeeping.
_FakeTorch = _make_fake_torch()


def _patch_torch_backend(module: _FakeSileroModule):
    """Patch ``torch`` + ``silero_vad`` into sys.modules for the torch path."""
    fake_pkg = type(
        "_FakeSileroVadPkg", (), {"load_silero_vad": staticmethod(lambda: module)}
    )
    return patch.dict("sys.modules", {"torch": _FakeTorch, "silero_vad": fake_pkg})


class TestSileroTorchModel:
    def test_returns_plain_float_probability(self) -> None:
        module = _FakeSileroModule(prob=0.75)
        model = SileroTorchModel(module)

        with patch.dict("sys.modules", {"torch": _FakeTorch}):
            prob = model(np.zeros(WINDOW_SAMPLES_16K, dtype=np.float32), 16000)

        assert isinstance(prob, float)
        assert prob == pytest.approx(0.75)
        assert module.calls[0][1] == 16000

    def test_accepts_singleton_batch_for_onnx_parity(self) -> None:
        """The ONNX wrapper accepts (batch, window); collapse batch of 1."""
        module = _FakeSileroModule()
        model = SileroTorchModel(module)
        audio = np.zeros((1, WINDOW_SAMPLES_16K), dtype=np.float32)

        with patch.dict("sys.modules", {"torch": _FakeTorch}):
            prob = model(audio, 16000)

        assert isinstance(prob, float)
        # Must be flattened to 1-D, not passed through as (1, N).
        assert np.asarray(module.calls[0][0]).ndim == 1

    def test_rejects_real_batches_rather_than_scoring_first_row(self) -> None:
        module = _FakeSileroModule()
        model = SileroTorchModel(module)
        audio = np.zeros((3, WINDOW_SAMPLES_16K), dtype=np.float32)

        with patch.dict("sys.modules", {"torch": _FakeTorch}):
            with pytest.raises(ValueError, match="batch size 1"):
                model(audio, 16000)

    def test_supports_8k_window(self) -> None:
        module = _FakeSileroModule()
        model = SileroTorchModel(module)

        with patch.dict("sys.modules", {"torch": _FakeTorch}):
            model(np.zeros(WINDOW_SAMPLES_8K, dtype=np.float32), 8000)

        assert module.calls[0][1] == 8000

    def test_inference_runs_inside_no_grad(self) -> None:
        """Autograd must be off: the module is recurrent, so a live graph
        would be retained through carried state for the whole stream."""
        tracker = _NoGradTracker()
        module = _FakeSileroModule(tracker=tracker)
        model = SileroTorchModel(module)
        fake_torch = _make_fake_torch(tracker)

        with patch.dict("sys.modules", {"torch": fake_torch}):
            model(np.zeros(WINDOW_SAMPLES_16K, dtype=np.float32), 16000)

        assert tracker.entered == 1, "torch.no_grad() was never entered"
        assert module.no_grad_depth_at_call == [1], (
            "model was invoked outside the no_grad block"
        )
        # And the block must be exited again, not leaked.
        assert tracker.depth == 0

    def test_reset_states_delegates_to_module(self) -> None:
        module = _FakeSileroModule()
        model = SileroTorchModel(module)

        model.reset_states()

        assert module.reset_count == 1

    def test_reset_states_ignores_batch_size_without_raising(self) -> None:
        """batch_size exists for ONNX parity; it cannot be honoured here."""
        module = _FakeSileroModule()
        model = SileroTorchModel(module)

        model.reset_states(batch_size=4)

        assert module.reset_count == 1


class TestLoadSileroModel:
    def test_prefers_torch_when_available(self) -> None:
        module = _FakeSileroModule()

        with _patch_torch_backend(module):
            model = load_silero_model()

        assert isinstance(model, SileroTorchModel)

    @pytest.mark.parametrize(
        "blocked",
        [
            # silero_vad absent: torch-based image without the VAD package.
            "silero_vad",
            # torch absent: base-onnx / base-engine ship onnxruntime only.
            "torch",
        ],
    )
    def test_falls_back_to_onnx_when_torch_path_unavailable(self, blocked: str) -> None:
        fake_session = _FakeOrtSession()

        with _block_import(blocked):
            with patch(
                "dalston.engine_sdk.silero_vad.load_silero_session",
                return_value=fake_session,
            ):
                model = load_silero_model()

        assert isinstance(model, SileroOnnxModel)

    def test_falls_back_to_onnx_when_torch_model_load_fails(self) -> None:
        """Package present but broken is logged, then ONNX is tried."""
        fake_session = _FakeOrtSession()

        def _boom() -> None:
            raise RuntimeError("corrupt weights")

        fake_pkg = type(
            "_BrokenSileroVadPkg", (), {"load_silero_vad": staticmethod(_boom)}
        )

        with patch.dict("sys.modules", {"torch": _FakeTorch, "silero_vad": fake_pkg}):
            with patch(
                "dalston.engine_sdk.silero_vad.load_silero_session",
                return_value=fake_session,
            ):
                model = load_silero_model()

        assert isinstance(model, SileroOnnxModel)

    def test_raises_naming_both_backends_when_neither_available(self) -> None:
        with _block_import("torch", "silero_vad", "onnxruntime"):
            with pytest.raises(RuntimeError) as exc_info:
                load_silero_model()

        message = str(exc_info.value)
        # Operators need to know both remedies, not just whichever was tried last.
        assert "silero-vad" in message
        assert "onnxruntime" in message


class TestBackendInterfaceParity:
    """Both backends must satisfy the SileroModel protocol identically."""

    def test_both_expose_the_same_call_surface(self) -> None:
        torch_model = SileroTorchModel(_FakeSileroModule())
        onnx_model = SileroOnnxModel(_FakeOrtSession())

        for model in (torch_model, onnx_model):
            assert callable(model)
            assert callable(model.reset_states)
            # reset_states must accept the batch_size kwarg on both.
            model.reset_states(batch_size=1)

    def test_both_return_float_for_the_same_input(self) -> None:
        audio = np.zeros(WINDOW_SAMPLES_16K, dtype=np.float32)

        onnx_model = SileroOnnxModel(_FakeOrtSession())
        onnx_prob = onnx_model(audio, 16000)

        torch_model = SileroTorchModel(_FakeSileroModule())
        with patch.dict("sys.modules", {"torch": _FakeTorch}):
            torch_prob = torch_model(audio, 16000)

        assert isinstance(onnx_prob, float)
        assert isinstance(torch_prob, float)


class TestSileroVersionAlignment:
    """M100.6: every Silero declaration must name one release.

    The two backends load different artifacts — the pip package's
    TorchScript weights vs the baked ONNX export — and are only the same
    model when both come from the same tag. A drifting pin or a forgotten
    URL silently changes speech probabilities and endpointing depending
    on which backend a given image happens to select, which is precisely
    the failure this milestone removed. These tests fail loudly rather
    than let it creep back on a routine dependency bump.
    """

    #: Files declaring a baked ONNX URL or a silero-vad package version.
    _SEARCH_ROOTS = ("docker", "engines")

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _declaration_files(cls) -> list[Path]:
        root = cls._repo_root()
        files: list[Path] = []
        for sub in cls._SEARCH_ROOTS:
            base = root / sub
            files.extend(p for p in base.rglob("Dockerfile*") if p.is_file())
            files.extend(p for p in base.rglob("requirements*.txt") if p.is_file())
        return files

    def test_expected_version_constant_is_a_tag(self) -> None:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", _SILERO_VAD_VERSION), (
            f"_SILERO_VAD_VERSION must be a vX.Y.Z tag, got {_SILERO_VAD_VERSION!r}"
        )

    def test_onnx_url_uses_the_version_constant(self) -> None:
        assert f"/raw/{_SILERO_VAD_VERSION}/" in _SILERO_VAD_ONNX_URL

    def test_all_baked_onnx_urls_match_the_constant(self) -> None:
        pattern = re.compile(r"silero-vad/raw/(v[\d.]+)/")
        mismatches: list[str] = []
        found = 0
        for path in self._declaration_files():
            for tag in pattern.findall(path.read_text()):
                found += 1
                if tag != _SILERO_VAD_VERSION:
                    mismatches.append(f"{path}: {tag}")

        assert found, "no baked Silero ONNX URLs found — has the layout changed?"
        assert not mismatches, (
            "baked ONNX URL(s) disagree with "
            f"_SILERO_VAD_VERSION={_SILERO_VAD_VERSION}: {mismatches}"
        )

    def test_all_package_pins_are_exact_and_match_the_constant(self) -> None:
        # >= would let a routine rebuild pull a newer release while the
        # baked ONNX stays put, recreating the mismatch.
        pattern = re.compile(r"silero-vad\s*(==|>=|>|~=)\s*([\d.]+)")
        expected = _SILERO_VAD_VERSION.lstrip("v")
        problems: list[str] = []
        found = 0
        for path in self._declaration_files():
            for operator, version in pattern.findall(path.read_text()):
                found += 1
                if operator != "==":
                    problems.append(
                        f"{path}: silero-vad{operator}{version} (not pinned)"
                    )
                elif version != expected:
                    problems.append(
                        f"{path}: silero-vad=={version} (expected {expected})"
                    )

        assert found, "no silero-vad package declarations found"
        assert not problems, f"silero-vad pin problems: {problems}"

    def test_cache_path_is_versioned(self, tmp_path: Path, monkeypatch) -> None:
        """A stale unversioned cache entry must not shadow a URL bump."""
        monkeypatch.delenv("DALSTON_SILERO_VAD_ONNX", raising=False)
        monkeypatch.setenv("DALSTON_MODEL_CACHE", str(tmp_path))

        # An old-style, unversioned cache file from a previous release.
        (tmp_path / "silero_vad.onnx").write_bytes(b"stale v5 weights")

        with patch("urllib.request.urlretrieve") as mock_urlretrieve:
            mock_urlretrieve.side_effect = lambda url, dest: Path(dest).write_bytes(
                b"fresh"
            )
            result = get_silero_onnx_path()

        # It must NOT return the stale unversioned file.
        assert result.name != "silero_vad.onnx"
        assert _SILERO_VAD_VERSION in result.name
        mock_urlretrieve.assert_called_once()
