"""Unit tests for PII detection engine heuristics."""

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


class TestPIIRuntimeModelSelection:
    """Tests for runtime_model_id-driven GLiNER loading."""

    def test_gliner_cache_is_keyed_by_runtime_model_id(self):
        PIIDetectionEngine = load_pii_engine()

        class _DummyModel:
            def __init__(self, model_id: str):
                self.model_id = model_id

            def to(self, device: str):
                return self

        class _DummyGLiNER:
            calls: list[tuple[str, str | None]] = []

            @classmethod
            def from_pretrained(cls, model_id: str, device: str | None = None):
                cls.calls.append((model_id, device))
                return _DummyModel(model_id)

        fake_gliner = types.SimpleNamespace(GLiNER=_DummyGLiNER)
        with patch.dict(sys.modules, {"gliner": fake_gliner}):
            engine = PIIDetectionEngine()
            engine._load_gliner("model/a")
            engine._load_gliner("model/a")  # Cached
            engine._load_gliner("model/b")

        assert _DummyGLiNER.calls[0][0] == "model/a"
        assert _DummyGLiNER.calls[1][0] == "model/b"
        assert len(_DummyGLiNER.calls) == 2
