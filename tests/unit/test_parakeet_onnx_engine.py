"""Unit tests for Parakeet ONNX batch transcription engine.

Tests the OnnxBatchEngine implementation with mocked onnx-asr models.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_onnx_engine.py
"""

import importlib.util
import sys
from pathlib import Path

import pytest


def load_parakeet_onnx_engine():
    """Load OnnxBatchEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/parakeet-onnx/engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet ONNX engine not found")

    spec = importlib.util.spec_from_file_location("parakeet_onnx_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet ONNX engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_onnx_engine"] = module
    spec.loader.exec_module(module)
    return module.OnnxBatchEngine


class TestParakeetOnnxEngineModelVariants:
    """Tests for Parakeet ONNX model variants (M41)."""

    def test_default_model_id_is_ctc_0_6b(self):
        """Test that default model is parakeet-onnx-ctc-0.6b."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine.DEFAULT_MODEL_ID == "parakeet-onnx-ctc-0.6b"

    def test_supported_models_include_ctc_variants(self):
        """Test that CTC model variants are supported."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert "parakeet-onnx-ctc-0.6b" in OnnxBatchEngine.CURATED_MODELS
        assert "parakeet-onnx-ctc-1.1b" in OnnxBatchEngine.CURATED_MODELS

    def test_supported_models_include_tdt_variants(self):
        """Test that TDT model variants are supported."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert "parakeet-onnx-tdt-0.6b-v2" in OnnxBatchEngine.CURATED_MODELS
        assert "parakeet-onnx-tdt-0.6b-v3" in OnnxBatchEngine.CURATED_MODELS

    def test_supported_models_include_rnnt_variant(self):
        """Test that RNNT model variant is supported."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert "parakeet-onnx-rnnt-0.6b" in OnnxBatchEngine.CURATED_MODELS

    def test_supported_models_excludes_unavailable(self):
        """Test that models without ONNX conversions are not supported."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        # TDT 1.1b has no ONNX conversion available
        assert "nvidia/parakeet-tdt-1.1b" not in OnnxBatchEngine.CURATED_MODELS

    def test_supported_models_count(self):
        """Test that all 5 ONNX models are supported."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert len(OnnxBatchEngine.CURATED_MODELS) == 5


class TestParakeetOnnxEngineHealthCheck:
    """Tests for Parakeet ONNX engine health check."""

    def test_health_check_returns_required_fields(self):
        """Test that health check includes engine information."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        health = engine.health_check()

        assert "status" in health
        assert "engine_id" in health
        assert "device" in health
        assert "models_loaded" in health
        assert "quantization" in health

    def test_health_check_reports_healthy(self):
        """Test that health check reports healthy on init."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["model_count"] == 0

    def test_health_check_reports_engine_id(self):
        """Test that health check reports correct engine_id."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        health = engine.health_check()

        assert health["engine_id"] == "onnx"


class TestParakeetOnnxEngineCapabilities:
    """Tests for Parakeet ONNX engine capabilities."""

    def test_get_capabilities_returns_onnx_engine_id(self):
        """Test that capabilities report onnx engine_id."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert caps.engine_id == "onnx"

    def test_get_capabilities_supports_word_timestamps(self):
        """Test that capabilities report word timestamp support."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert caps.supports_word_timestamps is True

    def test_get_capabilities_supports_cpu(self):
        """Test that capabilities report CPU support."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert caps.supports_cpu is True

    def test_get_capabilities_english_only(self):
        """Test that capabilities report English-only support."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert caps.languages == ["en"]

    def test_get_capabilities_transcribe_stage(self):
        """Test that capabilities report transcribe stage."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert "transcribe" in caps.stages

    def test_get_capabilities_no_streaming(self):
        """Test that capabilities report no streaming support (CTC)."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        caps = engine.get_capabilities()

        assert caps.supports_streaming is False


class TestParakeetOnnxEngineModelLoading:
    """Tests for model loading and validation."""

    def test_normalize_legacy_nvidia_model_id(self):
        """Test that legacy NVIDIA IDs normalize to ONNX runtime IDs."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        assert (
            engine._normalize_model_id("nvidia/parakeet-ctc-0.6b")
            == "parakeet-onnx-ctc-0.6b"
        )

    def test_normalize_onnx_hf_repo_id(self):
        """Test that ONNX HF repo IDs normalize to ONNX runtime IDs."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()
        assert (
            engine._normalize_model_id("istupakov/parakeet-tdt-0.6b-v3-onnx")
            == "parakeet-onnx-tdt-0.6b-v3"
        )

    def test_core_manager_passes_through_unknown_model(self):
        """Test that unknown model IDs are passed through to onnx_asr."""
        from unittest.mock import MagicMock, patch

        from dalston.engine_sdk.managers import OnnxModelManager

        manager = OnnxModelManager(device="cpu")
        mock_model = MagicMock()
        mock_onnx_asr = MagicMock()
        mock_onnx_asr.load_model.return_value = mock_model

        with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
            result = manager.acquire("nvidia/parakeet-tdt-1.1b")

        try:
            # Passed through as-is (not in MODEL_ALIASES)
            mock_onnx_asr.load_model.assert_called_once_with(
                "nvidia/parakeet-tdt-1.1b",
                quantization=None,
                providers=["CPUExecutionProvider"],
            )
            assert result is mock_model
        finally:
            manager.release("nvidia/parakeet-tdt-1.1b")
            manager.shutdown()


