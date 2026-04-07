"""Tests for the hf-asr-align-pyannote combo engine."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("torch", reason="torch required for combo engine tests")

# Ensure the engine directory is importable
_engine_dir = str(
    Path(__file__).resolve().parents[2]
    / "engines"
    / "stt-transcribe"
    / "hf-asr-align-pyannote"
)


def _load_engine_class():
    """Load HfAsrAlignPyannoteEngine from the engine file via importlib."""
    import importlib.util

    engine_file = Path(_engine_dir) / "engine.py"
    spec = importlib.util.spec_from_file_location(
        "combo_engine",
        engine_file,
        submodule_search_locations=[_engine_dir],
    )
    module = importlib.util.module_from_spec(spec)
    # Temporarily add engine dir for sibling imports (align.py, model_loader.py)
    added = _engine_dir not in sys.path
    if added:
        sys.path.insert(0, _engine_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if added and _engine_dir in sys.path:
            sys.path.remove(_engine_dir)
        # Clean up sibling modules to avoid polluting other tests
        for name in ("align", "model_loader", "ctc_forced_align"):
            sys.modules.pop(name, None)
    return module.HfAsrAlignPyannoteEngine


class TestComboEngineInit:
    """Test engine construction."""

    def test_engine_id(self):
        HfAsrAlignPyannoteEngine = _load_engine_class()

        assert HfAsrAlignPyannoteEngine.ENGINE_ID == "hf-asr-align-pyannote"

    def test_engine_yaml_valid(self):
        """engine.yaml should parse without errors and contain expected fields."""
        import yaml

        yaml_path = Path(_engine_dir) / "engine.yaml"
        with yaml_path.open() as f:
            data = yaml.safe_load(f)

        assert data["schema_version"] == "1.1"
        assert data["engine_id"] == "hf-asr-align-pyannote"
        assert data["stage"] == "transcribe"
        assert data["capabilities"]["word_timestamps"] is True
        assert data["capabilities"]["includes_diarization"] is True


class TestComboEngineProcess:
    """Test the two process() modes."""

    @patch("dalston.engine_sdk.device.detect_device", return_value="cpu")
    def test_http_direct_mode_returns_transcript(self, _mock_device):
        """When job_id == 'http', process() returns a plain Transcript."""
        HfAsrAlignPyannoteEngine = _load_engine_class()

        from dalston.common.pipeline_types import Transcript
        from dalston.engine_sdk.context import BatchTaskContext
        from dalston.engine_sdk.types import TaskRequest, TaskResponse

        mock_transcript = Transcript(
            text="hello world",
            segments=[],
            language="en",
            engine_id="hf-asr-align-pyannote",
        )

        engine = HfAsrAlignPyannoteEngine.__new__(HfAsrAlignPyannoteEngine)
        engine.logger = MagicMock()

        with patch.object(engine, "transcribe_audio", return_value=mock_transcript):
            task_request = TaskRequest(
                task_id="test",
                job_id="http",
                stage="transcribe",
                config={},
            )
            ctx = MagicMock(spec=BatchTaskContext)
            result = engine.process(task_request, ctx)

            assert isinstance(result, TaskResponse)
            assert isinstance(result.data, Transcript)
            assert result.data.text == "hello world"

    @patch("dalston.engine_sdk.device.detect_device", return_value="cpu")
    def test_batch_mode_returns_envelope(self, _mock_device):
        """When job_id != 'http', process() returns multi-key envelope."""
        HfAsrAlignPyannoteEngine = _load_engine_class()

        from dalston.common.pipeline_types import (
            AlignmentResponse,
            DiarizationResponse,
            Segment,
            Transcript,
        )
        from dalston.engine_sdk.context import BatchTaskContext
        from dalston.engine_sdk.types import TaskRequest, TaskResponse

        mock_transcript = Transcript(
            text="hello",
            segments=[],
            language="en",
            engine_id="hf-asr-align-pyannote",
        )
        mock_align = AlignmentResponse(
            text="hello",
            segments=[Segment(start=0.0, end=1.0, text="hello")],
            language="en",
            word_timestamps=True,
            skipped=False,
            engine_id="phoneme-align",
        )
        mock_diarize = DiarizationResponse(
            speakers=["SPEAKER_00"],
            turns=[],
            num_speakers=1,
            skipped=False,
            engine_id="pyannote-4.0",
        )

        engine = HfAsrAlignPyannoteEngine.__new__(HfAsrAlignPyannoteEngine)
        engine.logger = MagicMock()

        with (
            patch.object(engine, "_run_transcribe", return_value=mock_transcript),
            patch.object(engine, "_run_align", return_value=mock_align),
            patch.object(engine, "_run_diarize", return_value=mock_diarize),
        ):
            task_request = TaskRequest(
                task_id="test",
                job_id="job-123",
                stage="transcribe",
                config={},
                audio_path=Path("/tmp/test.wav"),
            )
            ctx = MagicMock(spec=BatchTaskContext)
            result = engine.process(task_request, ctx)

            assert isinstance(result, TaskResponse)
            data = result.to_dict()
            assert "stages_completed" in data
            assert "transcribe" in data["stages_completed"]
            assert "align" in data["stages_completed"]
            assert "diarize" in data["stages_completed"]
            assert "transcribe" in data
            assert "align" in data
            assert "diarize" in data
            # Verify serialized as dicts (not Pydantic objects)
            assert isinstance(data["transcribe"], dict)
            assert "text" in data["transcribe"]
            assert isinstance(data["align"], dict)
            assert isinstance(data["diarize"], dict)

    @patch("dalston.engine_sdk.device.detect_device", return_value="cpu")
    def test_skipped_stages_excluded_from_completed(self, _mock_device):
        """Skipped stages should not appear in stages_completed."""
        HfAsrAlignPyannoteEngine = _load_engine_class()

        from dalston.common.pipeline_types import (
            AlignmentResponse,
            DiarizationResponse,
            Segment,
            TimestampGranularity,
            Transcript,
        )
        from dalston.engine_sdk.context import BatchTaskContext
        from dalston.engine_sdk.types import TaskRequest

        mock_transcript = Transcript(
            text="hello",
            segments=[],
            language="en",
            engine_id="hf-asr-align-pyannote",
        )
        mock_align = AlignmentResponse(
            text="hello",
            segments=[Segment(start=0.0, end=1.0, text="hello")],
            language="en",
            word_timestamps=False,
            skipped=True,
            skip_reason="test",
            granularity_achieved=TimestampGranularity.SEGMENT,
            engine_id="phoneme-align",
        )
        mock_diarize = DiarizationResponse(
            speakers=[],
            turns=[],
            num_speakers=0,
            skipped=True,
            skip_reason="no token",
            engine_id="pyannote-4.0",
        )

        engine = HfAsrAlignPyannoteEngine.__new__(HfAsrAlignPyannoteEngine)
        engine.logger = MagicMock()

        with (
            patch.object(engine, "_run_transcribe", return_value=mock_transcript),
            patch.object(engine, "_run_align", return_value=mock_align),
            patch.object(engine, "_run_diarize", return_value=mock_diarize),
        ):
            task_request = TaskRequest(
                task_id="test",
                job_id="job-456",
                stage="transcribe",
                config={},
                audio_path=Path("/tmp/test.wav"),
            )
            ctx = MagicMock(spec=BatchTaskContext)
            result = engine.process(task_request, ctx)

            data = result.to_dict()
            assert data["stages_completed"] == ["transcribe"]
            # Skipped stages still present in envelope for debugging
            assert "align" in data
            assert "diarize" in data


class TestEnvelopeUnpacking:
    """Test the handlers.py envelope unpacking logic."""

    def test_single_stage_passthrough(self):
        """Normal single-stage output should pass through unchanged."""
        stage_outputs: dict = {}
        completed_stages: list = []

        data = {"text": "hello", "segments": []}
        task_stage = "transcribe"

        # Simulate the handler logic
        if isinstance(data, dict) and "stages_completed" in data:
            for stage_key in data["stages_completed"]:
                if stage_key in data:
                    stage_outputs[stage_key] = data[stage_key]
                    if stage_key not in completed_stages:
                        completed_stages.append(stage_key)
        else:
            stage_outputs[task_stage] = data
            completed_stages.append(task_stage)

        assert stage_outputs == {"transcribe": {"text": "hello", "segments": []}}
        assert completed_stages == ["transcribe"]

    def test_envelope_unpacking(self):
        """Multi-key envelope should be unpacked into separate stage outputs."""
        stage_outputs: dict = {}
        completed_stages: list = []

        data = {
            "stages_completed": ["transcribe", "align", "diarize"],
            "transcribe": {"text": "hello", "segments": []},
            "align": {"text": "hello", "segments": [], "word_timestamps": True},
            "diarize": {"speakers": ["SPEAKER_00"], "turns": []},
        }
        task_stage = "transcribe"

        if isinstance(data, dict) and "stages_completed" in data:
            for stage_key in data["stages_completed"]:
                if stage_key in data:
                    stage_outputs[stage_key] = data[stage_key]
                    if stage_key not in completed_stages:
                        completed_stages.append(stage_key)
        else:
            stage_outputs[task_stage] = data
            completed_stages.append(task_stage)

        assert "transcribe" in stage_outputs
        assert "align" in stage_outputs
        assert "diarize" in stage_outputs
        assert stage_outputs["transcribe"]["text"] == "hello"
        assert stage_outputs["diarize"]["speakers"] == ["SPEAKER_00"]
        assert completed_stages == ["transcribe", "align", "diarize"]

    def test_envelope_with_skipped_stages(self):
        """Envelope with partial stages_completed only unpacks listed stages."""
        stage_outputs: dict = {}
        completed_stages: list = []

        data = {
            "stages_completed": ["transcribe"],
            "transcribe": {"text": "hello"},
            "align": {"skipped": True},
            "diarize": {"skipped": True},
        }

        if isinstance(data, dict) and "stages_completed" in data:
            for stage_key in data["stages_completed"]:
                if stage_key in data:
                    stage_outputs[stage_key] = data[stage_key]
                    if stage_key not in completed_stages:
                        completed_stages.append(stage_key)
        else:
            stage_outputs["transcribe"] = data
            completed_stages.append("transcribe")

        assert list(stage_outputs.keys()) == ["transcribe"]
        assert completed_stages == ["transcribe"]
