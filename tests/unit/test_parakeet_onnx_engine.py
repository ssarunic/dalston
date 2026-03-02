"""Unit tests for Parakeet ONNX batch transcription engine.

Tests the ParakeetOnnxEngine implementation with mocked onnx-asr models.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_onnx_engine.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def load_parakeet_onnx_engine():
    """Load ParakeetOnnxEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/parakeet-onnx/engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet ONNX engine not found")

    spec = importlib.util.spec_from_file_location("parakeet_onnx_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet ONNX engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_onnx_engine"] = module
    spec.loader.exec_module(module)
    return module.ParakeetOnnxEngine


class TestParakeetOnnxEngineModelVariants:
    """Tests for Parakeet ONNX model variants (M41)."""

    def test_default_model_id_is_ctc_0_6b(self):
        """Test that default model is nvidia/parakeet-ctc-0.6b."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine.DEFAULT_MODEL_ID == "nvidia/parakeet-ctc-0.6b"

    def test_supported_models_include_ctc_variants(self):
        """Test that CTC model variants are supported."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert "nvidia/parakeet-ctc-0.6b" in ParakeetOnnxEngine.SUPPORTED_MODELS
        assert "nvidia/parakeet-ctc-1.1b" in ParakeetOnnxEngine.SUPPORTED_MODELS

    def test_supported_models_include_tdt_variants(self):
        """Test that TDT model variants are supported."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert "nvidia/parakeet-tdt-0.6b-v2" in ParakeetOnnxEngine.SUPPORTED_MODELS
        assert "nvidia/parakeet-tdt-0.6b-v3" in ParakeetOnnxEngine.SUPPORTED_MODELS

    def test_supported_models_include_rnnt_variant(self):
        """Test that RNNT model variant is supported."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert "nvidia/parakeet-rnnt-0.6b" in ParakeetOnnxEngine.SUPPORTED_MODELS

    def test_supported_models_excludes_unavailable(self):
        """Test that models without ONNX conversions are not supported."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        # TDT 1.1b has no ONNX conversion available
        assert "nvidia/parakeet-tdt-1.1b" not in ParakeetOnnxEngine.SUPPORTED_MODELS

    def test_supported_models_count(self):
        """Test that all 5 ONNX models are supported."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert len(ParakeetOnnxEngine.SUPPORTED_MODELS) == 5


class TestParakeetOnnxEngineHealthCheck:
    """Tests for Parakeet ONNX engine health check."""

    def test_health_check_returns_required_fields(self):
        """Test that health check includes engine information."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        health = engine.health_check()

        assert "status" in health
        assert "engine_id" in health
        assert "device" in health
        assert "model_loaded" in health
        assert "quantization" in health

    def test_health_check_reports_healthy(self):
        """Test that health check reports healthy on init."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["model_loaded"] is False
        assert health["loaded_model_id"] is None

    def test_health_check_reports_engine_id(self):
        """Test that health check reports correct engine_id."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        health = engine.health_check()

        assert health["engine_id"] == "nemo-onnx"


class TestParakeetOnnxEngineCapabilities:
    """Tests for Parakeet ONNX engine capabilities."""

    def test_get_capabilities_returns_nemo_onnx_runtime(self):
        """Test that capabilities report nemo-onnx runtime."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert caps.runtime == "nemo-onnx"

    def test_get_capabilities_supports_word_timestamps(self):
        """Test that capabilities report word timestamp support."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert caps.supports_word_timestamps is True

    def test_get_capabilities_supports_cpu(self):
        """Test that capabilities report CPU support."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert caps.supports_cpu is True

    def test_get_capabilities_english_only(self):
        """Test that capabilities report English-only support."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert caps.languages == ["en"]

    def test_get_capabilities_transcribe_stage(self):
        """Test that capabilities report transcribe stage."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert "transcribe" in caps.stages

    def test_get_capabilities_no_streaming(self):
        """Test that capabilities report no streaming support (CTC)."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()
        caps = engine.get_capabilities()

        assert caps.supports_streaming is False


class TestParakeetOnnxEngineModelLoading:
    """Tests for model loading and validation."""

    def test_ensure_model_loaded_rejects_unsupported(self):
        """Test that loading a model without ONNX conversion raises ValueError."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        with pytest.raises(ValueError, match="Unknown model"):
            engine._ensure_model_loaded("nvidia/parakeet-tdt-1.1b")

    def test_ensure_model_loaded_rejects_unknown(self):
        """Test that loading a completely unknown model raises ValueError."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        with pytest.raises(ValueError, match="Unknown model"):
            engine._ensure_model_loaded("some-random-model")


class TestParakeetOnnxDecoderTypeDetection:
    """Tests for decoder type extraction and alignment method mapping."""

    def test_ctc_decoder_type(self):
        """Test that CTC model IDs produce ctc decoder type."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._get_decoder_type("nvidia/parakeet-ctc-0.6b") == "ctc"
        assert ParakeetOnnxEngine._get_decoder_type("nvidia/parakeet-ctc-1.1b") == "ctc"

    def test_tdt_decoder_type(self):
        """Test that TDT model IDs produce tdt decoder type."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._get_decoder_type("nvidia/parakeet-tdt-0.6b-v2") == "tdt"
        assert ParakeetOnnxEngine._get_decoder_type("nvidia/parakeet-tdt-0.6b-v3") == "tdt"

    def test_rnnt_decoder_type(self):
        """Test that RNNT model IDs produce rnnt decoder type."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._get_decoder_type("nvidia/parakeet-rnnt-0.6b") == "rnnt"

    def test_alignment_method_ctc(self):
        """Test that CTC maps to AlignmentMethod.CTC."""
        from dalston.common.pipeline_types import AlignmentMethod

        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._alignment_method_for("ctc") == AlignmentMethod.CTC

    def test_alignment_method_tdt(self):
        """Test that TDT maps to AlignmentMethod.TDT."""
        from dalston.common.pipeline_types import AlignmentMethod

        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._alignment_method_for("tdt") == AlignmentMethod.TDT

    def test_alignment_method_rnnt(self):
        """Test that RNNT maps to AlignmentMethod.RNNT."""
        from dalston.common.pipeline_types import AlignmentMethod

        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        assert ParakeetOnnxEngine._alignment_method_for("rnnt") == AlignmentMethod.RNNT