class TestParakeetOnnxDecoderTypeDetection:
    """Tests for decoder type extraction and alignment method mapping."""

    def test_ctc_decoder_type(self):
        """Test that CTC model IDs produce ctc decoder type."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._get_decoder_type("nvidia/parakeet-ctc-0.6b") == "ctc"
        assert OnnxBatchEngine._get_decoder_type("nvidia/parakeet-ctc-1.1b") == "ctc"

    def test_tdt_decoder_type(self):
        """Test that TDT model IDs produce tdt decoder type."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._get_decoder_type("nvidia/parakeet-tdt-0.6b-v2") == "tdt"
        assert OnnxBatchEngine._get_decoder_type("nvidia/parakeet-tdt-0.6b-v3") == "tdt"

    def test_rnnt_decoder_type(self):
        """Test that RNNT model IDs produce rnnt decoder type."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._get_decoder_type("nvidia/parakeet-rnnt-0.6b") == "rnnt"

    def test_alignment_method_ctc(self):
        """Test that CTC maps to AlignmentMethod.CTC."""
        from dalston.common.pipeline_types import AlignmentMethod

        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._alignment_method_for("ctc") == AlignmentMethod.CTC

    def test_alignment_method_tdt(self):
        """Test that TDT maps to AlignmentMethod.TDT."""
        from dalston.common.pipeline_types import AlignmentMethod

        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._alignment_method_for("tdt") == AlignmentMethod.TDT

    def test_alignment_method_rnnt(self):
        """Test that RNNT maps to AlignmentMethod.RNNT."""
        from dalston.common.pipeline_types import AlignmentMethod

        OnnxBatchEngine = load_parakeet_onnx_engine()
        assert OnnxBatchEngine._alignment_method_for("rnnt") == AlignmentMethod.RNNT


class TestParakeetOnnxCatalogIntegration:
    """Tests for Parakeet ONNX integration with the engine catalog (M41).

    Note: Model metadata tests have been removed as models are now managed
    in the database (M46). Use ModelRegistryService for model metadata.
    """

    def test_onnx_engine_id_exists_in_catalog(self):
        """Test that onnx engine_id exists in the engine catalog."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        engine = catalog.get_engine("onnx")

        assert engine is not None, "onnx engine_id not found in catalog"
        assert "transcribe" in engine.capabilities.stages

    def test_onnx_engine_id_supports_english(self):
        """Test that onnx engine_id reports English support."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        engine = catalog.get_engine("onnx")

        assert engine is not None
        assert engine.capabilities.languages == ["en"]

    def test_dag_skips_align_for_onnx_ctc(self):
        """Test that DAG builder skips align stage for ONNX CTC models."""
        from uuid import uuid4

        from tests.dag_test_helpers import build_task_dag_for_test

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "model_transcribe": "parakeet-onnx-ctc-0.6b",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages
        # Mono pipeline: no merge stage
        assert "merge" not in stages

    def test_dag_skips_align_for_onnx_tdt(self):
        """Test that DAG builder skips align stage for ONNX TDT models."""
        from uuid import uuid4

        from tests.dag_test_helpers import build_task_dag_for_test

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "model_transcribe": "parakeet-onnx-tdt-0.6b-v3",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages

    def test_dag_skips_align_for_onnx_rnnt(self):
        """Test that DAG builder skips align stage for ONNX RNNT models."""
        from uuid import uuid4

        from tests.dag_test_helpers import build_task_dag_for_test

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "model_transcribe": "parakeet-onnx-rnnt-0.6b",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages


class TestParakeetOnnxTokensToWords:
    """Tests for SentencePiece token-to-word grouping."""

    def test_tokens_to_words_basic_unicode(self):
        """Test basic subword token grouping with Unicode word boundaries."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        # SentencePiece tokens with \u2581 word boundaries
        tokens = ["\u2581Hello", "\u2581world"]
        timestamps = [0.0, 0.3]

        words = engine._core._tokens_to_words(tokens, timestamps)

        assert len(words) == 2
        assert words[0].word == "Hello"
        assert words[1].word == "world"

    def test_tokens_to_words_basic_space(self):
        """Test basic subword token grouping with space word boundaries (onnx-asr style)."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        # onnx-asr style tokens with space prefix
        tokens = [" Hello", " world"]
        timestamps = [0.0, 0.3]

        words = engine._core._tokens_to_words(tokens, timestamps)

        assert len(words) == 2
        assert words[0].word == "Hello"
        assert words[1].word == "world"

    def test_tokens_to_words_multipiece(self):
        """Test grouping multi-piece words from subword tokens."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        # "transcription" split into subwords, then "is"
        tokens = [" trans", "crip", "tion", " is"]
        timestamps = [0.0, 0.2, 0.4, 0.6]

        words = engine._core._tokens_to_words(tokens, timestamps)

        assert len(words) == 2
        assert words[0].word == "transcription"
        assert words[0].start == 0.0
        assert words[0].end == 0.6  # End is start of next word
        assert words[1].word == "is"

    def test_tokens_to_words_empty(self):
        """Test that empty token list returns empty word list."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        words = engine._core._tokens_to_words([], [])
        assert words == []

    def test_tokens_to_words_timestamps_preserved(self):
        """Test that word timestamps reflect first and last token times."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        # "good" then "morning" split into subwords
        tokens = [" good", " morn", "ing"]
        timestamps = [1.0, 1.3, 1.5]

        words = engine._core._tokens_to_words(tokens, timestamps)

        assert len(words) == 2
        assert words[0].start == 1.0
        assert words[0].end == 1.3  # End is start of "morn"
        assert words[1].start == 1.3
        assert words[1].end == 1.5  # Last token, end equals start


