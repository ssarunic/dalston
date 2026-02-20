"""Unit tests for PII detection engine heuristics."""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


def load_pii_engine():
    """Load PIIDetectionEngine from engines directory using importlib."""
    if importlib.util.find_spec("redis") is None:
        pytest.skip("redis package is required to import PII engine")

    engine_path = Path("engines/stt-detect/pii-presidio/engine.py")
    if not engine_path.exists():
        raise FileNotFoundError("PII engine not found")

    spec = importlib.util.spec_from_file_location("pii_detection_engine", engine_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load PII engine module spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["pii_detection_engine"] = module
    spec.loader.exec_module(module)
    return module.PIIDetectionEngine


class TestPIINameFalsePositiveFiltering:
    """Tests for filtering obvious non-name GLiNER predictions."""

    def test_filters_pronouns_for_name_entities(self):
        PIIDetectionEngine = load_pii_engine()
        engine = PIIDetectionEngine()

        assert engine._should_filter_gliner_entity("name", "I") is True
        assert engine._should_filter_gliner_entity("name", "you") is True
        assert engine._should_filter_gliner_entity("name", "them") is True
        assert engine._should_filter_gliner_entity("name", "I,") is True

    def test_keeps_real_name_like_tokens(self):
        PIIDetectionEngine = load_pii_engine()
        engine = PIIDetectionEngine()

        assert engine._should_filter_gliner_entity("name", "Alice") is False
        assert engine._should_filter_gliner_entity("name", "john") is False
        assert engine._should_filter_gliner_entity("organization", "you") is False

    def test_gliner_person_prediction_skips_pronouns(self):
        PIIDetectionEngine = load_pii_engine()
        engine = PIIDetectionEngine()

        class _DummyGLiNER:
            def predict_entities(self, text, labels, threshold):
                return [
                    {
                        "label": "person",
                        "start": 0,
                        "end": 1,
                        "text": "I",
                        "score": 0.98,
                    },
                    {
                        "label": "person",
                        "start": 12,
                        "end": 17,
                        "text": "Alice",
                        "score": 0.95,
                    },
                ]

        engine._gliner_model = _DummyGLiNER()

        entities = engine._detect_with_gliner(
            text="I talked to Alice yesterday.",
            entity_types=["name"],
            confidence_threshold=0.5,
            word_times={},
            speaker_turns=[],
        )

        assert len(entities) == 1
        assert entities[0].entity_type == "name"
        assert entities[0].original_text == "Alice"
