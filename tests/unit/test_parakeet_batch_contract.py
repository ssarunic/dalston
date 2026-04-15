"""Contract tests for parakeet (NeMo) batch engine.

Verifies that the batch engine produces the correct output shape
(Transcript with segments, text, language) and that word
timestamp behavior is preserved after delegation to NemoInference.

These tests mock the NeMo model to avoid GPU/model dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from dalston.engine_sdk import TaskRequest
from dalston.engine_sdk.context import BatchTaskContext


def _ctx(task_id: str, job_id: str) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="transcribe",
    )


def _load_parakeet_engine_class():
    """Load NemoBatchEngine class from file to avoid import path issues."""
    engine_path = Path("engines/stt-transcribe/nemo/batch_engine.py")
    spec = importlib.util.spec_from_file_location("m63_parakeet_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_parakeet_engine"] = module
    spec.loader.exec_module(module)
    return module.NemoBatchEngine


# Load once at module level to avoid re-importing native C extensions.
# Guard: torch is required by the engine but not available in CI.
torch = pytest.importorskip("torch")
_ParakeetEngine = _load_parakeet_engine_class()


def _make_tdt_hypothesis(
    text: str = "hello world",
    word_timestamps: list[dict] | None = None,
    segment_timestamps: list[dict] | None = None,
) -> SimpleNamespace:
    """Create a mock NeMo TDT hypothesis with timestep dict."""
    if word_timestamps is None:
        word_timestamps = [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
    timestep = {"word": word_timestamps, "segment": segment_timestamps or []}
    return SimpleNamespace(text=text, timestep=timestep)


def _make_rnnt_hypothesis(
    text: str = "hello world",
    frame_indices: list[int] | None = None,
) -> SimpleNamespace:
    """Create a mock NeMo RNNT hypothesis with frame index list."""
    if frame_indices is None:
        frame_indices = [0, 50]
    return SimpleNamespace(text=text, timestep=frame_indices)


def _build_engine_with_mock_core(hypothesis):
    """Create a NemoBatchEngine with a mocked NemoInference."""
    mock_core = MagicMock()
    mock_core.device = "cpu"

    # Mock the manager for acquire/release
    mock_model = MagicMock()
    mock_model.transcribe.return_value = [[hypothesis]]
    mock_core.manager.acquire.return_value = mock_model
    mock_core.manager.release = MagicMock()

    # Mock core.transcribe to call transcribe_with_model with the mock model
    from dalston.engine_sdk.inference.nemo_inference import NemoInference

    # Use a real NemoInference._parse_hypothesis for result
    segments, words = NemoInference._parse_hypothesis(hypothesis, hypothesis.text)
    from dalston.engine_sdk.inference.nemo_inference import NeMoTranscriptionResult

    result = NeMoTranscriptionResult(
        text=hypothesis.text.strip(),
        segments=segments,
        language="en",
        language_probability=1.0,
    )
    mock_core.transcribe.return_value = result
    mock_core.transcribe_with_model.return_value = result
    mock_core.get_stats.return_value = {}

    with patch.object(_ParakeetEngine, "__init__", lambda self, core=None: None):
        engine = _ParakeetEngine.__new__(_ParakeetEngine)
        engine._core = mock_core
        engine._default_model_id = "nvidia/parakeet-tdt-1.1b"
        engine.engine_id = "nemo"
        engine.logger = MagicMock()
    return engine


class TestParakeetBatchOutputShape:
    """Verify Transcript structure from parakeet batch engine."""

    def test_output_has_text_and_language(self) -> None:
        hypothesis = _make_tdt_hypothesis(text="hello world")
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        data = result.data
        assert data.text == "hello world"
        assert data.language == "en"
        assert data.language_confidence == 1.0
        assert len(data.segments) >= 1
        assert data.engine_id == "nemo"

    def test_tdt_word_timestamps(self) -> None:
        hypothesis = _make_tdt_hypothesis(
            text="hello world",
            word_timestamps=[
                {"word": "hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
            ],
        )
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.words is not None
        assert len(seg.words) == 2
        assert seg.words[0].text == "hello"
        assert seg.words[0].start == 0.0
        assert seg.words[1].text == "world"

    def test_rnnt_frame_timestamps(self) -> None:
        hypothesis = _make_rnnt_hypothesis(
            text="hello world",
            frame_indices=[0, 50],
        )
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.words is not None
        assert len(seg.words) == 2
        # Frame indices * 0.01 = seconds
        assert seg.words[0].start == 0.0
        assert seg.words[1].start == 0.5

    def test_no_timestamps_fallback(self) -> None:
        hypothesis = SimpleNamespace(text="hello world", timestep=None)
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert len(result.data.segments) == 1
        assert result.data.segments[0].text == "hello world"
        assert (
            result.data.segments[0].words is None or result.data.segments[0].words == []
        )


class TestParakeetBatchAlignmentMethod:
    """Verify alignment method reporting based on decoder type."""

    def test_ctc_alignment(self) -> None:
        hypothesis = _make_tdt_hypothesis()
        engine = _build_engine_with_mock_core(hypothesis)
        engine._default_model_id = "nvidia/parakeet-ctc-0.6b"

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"loaded_model_id": "nvidia/parakeet-ctc-0.6b"},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.alignment_method.value == "ctc"

    def test_tdt_alignment(self) -> None:
        hypothesis = _make_tdt_hypothesis()
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"loaded_model_id": "nvidia/parakeet-tdt-1.1b"},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.alignment_method.value == "tdt"


class TestParakeetBatchTimestampGranularity:
    """Verify timestamp granularity reporting."""

    def test_word_granularity_when_words_present(self) -> None:
        hypothesis = _make_tdt_hypothesis()
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "word"

    def test_segment_granularity_when_no_words(self) -> None:
        hypothesis = SimpleNamespace(text="hello world", timestep=None)
        engine = _build_engine_with_mock_core(hypothesis)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "segment"


class TestTranscribeBatchWithModel:
    """Verify NemoInference.transcribe_batch_with_model list-in/list-out contract.

    Step 86.0 — the original implementation only parsed transcriptions[0],
    silently discarding results for batch inputs with N > 1. These tests
    guard that a list of N inputs yields N independent results in order.
    """

    def _make_hyp(
        self, text: str, words: list[tuple[str, float, float]]
    ) -> SimpleNamespace:
        ts = {
            "word": [{"word": w, "start": s, "end": e} for w, s, e in words],
            "segment": [
                {
                    "segment": text,
                    "start": words[0][1] if words else 0.0,
                    "end": words[-1][2] if words else 0.0,
                }
            ],
        }
        return SimpleNamespace(text=text, timestamp=ts)

    def _make_core(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        from dalston.engine_sdk.inference.nemo_inference import NemoInference

        # Stub torch.amp.autocast to be a no-op context manager so we don't
        # need a CUDA device for the test.
        class _NoopCtx:
            def __enter__(self) -> None:
                return None

            def __exit__(self, *args: object) -> None:
                return None

        # NemoInference uses `torch.amp.autocast("cuda")` on CUDA and
        # `torch.inference_mode()` on CPU. For CPU device (test default),
        # inference_mode already works — no monkeypatch needed.

        core = NemoInference.__new__(NemoInference)
        core._manager = MagicMock()
        core._current_model_id = "parakeet-tdt-0.6b-v3"
        core._device = "cpu"  # avoid CUDA autocast branch
        # Need the real device property — patch the underlying manager
        core._manager.device = "cpu"
        return core

    def test_transcribe_batch_returns_one_result_per_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A list of 3 inputs yields 3 independent NeMoTranscriptionResult objects."""
        core = self._make_core(monkeypatch)

        hyp_a = self._make_hyp(
            "hello alpha", [("hello", 0.0, 0.5), ("alpha", 0.5, 1.0)]
        )
        hyp_b = self._make_hyp("hello beta", [("hello", 0.0, 0.5), ("beta", 0.5, 1.0)])
        hyp_c = self._make_hyp(
            "hello gamma", [("hello", 0.0, 0.5), ("gamma", 0.5, 1.0)]
        )

        mock_model = MagicMock()
        # NeMo returns a list matching the input list — one entry per input.
        mock_model.transcribe.return_value = [hyp_a, hyp_b, hyp_c]

        results = core.transcribe_batch_with_model(
            mock_model,
            ["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"],
            batch_size=3,
        )

        assert len(results) == 3
        assert results[0].text == "hello alpha"
        assert results[1].text == "hello beta"
        assert results[2].text == "hello gamma"

        # NeMo was called with the full batch, not just [0]
        call_args = mock_model.transcribe.call_args
        assert len(call_args.args[0]) == 3
        assert call_args.kwargs["batch_size"] == 3

    def test_transcribe_batch_handles_nested_hypothesis_lists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NeMo with multiple decoding strategies wraps hypotheses in an extra list."""
        core = self._make_core(monkeypatch)

        hyp_a = self._make_hyp("a", [("a", 0.0, 0.1)])
        hyp_b = self._make_hyp("b", [("b", 0.0, 0.1)])

        mock_model = MagicMock()
        # Nested: one list per input, each with one hypothesis.
        mock_model.transcribe.return_value = [[hyp_a], [hyp_b]]

        results = core.transcribe_batch_with_model(
            mock_model, ["/tmp/a.wav", "/tmp/b.wav"], batch_size=2
        )

        assert len(results) == 2
        assert results[0].text == "a"
        assert results[1].text == "b"

    def test_transcribe_batch_empty_input_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty input → empty output, no model invocation."""
        core = self._make_core(monkeypatch)
        mock_model = MagicMock()

        results = core.transcribe_batch_with_model(mock_model, [], batch_size=1)

        assert results == []
        mock_model.transcribe.assert_not_called()

    def test_transcribe_with_model_single_delegates_to_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single-input wrapper unwraps the list result correctly."""
        core = self._make_core(monkeypatch)

        hyp = self._make_hyp("only one", [("only", 0.0, 0.3), ("one", 0.3, 0.6)])
        mock_model = MagicMock()
        mock_model.transcribe.return_value = [hyp]

        result = core.transcribe_with_model(mock_model, "/tmp/single.wav")

        assert result.text == "only one"
        # Confirm the single input was wrapped in a list when passed to NeMo.
        call_args = mock_model.transcribe.call_args
        assert len(call_args.args[0]) == 1