class TestParakeetOnnxWordsToSegments:
    """Tests for sentence-boundary based segment splitting."""

    def test_words_to_segments_single_sentence(self):
        """Test that a single sentence creates one segment."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        from dalston.engine_sdk.inference.onnx_inference import OnnxWordResult

        words = [
            OnnxWordResult(word="Hello", start=0.0, end=0.3),
            OnnxWordResult(word="world.", start=0.3, end=0.6),
        ]

        segments = engine._core._words_to_segments(words, "Hello world.")

        assert len(segments) == 1
        assert segments[0].text == "Hello world."
        assert len(segments[0].words) == 2

    def test_words_to_segments_multiple_sentences(self):
        """Test that multiple sentences create multiple segments."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        from dalston.engine_sdk.inference.onnx_inference import OnnxWordResult

        words = [
            OnnxWordResult(word="Hello.", start=0.0, end=0.3),
            OnnxWordResult(word="How", start=0.5, end=0.7),
            OnnxWordResult(word="are", start=0.7, end=0.9),
            OnnxWordResult(word="you?", start=0.9, end=1.2),
        ]

        segments = engine._core._words_to_segments(words, "Hello. How are you?")

        assert len(segments) == 2
        assert segments[0].text == "Hello."
        assert segments[1].text == "How are you?"

    def test_words_to_segments_question_mark(self):
        """Test that question marks create segment boundaries."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        from dalston.engine_sdk.inference.onnx_inference import OnnxWordResult

        words = [
            OnnxWordResult(word="What?", start=0.0, end=0.3),
            OnnxWordResult(word="Really!", start=0.5, end=0.8),
        ]

        segments = engine._core._words_to_segments(words, "What? Really!")

        assert len(segments) == 2
        assert segments[0].text == "What?"
        assert segments[1].text == "Really!"

    def test_words_to_segments_no_punctuation(self):
        """Test that text without sentence-ending punctuation stays as one segment."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        from dalston.engine_sdk.inference.onnx_inference import OnnxWordResult

        words = [
            OnnxWordResult(word="hello", start=0.0, end=0.2),
            OnnxWordResult(word="world", start=0.2, end=0.4),
        ]

        segments = engine._core._words_to_segments(words, "hello world")

        assert len(segments) == 1
        assert segments[0].text == "hello world"

    def test_words_to_segments_empty(self):
        """Test that empty words list creates fallback segment from full_text."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        segments = engine._core._words_to_segments([], "Some text")

        assert len(segments) == 1
        assert segments[0].text == "Some text"
        assert segments[0].words == []

    def test_words_to_segments_timestamps_preserved(self):
        """Test that segment timestamps match first/last word times."""
        OnnxBatchEngine = load_parakeet_onnx_engine()
        engine = OnnxBatchEngine()

        from dalston.engine_sdk.inference.onnx_inference import OnnxWordResult

        words = [
            OnnxWordResult(word="First.", start=1.0, end=1.5),
            OnnxWordResult(word="Second.", start=2.0, end=2.5),
        ]

        segments = engine._core._words_to_segments(words, "First. Second.")

        assert len(segments) == 2
        assert segments[0].start == 1.0
        assert segments[0].end == 1.5
        assert segments[1].start == 2.0
        assert segments[1].end == 2.5
