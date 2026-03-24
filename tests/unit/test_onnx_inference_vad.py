"""Unit tests for OnnxInference VAD segmentation path.

Tests the VAD-based long audio transcription in OnnxInference:
- File paths route through VAD, numpy arrays go direct
- VAD segments are parsed with correct absolute timestamps
- Token timestamps are offset by segment start time
- Empty/silent segments are filtered
- VAD is lazy-loaded and reused

Run with: pytest tests/unit/test_onnx_inference_vad.py
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.engine_sdk.inference.onnx_inference import (
    OnnxInference,
    OnnxTranscriptionResult,
)

# ---------------------------------------------------------------------------
# Fake onnx-asr types to simulate VAD + timestamped results
# ---------------------------------------------------------------------------


@dataclass
class FakeTimestampedSegmentResult:
    """Mimics onnx_asr.vad.TimestampedSegmentResult."""

    start: float
    end: float
    text: str
    timestamps: list[float] | None = None
    tokens: list[str] | None = None


@dataclass
class FakeTimestampedResult:
    """Mimics onnx_asr.asr.TimestampedResult."""

    text: str
    timestamps: list[float] | None = None
    tokens: list[str] | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def inference():
    """Create an OnnxInference with a mocked manager (no real model loading)."""
    with patch.object(OnnxInference, "__init__", lambda self: None):
        core = object.__new__(OnnxInference)
        core._manager = MagicMock()
        core._device = "cpu"
        core._quantization = "none"
        core._vad = None
        core._current_model_id = None
        return core


# ---------------------------------------------------------------------------
# Routing: file paths use VAD, numpy arrays go direct
# ---------------------------------------------------------------------------


class TestTranscribeRouting:
    def test_file_path_routes_to_vad(self, inference):
        """File path input should use _transcribe_with_vad."""
        model = MagicMock()

        with patch.object(inference, "_transcribe_with_vad") as mock_vad:
            mock_vad.return_value = OnnxTranscriptionResult(text="hello")
            result = inference.transcribe_with_model(model, "/path/to/audio.wav")

        mock_vad.assert_called_once_with(
            model, "/path/to/audio.wav", vad_batch_size=None
        )
        assert result.text == "hello"

    def test_numpy_array_routes_to_direct(self, inference):
        """Numpy array input should use _transcribe_direct."""
        model = MagicMock()
        audio = np.zeros(16000, dtype=np.float32)

        with patch.object(inference, "_transcribe_direct") as mock_direct:
            mock_direct.return_value = OnnxTranscriptionResult(text="hello")
            result = inference.transcribe_with_model(model, audio)

        mock_direct.assert_called_once()
        assert result.text == "hello"


# ---------------------------------------------------------------------------
# VAD result parsing
# ---------------------------------------------------------------------------


class TestParseVadResult:
    def test_single_segment(self, inference):
        """Single VAD segment produces one output segment."""
        vad_segments = [
            FakeTimestampedSegmentResult(
                start=0.0,
                end=5.0,
                text="Hello world.",
                tokens=[" Hello", " world."],
                timestamps=[0.0, 0.3],
            ),
        ]

        result = inference._parse_vad_result(vad_segments)

        assert result.text == "Hello world."
        assert len(result.segments) == 1
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 5.0

    def test_multiple_segments_with_offset_timestamps(self, inference):
        """Multiple VAD segments get correct absolute word timestamps."""
        vad_segments = [
            FakeTimestampedSegmentResult(
                start=0.0,
                end=3.0,
                text="First sentence.",
                tokens=[" First", " sentence."],
                timestamps=[0.0, 0.5],
            ),
            FakeTimestampedSegmentResult(
                start=10.0,
                end=14.0,
                text="Second sentence.",
                tokens=[" Second", " sentence."],
                timestamps=[0.0, 0.6],
            ),
        ]

        result = inference._parse_vad_result(vad_segments)

        assert result.text == "First sentence. Second sentence."
        assert len(result.segments) == 2

        # First segment: words at absolute time 0.0+
        seg1 = result.segments[0]
        assert seg1.start == 0.0
        assert seg1.end == 3.0
        assert len(seg1.words) == 2
        assert seg1.words[0].start == 0.0  # 0.0 + 0.0
        assert seg1.words[1].start == 0.5  # 0.0 + 0.5

        # Second segment: words offset by segment start (10.0)
        seg2 = result.segments[1]
        assert seg2.start == 10.0
        assert seg2.end == 14.0
        assert len(seg2.words) == 2
        assert seg2.words[0].start == 10.0  # 10.0 + 0.0
        assert seg2.words[1].start == 10.6  # 10.0 + 0.6

    def test_empty_segments_filtered(self, inference):
        """VAD segments with empty text are skipped."""
        vad_segments = [
            FakeTimestampedSegmentResult(start=0.0, end=1.0, text=""),
            FakeTimestampedSegmentResult(
                start=2.0,
                end=5.0,
                text="Real speech.",
                tokens=[" Real", " speech."],
                timestamps=[0.0, 0.3],
            ),
            FakeTimestampedSegmentResult(start=6.0, end=7.0, text="  "),
        ]

        result = inference._parse_vad_result(vad_segments)

        assert result.text == "Real speech."
        assert len(result.segments) == 1
        assert result.segments[0].start == 2.0

    def test_segments_without_tokens(self, inference):
        """VAD segments without token data produce segments with no words."""
        vad_segments = [
            FakeTimestampedSegmentResult(
                start=5.0,
                end=10.0,
                text="No tokens here.",
                tokens=None,
                timestamps=None,
            ),
        ]

        result = inference._parse_vad_result(vad_segments)

        assert result.text == "No tokens here."
        assert len(result.segments) == 1
        assert result.segments[0].words == []

    def test_empty_iterator(self, inference):
        """Empty VAD result produces empty transcription."""
        result = inference._parse_vad_result(iter([]))

        assert result.text == ""
        assert result.segments == []

    def test_many_segments_simulating_long_audio(self, inference):
        """Simulate a long recording split into many VAD segments."""
        vad_segments = []
        for i in range(50):
            start = i * 30.0
            end = start + 25.0
            vad_segments.append(
                FakeTimestampedSegmentResult(
                    start=start,
                    end=end,
                    text=f"Segment {i}.",
                    tokens=[" Segment", f" {i}."],
                    timestamps=[0.0, 0.5],
                )
            )

        result = inference._parse_vad_result(vad_segments)

        assert len(result.segments) == 50

        # Last segment should have absolute timestamps
        last = result.segments[-1]
        assert last.start == 49 * 30.0
        assert last.words[0].start == 49 * 30.0  # offset applied
        assert last.words[1].start == 49 * 30.0 + 0.5


# ---------------------------------------------------------------------------
# VAD lazy loading
# ---------------------------------------------------------------------------


class TestVadLifecycle:
    def test_vad_lazy_loaded_on_first_call(self, inference):
        """VAD model is not loaded until first file transcription."""
        assert inference._vad is None

        mock_vad = MagicMock()
        mock_load_vad = MagicMock(return_value=mock_vad)

        with patch.dict(
            "sys.modules",
            {"onnx_asr": MagicMock(load_vad=mock_load_vad)},
        ):
            # Patch the import target so _get_or_load_vad's
            # `from onnx_asr import load_vad` resolves to our mock
            with patch(
                "dalston.engine_sdk.inference.onnx_inference.load_vad",
                mock_load_vad,
                create=True,
            ):
                vad = inference._get_or_load_vad()

        mock_load_vad.assert_called_once_with("silero")
        assert vad is mock_vad
        assert inference._vad is mock_vad

    def test_shutdown_clears_vad(self, inference):
        """Shutdown should release the VAD model."""
        inference._vad = MagicMock()
        inference.shutdown()

        assert inference._vad is None

    def test_vad_reused_across_calls(self, inference):
        """VAD model is loaded once and reused."""
        fake_vad = MagicMock()
        inference._vad = fake_vad

        vad1 = inference._get_or_load_vad()
        vad2 = inference._get_or_load_vad()

        assert vad1 is vad2 is fake_vad


# ---------------------------------------------------------------------------
# Environment variable override
# ---------------------------------------------------------------------------


class TestVadConfiguration:
    def test_max_speech_duration_from_env(self, inference):
        """DALSTON_VAD_MAX_SPEECH_S overrides the default."""
        model = MagicMock()
        mock_vad = MagicMock()
        inference._vad = mock_vad

        # with_vad returns a mock that has with_timestamps
        mock_vad_adapter = MagicMock()
        mock_ts_adapter = MagicMock()
        mock_ts_adapter.recognize.return_value = iter([])
        mock_vad_adapter.with_timestamps.return_value = mock_ts_adapter
        model.with_vad.return_value = mock_vad_adapter

        with patch.dict("os.environ", {"DALSTON_VAD_MAX_SPEECH_S": "120"}):
            inference._transcribe_with_vad(model, "/path/audio.wav")

        model.with_vad.assert_called_once()
        call_kwargs = model.with_vad.call_args
        assert call_kwargs.kwargs["max_speech_duration_s"] == 120.0

    def test_default_max_speech_duration(self, inference):
        """Without env var, uses 60s default."""
        model = MagicMock()
        mock_vad = MagicMock()
        inference._vad = mock_vad

        mock_vad_adapter = MagicMock()
        mock_ts_adapter = MagicMock()
        mock_ts_adapter.recognize.return_value = iter([])
        mock_vad_adapter.with_timestamps.return_value = mock_ts_adapter
        model.with_vad.return_value = mock_vad_adapter

        with patch.dict("os.environ", {}, clear=False):
            # Ensure the env var is not set
            import os

            os.environ.pop("DALSTON_VAD_MAX_SPEECH_S", None)
            inference._transcribe_with_vad(model, "/path/audio.wav")

        call_kwargs = model.with_vad.call_args
        assert call_kwargs.kwargs["max_speech_duration_s"] == 60.0
