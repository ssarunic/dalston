"""Integration tests for M68 linear pipeline DAG.

Tests that the DAG builder produces the correct task structure when
linear_pipeline=True, including:
- No merge stage in mono pipelines
- Sequential diarize (depends on transcribe/align instead of parallel)
- Terminal stage is deterministic
- Per-channel pipelines still use merge
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
# Linear pipeline – no merge in mono pipelines
# ---------------------------------------------------------------------------


class TestLinearPipelineNoMerge:
    """Tests that linear pipeline omits merge for mono pipelines."""

    def test_default_pipeline_no_merge(self, job_id, audio_uri):
        """Default linear pipeline: prepare → transcribe → align (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            linear_pipeline=True,
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
            linear_pipeline=True,
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
            linear_pipeline=True,
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
            linear_pipeline=True,
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages
        assert "align" not in stages
        assert stages == ["prepare", "transcribe", "diarize"]


# ---------------------------------------------------------------------------
# Linear pipeline – sequential diarize
# ---------------------------------------------------------------------------


class TestLinearPipelineSequentialDiarize:
    """Tests that diarize is sequential in linear pipeline mode."""

    def test_diarize_depends_on_align(self, job_id, audio_uri):
        """In linear mode, diarize depends on prepare and align."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            linear_pipeline=True,
        )

        by_stage = {t.stage: t for t in tasks}
        diarize_deps = set(by_stage["diarize"].dependencies)

        assert by_stage["prepare"].id in diarize_deps
        assert by_stage["align"].id in diarize_deps
        assert len(diarize_deps) == 2

    def test_diarize_depends_on_transcribe_when_no_align(self, job_id, audio_uri):
        """Without alignment, diarize depends on prepare and transcribe."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
            linear_pipeline=True,
        )

        by_stage = {t.stage: t for t in tasks}
        diarize_deps = set(by_stage["diarize"].dependencies)

        assert by_stage["prepare"].id in diarize_deps
        assert by_stage["transcribe"].id in diarize_deps
        assert len(diarize_deps) == 2


# ---------------------------------------------------------------------------
# Legacy pipeline – backwards compatibility
# ---------------------------------------------------------------------------


class TestLegacyPipelineUnchanged:
    """Tests that legacy pipeline (linear_pipeline=False) is unchanged."""

    def test_default_legacy_pipeline(self, job_id, audio_uri):
        """Legacy pipeline still has merge."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            linear_pipeline=False,
        )

        stages = [t.stage for t in tasks]
        assert stages == ["prepare", "transcribe", "align", "merge"]

    def test_diarize_legacy_pipeline_parallel(self, job_id, audio_uri):
        """Legacy diarize still runs parallel to transcribe."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            linear_pipeline=False,
        )

        by_stage = {t.stage: t for t in tasks}
        # In legacy mode, diarize depends only on prepare (parallel to transcribe)
        assert by_stage["diarize"].dependencies == [by_stage["prepare"].id]
        # Merge stage is present
        assert "merge" in by_stage

    def test_legacy_merge_depends_on_all(self, job_id, audio_uri):
        """Legacy merge depends on prepare, transcribe, align, diarize."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            linear_pipeline=False,
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)
        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe"].id in merge_deps
        assert by_stage["align"].id in merge_deps
        assert by_stage["diarize"].id in merge_deps


# ---------------------------------------------------------------------------
# Per-channel pipelines always use merge
# ---------------------------------------------------------------------------


class TestPerChannelAlwaysMerge:
    """Tests that per_channel pipelines use merge regardless of linear_pipeline flag."""

    def test_per_channel_has_merge_in_linear_mode(self, job_id, audio_uri):
        """Per-channel pipelines always include merge (even in linear mode)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
            linear_pipeline=True,
        )

        stages = [t.stage for t in tasks]
        assert "merge" in stages

    def test_per_channel_has_merge_in_legacy_mode(self, job_id, audio_uri):
        """Per-channel pipelines have merge in legacy mode."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
            linear_pipeline=False,
        )

        stages = [t.stage for t in tasks]
        assert "merge" in stages


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
                linear_pipeline=True,
            )
            assert tasks[-1].stage == expected_last, (
                f"params={params}, expected={expected_last}, got={tasks[-1].stage}"
            )

    def test_terminal_stage_independent_of_task_order(self, job_id, audio_uri):
        """Multiple DAG builds produce the same terminal stage."""
        results = set()
        for _ in range(5):
            tasks = build_task_dag_for_test(
                job_id=uuid4(),  # Different job IDs
                audio_uri=audio_uri,
                parameters={"speaker_detection": "diarize"},
                linear_pipeline=True,
            )
            results.add(tasks[-1].stage)

        assert len(results) == 1, f"Terminal stage varied: {results}"
        assert results.pop() == "diarize"

    def test_pii_stages_present_in_linear_pipeline(self, job_id, audio_uri):
        """PII stages can still appear in linear pipeline (pending M67 migration)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"pii_detection": True},
            linear_pipeline=True,
        )

        stages = [t.stage for t in tasks]
        assert "pii_detect" in stages
        assert "merge" not in stages


# ---------------------------------------------------------------------------
# Dependency chain correctness
# ---------------------------------------------------------------------------


class TestLinearPipelineDependencyChain:
    """Tests correct dependency wiring in linear pipeline."""

    def test_full_linear_chain_dependencies(self, job_id, audio_uri):
        """Verify full dependency chain: prepare → transcribe → align → diarize."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            linear_pipeline=True,
        )

        by_stage = {t.stage: t for t in tasks}

        # prepare has no dependencies
        assert by_stage["prepare"].dependencies == []

        # transcribe depends on prepare
        assert by_stage["transcribe"].dependencies == [by_stage["prepare"].id]

        # align depends on transcribe
        assert by_stage["align"].dependencies == [by_stage["transcribe"].id]

        # diarize depends on prepare and align
        diarize_deps = set(by_stage["diarize"].dependencies)
        assert by_stage["prepare"].id in diarize_deps
        assert by_stage["align"].id in diarize_deps

    def test_prepare_always_first(self, job_id, audio_uri):
        """Prepare is always the first task with no dependencies."""
        for linear in [True, False]:
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={},
                linear_pipeline=linear,
            )
            assert tasks[0].stage == "prepare"
            assert tasks[0].dependencies == []
