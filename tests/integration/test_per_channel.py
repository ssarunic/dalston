"""Integration tests for per-channel speaker detection pipeline.

Tests the full per-channel flow: DAG building, previous_outputs key
normalization, dependency wiring, and merge task assembly.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from dalston.common.models import Task, TaskStatus
from dalston.orchestrator.dag import build_task_dag


class TestPerChannelDAG:
    """Tests for per_channel DAG structure and dependencies."""

    @pytest.fixture
    def job_id(self):
        return uuid4()

    @pytest.fixture
    def audio_uri(self):
        return "s3://test-bucket/audio/stereo.wav"

    def test_per_channel_dag_creates_correct_stages(self, job_id, audio_uri):
        """per_channel mode creates prepare, transcribe_ch0/1, align_ch0/1, merge."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" in stages
        assert "align_ch1" in stages
        assert "merge" in stages
        assert len(tasks) == 6

    def test_per_channel_dag_without_alignment(self, job_id, audio_uri):
        """per_channel without word timestamps skips align tasks."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "per_channel",
                "timestamps_granularity": "segment",
            },
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" not in stages
        assert "align_ch1" not in stages
        assert "merge" in stages
        assert len(tasks) == 4  # prepare + 2 transcribe + merge

    def test_per_channel_transcribe_depends_on_prepare(self, job_id, audio_uri):
        """Both transcribe_ch tasks depend on prepare."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id

        assert by_stage["transcribe_ch0"].dependencies == [prepare_id]
        assert by_stage["transcribe_ch1"].dependencies == [prepare_id]

    def test_per_channel_align_depends_on_its_transcribe(self, job_id, audio_uri):
        """Each align_chN depends on its corresponding transcribe_chN."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}

        assert by_stage["align_ch0"].dependencies == [by_stage["transcribe_ch0"].id]
        assert by_stage["align_ch1"].dependencies == [by_stage["transcribe_ch1"].id]

    def test_merge_depends_on_all_channel_tasks(self, job_id, audio_uri):
        """Merge task depends on prepare and ALL per-channel tasks."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        # Merge must depend on prepare + both transcribe + both align
        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe_ch0"].id in merge_deps
        assert by_stage["transcribe_ch1"].id in merge_deps
        assert by_stage["align_ch0"].id in merge_deps
        assert by_stage["align_ch1"].id in merge_deps
        assert len(merge_deps) == 5

    def test_merge_depends_on_transcribe_when_no_alignment(self, job_id, audio_uri):
        """Without alignment, merge depends on prepare + both transcribe tasks."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "per_channel",
                "timestamps_granularity": "segment",
            },
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe_ch0"].id in merge_deps
        assert by_stage["transcribe_ch1"].id in merge_deps
        assert len(merge_deps) == 3

    def test_merge_config_has_per_channel_metadata(self, job_id, audio_uri):
        """Merge task config contains per_channel speaker_detection and channel_count."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}
        merge_config = by_stage["merge"].config

        assert merge_config["speaker_detection"] == "per_channel"
        assert merge_config["channel_count"] == 2

    def test_per_channel_transcribe_config_includes_channel(self, job_id, audio_uri):
        """Each transcribe_chN has the channel index in its config."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe_ch0"].config["channel"] == 0
        assert by_stage["transcribe_ch1"].config["channel"] == 1

    def test_prepare_config_has_split_channels(self, job_id, audio_uri):
        """Prepare task has split_channels=True for per_channel mode."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["prepare"].config.get("split_channels") is True


