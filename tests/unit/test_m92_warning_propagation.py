"""Unit tests for M92 step 92.2: warning propagation + vocab boosting robustness.

Covers:
- Engine Transcript.warnings reaching pipeline_warnings in both assembly paths
- Successful-align warnings no longer being dropped
- NeMo vocabulary boosting: temp-file cleanup on failure, config-copy isolation,
  and the per-model boosting_tree capability probe
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import structlog

from dalston.common.pipeline_types import (
    AlignmentResponse,
    Segment,
    Transcript,
)
from dalston.common.transcript import (
    _select_segments,
    assemble_per_channel_transcript,
    assemble_transcript,
)

# ---------------------------------------------------------------------------
# Warning propagation (F9)
# ---------------------------------------------------------------------------

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


def _transcribe_output(warnings: list[str] | None = None) -> dict:
    return {
        "text": "Hello world",
        "language": "en",
        "segments": [{"start": 0.0, "end": 2.0, "text": "Hello world"}],
        "engine_id": "nemo",
        "warnings": warnings or [],
    }


class TestEngineWarningPropagation:
    def test_standard_assembly_propagates_engine_warnings(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe": _transcribe_output(
                ["Vocabulary boosting (2 terms) failed to configure"]
            ),
        }
        result = assemble_transcript(job_id="j1", stage_outputs=stage_outputs)
        assert (
            "Vocabulary boosting (2 terms) failed to configure"
            in result.metadata.pipeline_warnings
        )

    def test_per_channel_assembly_prefixes_engine_warnings(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe_ch0": _transcribe_output(["warning zero"]),
            "transcribe_ch1": _transcribe_output(["warning one"]),
        }
        result = assemble_per_channel_transcript(
            job_id="j2", stage_outputs=stage_outputs, channel_count=2
        )
        assert "ch0: warning zero" in result.metadata.pipeline_warnings
        assert "ch1: warning one" in result.metadata.pipeline_warnings

    def test_successful_align_warnings_propagate(self):
        transcript = Transcript.model_validate(_transcribe_output())
        align = AlignmentResponse(
            text="Hello world",
            language="en",
            segments=[Segment(start=0.0, end=2.0, text="Hello world")],
            word_timestamps=True,
            engine_id="phoneme-align",
            skipped=False,
            warnings=["align quality degraded"],
        )
        _, _, warnings = _select_segments(align_response=align, transcript=transcript)
        assert "align quality degraded" in warnings

    def test_no_warnings_is_empty(self):
        stage_outputs = {"prepare": _PREPARE, "transcribe": _transcribe_output()}
        result = assemble_transcript(job_id="j3", stage_outputs=stage_outputs)
        assert result.metadata.pipeline_warnings == []


# ---------------------------------------------------------------------------
# NeMo vocabulary boosting robustness (F8)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nemo_engine_cls():
    engine_path = Path("engines/stt-transcribe/nemo/batch_engine.py")
    if not engine_path.exists():
        pytest.skip("nemo engine not found")
    spec = importlib.util.spec_from_file_location("m92_parakeet_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m92_parakeet_engine"] = module
    try:
        spec.loader.exec_module(module)
        yield module.NemoBatchEngine
    finally:
        sys.modules.pop("m92_parakeet_engine", None)


def _bare_engine(cls) -> SimpleNamespace:
    """Engine-shaped object for exercising methods without model loading."""
    return SimpleNamespace(
        logger=structlog.get_logger(),
        _boosting_support={},
        _configure_vocabulary_boosting=cls._configure_vocabulary_boosting,
        _model_supports_boosting=cls._model_supports_boosting,
    )


class _GreedyCfg(SimpleNamespace):
    """Attribute container that also supports `in`, like OmegaConf DictConfig."""

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


def _model_with_boosting() -> SimpleNamespace:
    greedy = _GreedyCfg(
        boosting_tree=SimpleNamespace(
            key_phrases_file=None, context_score=0.0, depth_scaling=0.0
        ),
        boosting_tree_alpha=0.0,
    )
    decoding = SimpleNamespace(strategy="greedy", greedy=greedy)
    return SimpleNamespace(
        cfg=SimpleNamespace(decoding=decoding),
        change_decoding_strategy=MagicMock(),
    )


def _model_without_boosting() -> SimpleNamespace:
    decoding = SimpleNamespace(strategy="greedy", greedy=_GreedyCfg())
    return SimpleNamespace(
        cfg=SimpleNamespace(decoding=decoding),
        change_decoding_strategy=MagicMock(),
    )


class TestVocabularyBoostingRobustness:
    def test_success_returns_path_and_leaves_live_config_untouched(
        self, nemo_engine_cls
    ):
        engine = _bare_engine(nemo_engine_cls)
        model = _model_with_boosting()

        path = engine._configure_vocabulary_boosting(engine, model, ["Bizzon"])
        try:
            assert path is not None and path.exists()
            # Mutations happened on a deepcopy; live config is untouched.
            assert model.cfg.decoding.strategy == "greedy"
            assert model.cfg.decoding.greedy.boosting_tree.key_phrases_file is None
            # The applied config is the mutated copy.
            applied = model.change_decoding_strategy.call_args.args[0]
            assert applied.strategy == "greedy_batch"
            assert applied.greedy.boosting_tree.key_phrases_file == str(path)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def test_failure_cleans_temp_file_and_returns_none(
        self, nemo_engine_cls, tmp_path, monkeypatch
    ):
        engine = _bare_engine(nemo_engine_cls)
        model = _model_without_boosting()  # greedy lacks boosting_tree -> raises

        created: list[Path] = []
        real_tmp = importlib.import_module("tempfile").NamedTemporaryFile

        def tracking_tmp(*args, **kwargs):
            f = real_tmp(*args, **kwargs)
            created.append(Path(f.name))
            return f

        monkeypatch.setattr("tempfile.NamedTemporaryFile", tracking_tmp)

        path = engine._configure_vocabulary_boosting(engine, model, ["Bizzon"])
        assert path is None
        assert len(created) == 1
        assert not created[0].exists(), "temp vocab file must be cleaned up on failure"
        # Live config untouched, no strategy applied.
        assert model.cfg.decoding.strategy == "greedy"
        model.change_decoding_strategy.assert_not_called()

    def test_supports_boosting_probe_and_cache(self, nemo_engine_cls):
        engine = _bare_engine(nemo_engine_cls)
        assert (
            engine._model_supports_boosting(engine, _model_with_boosting(), "m1")
            is True
        )
        assert (
            engine._model_supports_boosting(engine, _model_without_boosting(), "m2")
            is False
        )
        # Cached: a now-different model object for the same id keeps the answer.
        assert (
            engine._model_supports_boosting(engine, _model_without_boosting(), "m1")
            is True
        )
