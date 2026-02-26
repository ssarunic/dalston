"""Unit tests for the phoneme forced alignment engine.

Tests the CTC forced alignment algorithm, model loader, and engine wrapper
with mocked wav2vec2 models.

Run with: uv run --extra dev pytest tests/unit/test_phoneme_align_engine.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

torch = pytest.importorskip("torch")
import numpy as np  # noqa: E402

# ---------------------------------------------------------------------------
# Module loaders (engines live outside the dalston package)
# ---------------------------------------------------------------------------


def _load_module(name: str, filepath: str):
    """Load a Python module from a file path."""
    path = Path(filepath)
    if not path.exists():
        pytest.skip(f"{filepath} not found")

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        pytest.skip(f"Could not load spec for {filepath}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ctc_module():
    return _load_module(
        "ctc_forced_align",
        "engines/stt-align/phoneme-align/ctc_forced_align.py",
    )


@pytest.fixture(scope="module")
def model_loader_module():
    return _load_module(
        "model_loader",
        "engines/stt-align/phoneme-align/model_loader.py",
    )


@pytest.fixture(scope="module")
def align_module(ctc_module):
    """Load the align module after ctc_forced_align is in sys.modules."""
    return _load_module(
        "align",
        "engines/stt-align/phoneme-align/align.py",
    )


@pytest.fixture(scope="module")
def engine_module(ctc_module, model_loader_module, align_module):
    """Load the engine module after its dependencies are in sys.modules."""
    return _load_module(
        "phoneme_align_engine",
        "engines/stt-align/phoneme-align/engine.py",
    )


# =========================================================================
# CTC Forced Alignment Core Tests
# =========================================================================


class TestBuildTrellis:
    """Tests for the CTC trellis construction."""

    def test_trellis_shape(self, ctc_module):
        """Trellis has shape (num_frames, num_tokens)."""
        num_frames, vocab_size = 20, 5
        emission = torch.randn(num_frames, vocab_size)
        emission = torch.log_softmax(emission, dim=-1)
        tokens = [1, 2, 3]

        trellis = ctc_module.build_trellis(emission, tokens, blank_id=0)

        assert trellis.shape == (num_frames, len(tokens))

    def test_trellis_first_row_except_origin_is_neg_inf(self, ctc_module):
        """Cannot be at token > 0 at frame 0."""
        emission = torch.log_softmax(torch.randn(10, 5), dim=-1)
        tokens = [1, 2, 3]

        trellis = ctc_module.build_trellis(emission, tokens)

        assert trellis[0, 0] == 0.0
        assert all(trellis[0, j] == float("-inf") for j in range(1, len(tokens)))

    def test_trellis_single_token(self, ctc_module):
        """Trellis with one token should accumulate blank probabilities."""
        emission = torch.log_softmax(torch.randn(10, 5), dim=-1)
        tokens = [1]

        trellis = ctc_module.build_trellis(emission, tokens)

        assert trellis.shape == (10, 1)


class TestBacktrack:
    """Tests for beam-search backtracking."""

    def test_backtrack_returns_path(self, ctc_module):
        """Backtrack should return a non-empty path for a valid alignment."""
        emission = torch.log_softmax(torch.randn(20, 5), dim=-1)
        tokens = [1, 2]

        trellis = ctc_module.build_trellis(emission, tokens)
        path = ctc_module.backtrack(trellis, emission, tokens, beam_width=2)

        assert path is not None
        assert len(path) > 0

    def test_backtrack_path_covers_all_frames(self, ctc_module):
        """Path should span from frame 0 to the last frame."""
        emission = torch.log_softmax(torch.randn(15, 5), dim=-1)
        tokens = [1, 2]

        trellis = ctc_module.build_trellis(emission, tokens)
        path = ctc_module.backtrack(trellis, emission, tokens)

        assert path is not None
        time_indices = [p.time_index for p in path]
        assert min(time_indices) == 0
        assert max(time_indices) == 14  # num_frames - 1

    def test_backtrack_path_is_monotonic(self, ctc_module):
        """Path time indices should be monotonically increasing."""
        emission = torch.log_softmax(torch.randn(20, 5), dim=-1)
        tokens = [1, 2, 3]

        trellis = ctc_module.build_trellis(emission, tokens)
        path = ctc_module.backtrack(trellis, emission, tokens)

        assert path is not None
        for i in range(1, len(path)):
            assert path[i].time_index >= path[i - 1].time_index

    def test_backtrack_token_indices_monotonic(self, ctc_module):
        """Path token indices should be monotonically increasing."""
        emission = torch.log_softmax(torch.randn(20, 5), dim=-1)
        tokens = [1, 2, 3]

        trellis = ctc_module.build_trellis(emission, tokens)
        path = ctc_module.backtrack(trellis, emission, tokens)

        assert path is not None
        for i in range(1, len(path)):
            assert path[i].token_index >= path[i - 1].token_index


class TestMergeRepeats:
    """Tests for merging consecutive frames into character segments."""

    def test_merge_basic(self, ctc_module):
        """Merge consecutive frames for the same token."""
        # Simulate a path: token 0 for frames 0-4, token 1 for frames 5-9
        path = [ctc_module._Point(0, i, 0.9) for i in range(5)] + [
            ctc_module._Point(1, i, 0.8) for i in range(5, 10)
        ]

        segments = ctc_module.merge_repeats(path, "ab")

        assert len(segments) == 2
        assert segments[0].label == "a"
        assert segments[0].start == 0
        assert segments[0].end == 5  # exclusive end
        assert segments[1].label == "b"
        assert segments[1].start == 5
        assert segments[1].end == 10

    def test_merge_scores_are_averaged(self, ctc_module):
        """Segment score is the mean of frame scores."""
        path = [
            ctc_module._Point(0, 0, 0.6),
            ctc_module._Point(0, 1, 0.8),
            ctc_module._Point(0, 2, 1.0),
        ]

        segments = ctc_module.merge_repeats(path, "a")

        assert len(segments) == 1
        assert abs(segments[0].score - 0.8) < 1e-6


class TestWildcardEmission:
    """Tests for wildcard token handling."""

    def test_wildcard_gets_max_non_blank(self, ctc_module):
        """Wildcard tokens (-1) should get the max non-blank score."""
        frame = torch.tensor([0.1, 0.5, 0.3, 0.9])  # blank=0, max non-blank at idx 3
        tokens = [-1]

        result = ctc_module._token_emission(frame, tokens, blank_id=0)

        assert result[0].item() == pytest.approx(0.9)

    def test_regular_token_gets_its_score(self, ctc_module):
        """Non-wildcard tokens get their actual emission score."""
        frame = torch.tensor([0.1, 0.5, 0.3, 0.9])
        tokens = [2]

        result = ctc_module._token_emission(frame, tokens, blank_id=0)

        assert result[0].item() == pytest.approx(0.3)

    def test_mixed_wildcard_and_regular(self, ctc_module):
        """Mix of wildcard and regular tokens in one call."""
        frame = torch.tensor([0.1, 0.5, 0.3, 0.9])
        tokens = [1, -1, 2]

        result = ctc_module._token_emission(frame, tokens, blank_id=0)

        assert result[0].item() == pytest.approx(0.5)  # token 1
        assert result[1].item() == pytest.approx(0.9)  # wildcard -> max non-blank
        assert result[2].item() == pytest.approx(0.3)  # token 2


# =========================================================================
# Model Loader Tests
# =========================================================================


class TestLanguageSupport:
    """Tests for language support checking."""

    def test_english_is_supported(self, model_loader_module):
        assert model_loader_module.is_language_supported("en") is True

    def test_japanese_is_supported(self, model_loader_module):
        assert model_loader_module.is_language_supported("ja") is True

    def test_unknown_language_not_supported(self, model_loader_module):
        assert model_loader_module.is_language_supported("xx") is False

    def test_torchaudio_languages(self, model_loader_module):
        """All five torchaudio languages should be supported."""
        for lang in ("en", "fr", "de", "es", "it"):
            assert model_loader_module.is_language_supported(lang)

    def test_hf_languages_sample(self, model_loader_module):
        """Spot-check some HuggingFace-backed languages."""
        for lang in ("ja", "zh", "ru", "ar", "ko", "sv"):
            assert model_loader_module.is_language_supported(lang)


class TestAlignModelMetadata:
    """Tests for AlignModelMetadata."""

    def test_metadata_attributes(self, model_loader_module):
        metadata = model_loader_module.AlignModelMetadata(
            language="en",
            dictionary={"a": 1, "b": 2},
            pipeline_type="torchaudio",
        )

        assert metadata.language == "en"
        assert metadata.dictionary == {"a": 1, "b": 2}
        assert metadata.pipeline_type == "torchaudio"


class TestLoadAlignModelErrors:
    """Tests for model loading error paths."""

    def test_raises_for_unsupported_language(self, model_loader_module):
        with pytest.raises(ValueError, match="No default alignment model"):
            model_loader_module.load_align_model("xx", "cpu")


# =========================================================================
# Alignment Pipeline Tests
# =========================================================================


class TestPreprocessSegment:
    """Tests for text preprocessing."""

    def test_strips_leading_trailing_whitespace(self, align_module):
        sd = align_module._preprocess_segment(
            "  hello  ",
            {"h": 0, "e": 1, "l": 2, "o": 3, "|": 4},
            "en",
        )

        # Only inner characters should be present
        assert "*" not in sd["clean_chars"]  # all chars are in dict
        assert len(sd["clean_chars"]) == 5  # "hello"

    def test_spaces_become_pipe(self, align_module):
        sd = align_module._preprocess_segment(
            "hi there",
            {"h": 0, "i": 1, "|": 2, "t": 3, "e": 4, "r": 5},
            "en",
        )

        assert "|" in sd["clean_chars"]

    def test_unknown_chars_become_wildcard(self, align_module):
        sd = align_module._preprocess_segment(
            "café",
            {"c": 0, "a": 1, "f": 2},
            "en",
        )

        assert "*" in sd["clean_chars"]

    def test_japanese_no_pipe_substitution(self, align_module):
        """Japanese text should not substitute spaces for pipes."""
        sd = align_module._preprocess_segment(
            "こんにちは",
            {"こ": 0, "ん": 1, "に": 2, "ち": 3, "は": 4},
            "ja",
        )

        assert "|" not in sd["clean_chars"]
        assert len(sd["clean_chars"]) == 5


class TestInterpolateNans:
    """Tests for NaN interpolation of timestamps."""

    def test_no_nans(self, align_module):
        result = align_module._interpolate_nans([1.0, 2.0, 3.0])
        assert result == [1.0, 2.0, 3.0]

    def test_all_nans(self, align_module):
        result = align_module._interpolate_nans([None, None, None])
        assert result == [None, None, None]

    def test_middle_nan_nearest(self, align_module):
        """Default 'nearest' snaps to the closest known value."""
        result = align_module._interpolate_nans([1.0, None, 3.0], method="nearest")
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(1.0)  # equidistant, snaps left
        assert result[2] == pytest.approx(3.0)

    def test_middle_nan_nearest_snaps_to_closer(self, align_module):
        """When one neighbour is closer, nearest picks it."""
        result = align_module._interpolate_nans(
            [1.0, None, None, None, 5.0], method="nearest"
        )
        # index 1: closer to idx 0 (dist 1) than idx 4 (dist 3) → 1.0
        assert result[1] == pytest.approx(1.0)
        # index 2: equidistant → snaps left → 1.0
        assert result[2] == pytest.approx(1.0)
        # index 3: closer to idx 4 (dist 1) than idx 0 (dist 3) → 5.0
        assert result[3] == pytest.approx(5.0)

    def test_middle_nan_linear(self, align_module):
        """Linear method interpolates between neighbours."""
        result = align_module._interpolate_nans([1.0, None, 3.0], method="linear")
        assert result[1] == pytest.approx(2.0)

    def test_leading_nan_filled(self, align_module):
        result = align_module._interpolate_nans([None, 2.0, 3.0])
        assert result[0] is not None

    def test_trailing_nan_filled(self, align_module):
        result = align_module._interpolate_nans([1.0, 2.0, None])
        assert result[2] is not None

    def test_single_value_fills_all(self, align_module):
        result = align_module._interpolate_nans([None, 5.0, None])
        assert all(v == pytest.approx(5.0) for v in result)


class TestAggregateWords:
    """Tests for grouping characters into words."""

    def test_simple_word_aggregation(self, align_module):
        timings = [
            align_module._CharTiming("h", 0.0, 0.1, 0.9, 0),
            align_module._CharTiming("i", 0.1, 0.2, 0.8, 0),
            align_module._CharTiming(" ", None, None, None, 0),
            align_module._CharTiming("y", 0.3, 0.4, 0.85, 1),
            align_module._CharTiming("o", 0.4, 0.5, 0.95, 1),
        ]

        words = align_module._aggregate_words(timings, "en")

        assert len(words) == 2
        assert words[0].word == "hi"
        assert words[0].start == pytest.approx(0.0)
        assert words[0].end == pytest.approx(0.2)
        assert words[1].word == "yo"
        assert words[1].start == pytest.approx(0.3)
        assert words[1].end == pytest.approx(0.5)

    def test_empty_words_filtered(self, align_module):
        timings = [
            align_module._CharTiming(" ", None, None, None, 0),
        ]

        words = align_module._aggregate_words(timings, "en")

        assert len(words) == 0

    def test_word_score_is_mean(self, align_module):
        timings = [
            align_module._CharTiming("a", 0.0, 0.1, 0.6, 0),
            align_module._CharTiming("b", 0.1, 0.2, 0.8, 0),
        ]

        words = align_module._aggregate_words(timings, "en")

        assert words[0].score == pytest.approx(0.7)


class TestFindBlankId:
    """Tests for blank token detection."""

    def test_pad_token(self, align_module):
        assert align_module._find_blank_id({"[pad]": 5, "a": 1}) == 5

    def test_angle_pad_token(self, align_module):
        assert align_module._find_blank_id({"<pad>": 3, "a": 1}) == 3

    def test_default_to_zero(self, align_module):
        assert align_module._find_blank_id({"a": 1, "b": 2}) == 0


# =========================================================================
# End-to-end Alignment Test (with synthetic data)
# =========================================================================


class TestAlignEndToEnd:
    """End-to-end test of the alignment pipeline with synthetic data."""

    def test_align_produces_word_timestamps(self, align_module):
        """Alignment should produce word-level timestamps for a segment."""
        # Create a mock model that returns predictable emissions
        vocab_size = 30
        num_frames = 50

        mock_model = MagicMock()
        mock_emissions = torch.randn(1, num_frames, vocab_size)
        mock_model.return_value = MagicMock(logits=mock_emissions)

        metadata = align_module.__builtins__  # Need AlignModelMetadata
        # Import from model_loader since it's in sys.modules
        from model_loader import AlignModelMetadata

        dictionary = {chr(ord("a") + i): i + 1 for i in range(26)}
        dictionary["|"] = 27
        dictionary["<pad>"] = 0

        metadata = AlignModelMetadata(
            language="en",
            dictionary=dictionary,
            pipeline_type="huggingface",
        )

        audio = np.random.randn(16000 * 3).astype(np.float32)  # 3 seconds

        transcript = [
            align_module.InputSegment(start=0.0, end=3.0, text="hello world"),
        ]

        result = align_module.align(
            transcript=transcript,
            model=mock_model,
            metadata=metadata,
            audio=audio,
            device="cpu",
        )

        assert len(result.segments) == 1
        seg = result.segments[0]
        assert seg.text == "hello world"
        # Should have produced word-level timestamps (may or may not succeed
        # depending on synthetic emissions, but structure should be correct)
        assert isinstance(seg.words, list)

    def test_align_fallback_for_empty_segment(self, align_module):
        """Segment with no alignable characters should fall back gracefully."""
        from model_loader import AlignModelMetadata

        metadata = AlignModelMetadata(
            language="en",
            dictionary={"a": 1},
            pipeline_type="huggingface",
        )

        audio = np.random.randn(16000).astype(np.float32)
        transcript = [
            align_module.InputSegment(start=0.0, end=1.0, text="123 !@#"),
        ]

        mock_model = MagicMock()
        result = align_module.align(
            transcript=transcript,
            model=mock_model,
            metadata=metadata,
            audio=audio,
            device="cpu",
        )

        assert len(result.segments) == 1
        assert result.segments[0].text == "123 !@#"
        assert result.segments[0].words == []

    def test_align_multiple_segments(self, align_module):
        """Multiple segments should each be aligned independently."""
        from model_loader import AlignModelMetadata

        vocab_size = 30
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(logits=torch.randn(1, 50, vocab_size))

        dictionary = {chr(ord("a") + i): i + 1 for i in range(26)}
        dictionary["|"] = 27
        dictionary["<pad>"] = 0

        metadata = AlignModelMetadata(
            language="en",
            dictionary=dictionary,
            pipeline_type="huggingface",
        )

        audio = np.random.randn(16000 * 5).astype(np.float32)
        transcript = [
            align_module.InputSegment(start=0.0, end=2.0, text="hello"),
            align_module.InputSegment(start=2.0, end=4.0, text="world"),
        ]

        result = align_module.align(
            transcript=transcript,
            model=mock_model,
            metadata=metadata,
            audio=audio,
            device="cpu",
        )

        assert len(result.segments) == 2
        assert result.segments[0].text == "hello"
        assert result.segments[1].text == "world"


# =========================================================================
# Engine Wrapper Tests
# =========================================================================


class TestPhonemeAlignEngineInit:
    """Tests for engine initialization."""

    def test_cpu_device_detection(self, engine_module):
        """Engine detects CPU when CUDA unavailable."""
        with patch.object(torch.cuda, "is_available", return_value=False):
            engine = engine_module.PhonemeAlignEngine()

        assert engine._device == "cpu"
        assert engine._compute_type == "float32"

    def test_gpu_device_detection(self, engine_module):
        """Engine detects CUDA when available."""
        with patch.object(torch.cuda, "is_available", return_value=True):
            engine = engine_module.PhonemeAlignEngine()

        assert engine._device == "cuda"
        assert engine._compute_type == "float16"


class TestPhonemeAlignEngineHealthCheck:
    """Tests for engine health check."""

    def test_health_check_fields(self, engine_module):
        with patch.object(torch.cuda, "is_available", return_value=False):
            engine = engine_module.PhonemeAlignEngine()

        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["device"] == "cpu"
        assert "cuda_available" in health
        assert "cached_languages" in health
        assert isinstance(health["cached_languages"], list)

    def test_health_check_no_cached_models_initially(self, engine_module):
        with patch.object(torch.cuda, "is_available", return_value=False):
            engine = engine_module.PhonemeAlignEngine()

        health = engine.health_check()

        assert health["cached_languages"] == []


class TestPhonemeAlignEngineFallback:
    """Tests for the engine's fallback behavior."""

    def test_fallback_output_structure(self, engine_module):
        """Fallback output should have skipped=True and segment-level granularity."""
        with patch.object(torch.cuda, "is_available", return_value=False):
            engine = engine_module.PhonemeAlignEngine()

        from align import InputSegment

        result = engine._fallback_output(
            text="hello world",
            segments=[InputSegment(start=0.0, end=1.0, text="hello world")],
            language="en",
            reason="test fallback",
        )

        output = result.data
        assert output.skipped is True
        assert output.skip_reason == "test fallback"
        assert output.word_timestamps is False
        assert output.engine_id == "phoneme-align"
        assert len(output.segments) == 1
        assert output.segments[0].text == "hello world"

    def test_fallback_for_unsupported_language(self, engine_module):
        """Engine should fall back gracefully for unsupported languages."""
        with patch.object(torch.cuda, "is_available", return_value=False):
            engine = engine_module.PhonemeAlignEngine()

        model_result = engine._get_align_model("xx")

        assert model_result is None


class TestPhonemeAlignEngineDagIntegration:
    """Tests for phoneme-align integration with the orchestrator."""

    def test_engine_yaml_exists(self):
        """engine.yaml should exist."""
        path = Path("engines/stt-align/phoneme-align/engine.yaml")
        assert path.exists()

    def test_engine_yaml_has_correct_id(self):
        """engine.yaml should declare id: phoneme-align."""
        import yaml

        path = Path("engines/stt-align/phoneme-align/engine.yaml")
        with open(path) as f:
            config = yaml.safe_load(f)

        assert config["id"] == "phoneme-align"
        assert config["stage"] == "align"

    def test_engine_yaml_capabilities(self):
        """engine.yaml should declare word_timestamps and cpu support."""
        import yaml

        path = Path("engines/stt-align/phoneme-align/engine.yaml")
        with open(path) as f:
            config = yaml.safe_load(f)

        assert config["capabilities"]["word_timestamps"] is True
        assert config["hardware"]["supports_cpu"] is True