class TestPerChannelPreviousOutputs:
    """Tests that _gather_previous_outputs normalizes per-channel keys."""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.s3_bucket = "test-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = "http://localhost:9000"
        return settings

    @pytest.mark.asyncio
    async def test_gather_normalizes_channel_stage_names(self, mock_settings):
        """transcribe_ch0 output is available under both 'transcribe_ch0' and 'transcribe' keys."""
        from dalston.orchestrator.handlers import _gather_previous_outputs

        job_id = uuid4()
        transcribe_ch0_id = uuid4()
        prepare_id = uuid4()

        # Mock TaskModel objects
        transcribe_ch0_task = MagicMock()
        transcribe_ch0_task.id = transcribe_ch0_id
        transcribe_ch0_task.job_id = job_id
        transcribe_ch0_task.stage = "transcribe_ch0"

        prepare_task = MagicMock()
        prepare_task.id = prepare_id
        prepare_task.job_id = job_id
        prepare_task.stage = "prepare"

        task_by_id = {
            transcribe_ch0_id: transcribe_ch0_task,
            prepare_id: prepare_task,
        }

        transcribe_output = {
            "data": {
                "text": "hello world",
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
                "language": "en",
            }
        }
        prepare_output = {
            "data": {
                "duration": 6.0,
                "channels": 2,
                "channel_files": [],
            }
        }

        async def mock_get_task_output(job_id, task_id, settings):
            if task_id == transcribe_ch0_id:
                return transcribe_output
            if task_id == prepare_id:
                return prepare_output
            return None

        with patch(
            "dalston.orchestrator.handlers.get_task_output",
            side_effect=mock_get_task_output,
        ):
            result = await _gather_previous_outputs(
                dependency_ids=[prepare_id, transcribe_ch0_id],
                task_by_id=task_by_id,
                settings=mock_settings,
            )

        # Full key is present
        assert "transcribe_ch0" in result
        # Normalized base key is also present
        assert "transcribe" in result
        # Both point to the same data
        assert result["transcribe"] == result["transcribe_ch0"]
        # Non-channel stage is not duplicated
        assert "prepare" in result

    @pytest.mark.asyncio
    async def test_gather_does_not_normalize_non_channel_stages(self, mock_settings):
        """Standard stage names like 'transcribe' are not duplicated."""
        from dalston.orchestrator.handlers import _gather_previous_outputs

        job_id = uuid4()
        transcribe_id = uuid4()

        transcribe_task = MagicMock()
        transcribe_task.id = transcribe_id
        transcribe_task.job_id = job_id
        transcribe_task.stage = "transcribe"

        task_by_id = {transcribe_id: transcribe_task}

        async def mock_get_task_output(job_id, task_id, settings):
            return {"data": {"text": "hello"}}

        with patch(
            "dalston.orchestrator.handlers.get_task_output",
            side_effect=mock_get_task_output,
        ):
            result = await _gather_previous_outputs(
                dependency_ids=[transcribe_id],
                task_by_id=task_by_id,
                settings=mock_settings,
            )

        assert "transcribe" in result
        assert len(result) == 1  # No duplicate key

    @pytest.mark.asyncio
    async def test_merge_receives_both_channel_and_base_keys(self, mock_settings):
        """Merge task gets transcribe_ch0, transcribe_ch1, align_ch0, align_ch1, and base keys."""
        from dalston.orchestrator.handlers import _gather_previous_outputs

        job_id = uuid4()
        ids = {
            "prepare": uuid4(),
            "transcribe_ch0": uuid4(),
            "transcribe_ch1": uuid4(),
            "align_ch0": uuid4(),
            "align_ch1": uuid4(),
        }

        task_by_id = {}
        for stage, task_id in ids.items():
            t = MagicMock()
            t.id = task_id
            t.job_id = job_id
            t.stage = stage
            task_by_id[task_id] = t

        async def mock_get_task_output(job_id, task_id, settings):
            for stage, tid in ids.items():
                if task_id == tid:
                    return {"data": {"stage": stage, "segments": []}}
            return None

        with patch(
            "dalston.orchestrator.handlers.get_task_output",
            side_effect=mock_get_task_output,
        ):
            result = await _gather_previous_outputs(
                dependency_ids=list(ids.values()),
                task_by_id=task_by_id,
                settings=mock_settings,
            )

        # All original keys present
        for stage in ids:
            assert stage in result

        # Base keys also present for channel stages
        assert "transcribe" in result
        assert "align" in result
        assert "prepare" in result

        # Base keys point to last-processed channel's data (both channels produce them)
        assert result["transcribe"]["stage"] in ("transcribe_ch0", "transcribe_ch1")
        assert result["align"]["stage"] in ("align_ch0", "align_ch1")


class TestPerChannelTaskCompletion:
    """Tests that task error is cleared on successful retry."""

    @pytest.mark.asyncio
    async def test_task_error_cleared_on_completion(self):
        """handle_task_completed clears task.error from a previous failed attempt."""
        from dalston.orchestrator.handlers import handle_task_completed

        task_id = uuid4()
        job_id = uuid4()

        # Mock task that previously failed (has error set) but is now completing
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.job_id = job_id
        mock_task.stage = "transcribe_ch0"
        mock_task.status = TaskStatus.READY.value
        mock_task.error = "Engine restarted with task in-flight"
        mock_task.dependencies = []

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_task)

        # No other tasks in the job (just this one for simplicity)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_settings = MagicMock()

        await handle_task_completed(task_id, mock_db, mock_redis, mock_settings)

        # Error should be cleared
        assert mock_task.error is None
        assert mock_task.status == TaskStatus.COMPLETED.value