class TestParakeetOnnxCatalogIntegration:
    """Tests for Parakeet ONNX integration with the model catalog (M41)."""

    def test_onnx_ctc_models_exist_in_catalog(self):
        """Test that CTC ONNX model entries exist in the catalog."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        model_06 = catalog.get_model("parakeet-onnx-ctc-0.6b")
        model_11 = catalog.get_model("parakeet-onnx-ctc-1.1b")

        assert model_06 is not None, "parakeet-onnx-ctc-0.6b not found in catalog"
        assert model_11 is not None, "parakeet-onnx-ctc-1.1b not found in catalog"

    def test_onnx_tdt_models_exist_in_catalog(self):
        """Test that TDT ONNX model entries exist in the catalog."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        model_v2 = catalog.get_model("parakeet-onnx-tdt-0.6b-v2")
        model_v3 = catalog.get_model("parakeet-onnx-tdt-0.6b-v3")

        assert model_v2 is not None, "parakeet-onnx-tdt-0.6b-v2 not found in catalog"
        assert model_v3 is not None, "parakeet-onnx-tdt-0.6b-v3 not found in catalog"

    def test_onnx_rnnt_model_exists_in_catalog(self):
        """Test that RNNT ONNX model entry exists in the catalog."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        model = catalog.get_model("parakeet-onnx-rnnt-0.6b")

        assert model is not None, "parakeet-onnx-rnnt-0.6b not found in catalog"

    def test_onnx_models_use_nemo_onnx_runtime(self):
        """Test that all ONNX models map to nemo-onnx runtime."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()

        for model_id in [
            "parakeet-onnx-ctc-0.6b",
            "parakeet-onnx-ctc-1.1b",
            "parakeet-onnx-tdt-0.6b-v2",
            "parakeet-onnx-tdt-0.6b-v3",
            "parakeet-onnx-rnnt-0.6b",
        ]:
            runtime = catalog.get_runtime_for_model(model_id)
            assert runtime == "nemo-onnx", f"{model_id} should use nemo-onnx runtime"

    def test_onnx_models_have_correct_runtime_model_ids(self):
        """Test that ONNX models map to correct HuggingFace model IDs."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()

        expected = {
            "parakeet-onnx-ctc-0.6b": "nvidia/parakeet-ctc-0.6b",
            "parakeet-onnx-ctc-1.1b": "nvidia/parakeet-ctc-1.1b",
            "parakeet-onnx-tdt-0.6b-v2": "nvidia/parakeet-tdt-0.6b-v2",
            "parakeet-onnx-tdt-0.6b-v3": "nvidia/parakeet-tdt-0.6b-v3",
            "parakeet-onnx-rnnt-0.6b": "nvidia/parakeet-rnnt-0.6b",
        }

        for model_id, expected_runtime_id in expected.items():
            actual = catalog.get_runtime_model_id(model_id)
            assert actual == expected_runtime_id, (
                f"{model_id}: expected {expected_runtime_id}, got {actual}"
            )

    def test_onnx_models_support_cpu(self):
        """Test that ONNX models support CPU (unlike NeMo variants)."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        onnx_models = catalog.get_models_for_runtime("nemo-onnx")

        assert len(onnx_models) >= 5
        for model in onnx_models:
            assert model.supports_cpu is True, (
                f"ONNX model {model.id} should support CPU"
            )

    def test_onnx_models_have_word_timestamps(self):
        """Test that ONNX models report word timestamp support."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        onnx_models = catalog.get_models_for_runtime("nemo-onnx")

        for model in onnx_models:
            assert model.word_timestamps is True, (
                f"ONNX model {model.id} should have word_timestamps=True"
            )

    def test_onnx_runtime_exists_in_catalog(self):
        """Test that nemo-onnx runtime exists in the engine catalog."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        engine = catalog.get_engine("nemo-onnx")

        assert engine is not None, "nemo-onnx runtime not found in catalog"
        assert "transcribe" in engine.capabilities.stages

    def test_onnx_runtime_supports_english(self):
        """Test that nemo-onnx runtime reports English support."""
        from dalston.orchestrator.catalog import get_catalog

        catalog = get_catalog()
        engine = catalog.get_engine("nemo-onnx")

        assert engine is not None
        assert engine.capabilities.languages == ["en"]

    def test_dag_skips_align_for_onnx_ctc(self):
        """Test that DAG builder skips align stage for ONNX CTC models."""
        from uuid import uuid4

        from tests.dag_test_helpers import build_task_dag_for_test

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "engine_transcribe": "parakeet-onnx-ctc-0.6b",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages
        assert "merge" in stages

    def test_dag_skips_align_for_onnx_tdt(self):
        """Test that DAG builder skips align stage for ONNX TDT models."""
        from uuid import uuid4

        from tests.dag_test_helpers import build_task_dag_for_test

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "engine_transcribe": "parakeet-onnx-tdt-0.6b-v3",
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
            "engine_transcribe": "parakeet-onnx-rnnt-0.6b",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages


class TestParakeetOnnxTokensToWords:
    """Tests for SentencePiece token-to-word grouping."""

    def test_tokens_to_words_basic(self):
        """Test basic subword token grouping into words."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        # Simulate SentencePiece tokens with \u2581 word boundaries
        mock_tokens = [
            MagicMock(text="\u2581Hello", start=0.0, end=0.3),
            MagicMock(text="\u2581world", start=0.3, end=0.6),
        ]

        words = engine._tokens_to_words(mock_tokens)

        assert len(words) == 2
        assert words[0].text == "Hello"
        assert words[1].text == "world"

    def test_tokens_to_words_multipiece(self):
        """Test grouping multi-piece words from subword tokens."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        # "transcription" split into subwords
        mock_tokens = [
            MagicMock(text="\u2581trans", start=0.0, end=0.2),
            MagicMock(text="crip", start=0.2, end=0.4),
            MagicMock(text="tion", start=0.4, end=0.6),
            MagicMock(text="\u2581is", start=0.6, end=0.8),
        ]

        words = engine._tokens_to_words(mock_tokens)

        assert len(words) == 2
        assert words[0].text == "transcription"
        assert words[0].start == 0.0
        assert words[0].end == 0.6
        assert words[1].text == "is"

    def test_tokens_to_words_empty(self):
        """Test that empty token list returns empty word list."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        words = engine._tokens_to_words([])
        assert words == []

    def test_tokens_to_words_timestamps_preserved(self):
        """Test that word timestamps reflect first and last token times."""
        ParakeetOnnxEngine = load_parakeet_onnx_engine()
        engine = ParakeetOnnxEngine()

        mock_tokens = [
            MagicMock(text="\u2581good", start=1.0, end=1.3),
            MagicMock(text="\u2581morn", start=1.3, end=1.5),
            MagicMock(text="ing", start=1.5, end=1.8),
        ]

        words = engine._tokens_to_words(mock_tokens)

        assert len(words) == 2
        assert words[0].start == 1.0
        assert words[0].end == 1.3
        assert words[1].start == 1.3
        assert words[1].end == 1.8
