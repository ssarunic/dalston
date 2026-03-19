"""Unit tests for the NeMo Sortformer diarization engine.

Tests the engine logic with mocked NeMo model — the actual
SortformerEncLabelModel is not required.

Run with: pytest tests/unit/test_nemo_sortformer_engine.py -v
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dalston.common.pipeline_types import DiarizationResponse, SpeakerTurn
from dalston.engine_sdk import BatchTaskContext, TaskRequest, TaskResponse

# ---------------------------------------------------------------------------
# Module loader (engine lives outside the dalston package)
# ---------------------------------------------------------------------------


def _load_sortformer_engine():
    """Load NemoSortformerEngine from engines directory.

    Stubs the ``nemo`` package before loading so the engine module
    can be imported without a full NeMo installation.
    """
    engine_path = Path("engines/stt-diarize/nemo-sortformer/engine.py")
    if not engine_path.exists():
        pytest.skip("nemo-sortformer engine not found")

    # Stub nemo imports so the module can be loaded
    nemo_stub = types.ModuleType("nemo")
    nemo_collections = types.ModuleType("nemo.collections")
    nemo_asr = types.ModuleType("nemo.collections.asr")
    nemo_models = types.ModuleType("nemo.collections.asr.models")
    nemo_models.SortformerEncLabelModel = MagicMock()

    sys.modules.setdefault("nemo", nemo_stub)
    sys.modules.setdefault("nemo.collections", nemo_collections)
    sys.modules.setdefault("nemo.collections.asr", nemo_asr)
    sys.modules.setdefault("nemo.collections.asr.models", nemo_models)

    spec = importlib.util.spec_from_file_location("nemo_sortformer_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load nemo-sortformer engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["nemo_sortformer_engine"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def engine_module():
    return _load_sortformer_engine()


@pytest.fixture()
def make_ctx():
    """Create a BatchTaskContext for testing."""

    def _make(task_id: str = "task-1", job_id: str = "job-1") -> BatchTaskContext:
        return BatchTaskContext(
            engine_id="nemo-sortformer",
            instance="test-instance",
            task_id=task_id,
            job_id=job_id,
            stage="diarize",
        )

    return _make


# =========================================================================
# Model Registry Tests
# =========================================================================


class TestModelRegistry:
    def test_default_model_in_registry(self, engine_module):
        assert engine_module.DEFAULT_MODEL_ID in engine_module.MODEL_REGISTRY

    def test_all_registry_entries_have_hf_names(self, engine_module):
        for model_id, hf_name in engine_module.MODEL_REGISTRY.items():
            assert hf_name, f"Empty HF name for {model_id}"
            assert "nvidia/" in hf_name.lower() or "diar" in hf_name.lower()


# =========================================================================
# Engine Initialization Tests
# =========================================================================


class TestEngineInit:
    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "true", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_disabled_mode(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        assert engine._disabled is True

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_cpu_device(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        assert engine._device == "cpu"

    @patch.dict(
        "os.environ",
        {
            "DALSTON_DIARIZATION_DISABLED": "",
            "DALSTON_DEVICE": "",
            "DALSTON_NEMO_ALLOW_CPU": "",
        },
        clear=False,
    )
    def test_requires_gpu_or_allow_cpu(self, engine_module):
        """Without GPU and without NEMO_ALLOW_CPU, init should raise."""
        with pytest.raises(RuntimeError, match="requires GPU"):
            engine_module.NemoSortformerEngine()


# =========================================================================
# Mock Output Tests
# =========================================================================


class TestMockOutput:
    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "true", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_disabled_returns_mock(self, engine_module, make_ctx):
        engine = engine_module.NemoSortformerEngine()
        inp = TaskRequest(task_id="t1", job_id="j1", stage="diarize", config={})
        result = engine.process(inp, make_ctx())

        assert isinstance(result, TaskResponse)
        data = result.data
        assert isinstance(data, DiarizationResponse)
        assert data.skipped is True
        assert data.engine_id == "nemo-sortformer"
        assert data.num_speakers == 1


# =========================================================================
# Processing Logic Tests
# =========================================================================


class TestProcessing:
    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_missing_loaded_model_id_raises(self, engine_module, make_ctx):
        engine = engine_module.NemoSortformerEngine()
        inp = TaskRequest(task_id="t1", job_id="j1", stage="diarize", config={})
        with pytest.raises(ValueError, match="loaded_model_id"):
            engine.process(inp, make_ctx())

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_max_speakers_over_4_raises(self, engine_module, make_ctx):
        engine = engine_module.NemoSortformerEngine()
        inp = TaskRequest(
            task_id="t1",
            job_id="j1",
            stage="diarize",
            config={
                "loaded_model_id": "nvidia/diar-sortformer-4spk-v2.1",
                "max_speakers": 8,
            },
        )
        with pytest.raises(ValueError, match="at most 4 speakers"):
            engine.process(inp, make_ctx())

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_unknown_model_id_raises(self, engine_module, make_ctx):
        engine = engine_module.NemoSortformerEngine()
        inp = TaskRequest(
            task_id="t1",
            job_id="j1",
            stage="diarize",
            config={"loaded_model_id": "unknown/model"},
            audio_path=Path("/tmp/test.wav"),
        )
        with patch.object(engine, "_get_audio_duration", return_value=10.0):
            with pytest.raises(ValueError, match="Unsupported loaded_model_id"):
                engine.process(inp, make_ctx())

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_successful_diarization(self, engine_module, make_ctx):
        """Test full processing with a mocked NeMo model."""
        engine = engine_module.NemoSortformerEngine()

        # Mock model that returns "start end speaker_N" strings
        mock_model = MagicMock()
        mock_model.diarize.return_value = [
            [
                "0.0 2.5 speaker_0",
                "2.5 5.0 speaker_1",
                "4.8 7.0 speaker_0",
            ]
        ]
        mock_model.eval.return_value = mock_model

        # Patch model loading and audio duration
        engine._models["nvidia/diar-sortformer-4spk-v2.1"] = mock_model

        inp = TaskRequest(
            task_id="t1",
            job_id="j1",
            stage="diarize",
            config={"loaded_model_id": "nvidia/diar-sortformer-4spk-v2.1"},
            audio_path=Path("/tmp/test.wav"),
        )

        with patch.object(engine, "_get_audio_duration", return_value=7.0):
            result = engine.process(inp, make_ctx())

        assert isinstance(result, TaskResponse)
        data = result.data
        assert isinstance(data, DiarizationResponse)
        assert data.num_speakers == 2
        assert data.speakers == ["SPEAKER_00", "SPEAKER_01"]
        assert len(data.turns) == 3
        assert data.skipped is False
        assert data.engine_id == "nemo-sortformer"
        # Overlap between (4.8, 5.0) where SPEAKER_01 and SPEAKER_00 overlap
        assert data.overlap_duration > 0


# =========================================================================
# Overlap Calculation Tests
# =========================================================================


class TestOverlapCalculation:
    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_no_overlap(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        turns = [
            SpeakerTurn(start=0.0, end=2.0, speaker="SPEAKER_00"),
            SpeakerTurn(start=2.0, end=4.0, speaker="SPEAKER_01"),
        ]
        duration, ratio = engine._calculate_overlap_stats(turns, 4.0)
        assert duration == 0.0
        assert ratio == 0.0

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_with_overlap(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        turns = [
            SpeakerTurn(start=0.0, end=3.0, speaker="SPEAKER_00"),
            SpeakerTurn(start=2.0, end=5.0, speaker="SPEAKER_01"),
        ]
        duration, ratio = engine._calculate_overlap_stats(turns, 5.0)
        assert duration == 1.0  # 2.0 to 3.0
        assert ratio == 0.2  # 1.0 / 5.0

    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_empty_turns(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        duration, ratio = engine._calculate_overlap_stats([], 10.0)
        assert duration == 0.0
        assert ratio == 0.0


# =========================================================================
# Health Check Tests
# =========================================================================


class TestHealthCheck:
    @patch.dict(
        "os.environ",
        {"DALSTON_DIARIZATION_DISABLED": "true", "DALSTON_DEVICE": "cpu"},
        clear=False,
    )
    def test_health_check_disabled(self, engine_module):
        engine = engine_module.NemoSortformerEngine()
        health = engine.health_check()
        assert health["status"] == "healthy"
        assert health["diarization_disabled"] is True
        assert health["max_speakers"] == 4
        assert "available_loaded_models" in health
