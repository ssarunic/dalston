"""Unit tests for M92 step 92.8: NeMo confidence emission (flag-gated).

When DALSTON_NEMO_CONFIDENCE is enabled, the decoding config preserves
word confidence; the shared core parses hypothesis.word_confidence into
word confidences and per-segment means. Never fabricated: absent
confidences stay None.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import structlog

from dalston.engine_sdk.inference.nemo_inference import NemoInference


def _hypothesis(word_confidence=None):
    hyp = SimpleNamespace(
        text="hello world",
        timestamp={
            "word": [
                {"word": "hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.6, "end": 1.0},
            ],
            "segment": [{"start": 0.0, "end": 1.0, "segment": "hello world"}],
        },
    )
    if word_confidence is not None:
        hyp.word_confidence = word_confidence
    return hyp


class TestConfidenceParsing:
    def test_confidences_attach_to_words_and_segment(self):
        segments, words = NemoInference._parse_hypothesis(
            _hypothesis(word_confidence=[0.9, 0.7]), "hello world"
        )
        assert [w.confidence for w in words] == [0.9, 0.7]
        assert segments[0].confidence == 0.8  # mean

    def test_absent_confidence_stays_none(self):
        segments, words = NemoInference._parse_hypothesis(_hypothesis(), "hello world")
        assert all(w.confidence is None for w in words)
        assert segments[0].confidence is None

    def test_length_mismatch_ignored(self):
        segments, words = NemoInference._parse_hypothesis(
            _hypothesis(word_confidence=[0.9]), "hello world"
        )
        assert all(w.confidence is None for w in words)
        assert segments[0].confidence is None

    def test_non_numeric_confidence_ignored(self):
        _, words = NemoInference._parse_hypothesis(
            _hypothesis(word_confidence=["high", "low"]), "hello world"
        )
        assert all(w.confidence is None for w in words)


class TestConfidenceThroughAssembly:
    """R4: segment confidence must survive the assembly boundary."""

    _PREPARE = {
        "channel_files": [
            {
                "artifact_id": "a1",
                "format": "wav",
                "duration": 10.0,
                "sample_rate": 16000,
                "channels": 1,
            }
        ],
        "engine_id": "audio-prepare",
    }

    def _transcribe(self) -> dict:
        return {
            "text": "hello",
            "language": "hr",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "hello",
                    "metadata": {"confidence": 0.83},
                }
            ],
            "engine_id": "nemo",
        }

    def test_standard_assembly_carries_confidence(self):
        from dalston.common.transcript import assemble_transcript

        result = assemble_transcript(
            job_id="j1",
            stage_outputs={"prepare": self._PREPARE, "transcribe": self._transcribe()},
        )
        assert result.segments[0].confidence == 0.83

    def test_per_channel_assembly_carries_confidence(self):
        from dalston.common.transcript import assemble_per_channel_transcript

        result = assemble_per_channel_transcript(
            job_id="j2",
            stage_outputs={
                "prepare": self._PREPARE,
                "transcribe_ch0": self._transcribe(),
                "transcribe_ch1": self._transcribe(),
            },
            channel_count=2,
        )
        assert all(seg.confidence == 0.83 for seg in result.segments)

    def test_absent_confidence_stays_none_through_assembly(self):
        from dalston.common.transcript import assemble_transcript

        transcribe = self._transcribe()
        transcribe["segments"][0]["metadata"] = {}
        result = assemble_transcript(
            job_id="j3",
            stage_outputs={"prepare": self._PREPARE, "transcribe": transcribe},
        )
        assert result.segments[0].confidence is None


@pytest.fixture(scope="module")
def nemo_engine_cls():
    engine_path = Path("engines/stt-transcribe/nemo/batch_engine.py")
    if not engine_path.exists():
        pytest.skip("nemo engine not found")
    spec = importlib.util.spec_from_file_location("m92_conf_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m92_conf_engine"] = module
    try:
        spec.loader.exec_module(module)
        yield module.NemoBatchEngine
    finally:
        sys.modules.pop("m92_conf_engine", None)


def _bare_engine(cls) -> SimpleNamespace:
    return SimpleNamespace(
        logger=structlog.get_logger(),
        _confidence_applied=set(),
        _confidence_emission_enabled=cls._confidence_emission_enabled,
        _ensure_confidence=cls._ensure_confidence,
    )


def _model():
    decoding = SimpleNamespace(
        strategy="greedy_batch",
        confidence_cfg=SimpleNamespace(preserve_word_confidence=False),
    )
    return SimpleNamespace(
        cfg=SimpleNamespace(decoding=decoding),
        change_decoding_strategy=MagicMock(),
    )


class TestEnsureConfidence:
    def test_disabled_by_default(self, nemo_engine_cls, monkeypatch):
        monkeypatch.delenv("DALSTON_NEMO_CONFIDENCE", raising=False)
        engine = _bare_engine(nemo_engine_cls)
        model = _model()
        engine._ensure_confidence(engine, model, "m1")
        model.change_decoding_strategy.assert_not_called()

    def test_enabled_applies_config_once(self, nemo_engine_cls, monkeypatch):
        monkeypatch.setenv("DALSTON_NEMO_CONFIDENCE", "1")
        engine = _bare_engine(nemo_engine_cls)
        model = _model()
        engine._ensure_confidence(engine, model, "m1")
        applied = model.change_decoding_strategy.call_args.args[0]
        assert applied.confidence_cfg.preserve_word_confidence is True
        # Live config untouched (mutation happened on a deepcopy).
        assert model.cfg.decoding.confidence_cfg.preserve_word_confidence is False

        # Second call for the same model is a no-op.
        engine._ensure_confidence(engine, model, "m1")
        assert model.change_decoding_strategy.call_count == 1

    def test_config_failure_is_loud_but_not_fatal(self, nemo_engine_cls, monkeypatch):
        monkeypatch.setenv("DALSTON_NEMO_CONFIDENCE", "true")
        engine = _bare_engine(nemo_engine_cls)
        # No confidence_cfg on the decoding config -> AttributeError inside.
        model = SimpleNamespace(
            cfg=SimpleNamespace(decoding=SimpleNamespace(strategy="greedy_batch")),
            change_decoding_strategy=MagicMock(),
        )
        engine._ensure_confidence(engine, model, "m1")  # must not raise
        model.change_decoding_strategy.assert_not_called()
        # Not retried on subsequent tasks.
        engine._ensure_confidence(engine, model, "m1")
        model.change_decoding_strategy.assert_not_called()
