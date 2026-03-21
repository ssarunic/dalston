"""Integration tests for the linear pipeline DAG.

Tests that the DAG builder produces the correct task structure:
- No merge stage in any pipeline (orchestrator assembles transcript.json)
- Sequential diarize (depends on transcribe/align, not parallel)
- Terminal stage is deterministic
"""

from uuid import uuid4

import pytest

from tests.dag_test_helpers import build_task_dag_for_test


@pytest.fixture
def job_id():
    return uuid4()


@pytest.fixture
def audio_uri():
    return "s3://test-bucket/audio/test.wav"


# ---------------------------------------------------------------------------
# No merge in mono pipelines
# ---------------------------------------------------------------------------


class TestPipelineNoMerge:
    """Tests that mono pipelines omit the merge stage."""

    def test_default_pipeline_no_merge(self, job_id, audio_uri):
        """Default pipeline: prepare → transcribe → align (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages
        assert stages == ["prepare", "transcribe", "align"]

    def test_segment_only_pipeline_no_merge(self, job_id, audio_uri):
        """Segment-only pipeline: prepare → transcribe (no merge, no align)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"timestamps_granularity": "segment"},
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages
        assert "align" not in stages
        assert stages == ["prepare", "transcribe"]

    def test_diarize_pipeline_no_merge(self, job_id, audio_uri):
        """Diarize pipeline: prepare → transcribe → align → diarize (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages
        assert stages == ["prepare", "transcribe", "align", "diarize"]

    def test_diarize_without_align_no_merge(self, job_id, audio_uri):
        """Diarize without align: prepare → transcribe → diarize (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages
        assert "align" not in stages
        assert stages == ["prepare", "transcribe", "diarize"]


# ---------------------------------------------------------------------------
# Sequential diarize
# ---------------------------------------------------------------------------


class TestSequentialDiarize:
    """Tests that diarize runs sequentially after transcribe/align."""

    def test_diarize_depends_only_on_prepare(self, job_id, audio_uri):
        """Diarize depends only on prepare — runs in parallel with transcribe/align."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["diarize"].dependencies == [by_stage["prepare"].id]

    def test_diarize_depends_only_on_prepare_without_align(self, job_id, audio_uri):
        """Without alignment, diarize still depends only on prepare."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["diarize"].dependencies == [by_stage["prepare"].id]


# ---------------------------------------------------------------------------
# Per-channel pipelines have no merge
# ---------------------------------------------------------------------------


class TestPerChannelNoMerge:
    """Tests that per_channel pipelines do not use merge."""

    def test_per_channel_no_merge(self, job_id, audio_uri):
        """Per-channel pipelines do not include merge."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages


# ---------------------------------------------------------------------------
# Terminal stage determinism
# ---------------------------------------------------------------------------


class TestTerminalStageDeterminism:
    """Tests that the terminal stage is deterministic and independent of timing."""

    def test_terminal_stage_is_last_task(self, job_id, audio_uri):
        """The last task in the DAG is the terminal stage."""
        for params, expected_last in [
            ({}, "align"),
            ({"timestamps_granularity": "segment"}, "transcribe"),
            ({"speaker_detection": "diarize"}, "diarize"),
            (
                {
                    "speaker_detection": "diarize",
                    "timestamps_granularity": "segment",
                },
                "diarize",
            ),
        ]:
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters=params,
            )
            assert tasks[-1].stage == expected_last, (
                f"params={params}, expected={expected_last}, got={tasks[-1].stage}"
            )

    def test_terminal_stage_independent_of_task_order(self, job_id, audio_uri):
        """Multiple DAG builds produce the same terminal stage."""
        results = set()
        for _ in range(5):
            tasks = build_task_dag_for_test(
                job_id=uuid4(),
                audio_uri=audio_uri,
                parameters={"speaker_detection": "diarize"},
            )
            results.add(tasks[-1].stage)

        assert len(results) == 1, f"Terminal stage varied: {results}"
        assert results.pop() == "diarize"

    def test_pii_stages_not_in_pipeline(self, job_id, audio_uri):
        """PII is post-processing; pii_detect is not a DAG task."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"pii_detection": True},
        )

        stages = [t.stage for t in tasks]
        assert "pii_detect" not in stages
        assert "merge" not in stages


# ---------------------------------------------------------------------------
# Dependency chain correctness
# ---------------------------------------------------------------------------


class TestDependencyChain:
    """Tests correct dependency wiring."""

    def test_full_chain_dependencies(self, job_id, audio_uri):
        """Verify dependency graph: prepare → transcribe → align, prepare → diarize."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}

        assert by_stage["prepare"].dependencies == []
        assert by_stage["transcribe"].dependencies == [by_stage["prepare"].id]
        assert by_stage["align"].dependencies == [by_stage["transcribe"].id]
        assert by_stage["diarize"].dependencies == [by_stage["prepare"].id]

    def test_prepare_always_first(self, job_id, audio_uri):
        """Prepare is always the first task with no dependencies."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )
        assert tasks[0].stage == "prepare"
        assert tasks[0].dependencies == []
