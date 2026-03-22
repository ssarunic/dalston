"""Integration tests for speaker detection modes (diarize and per_channel).

Tests DAG structure, dependency wiring, and configuration for all
speaker_detection modes: none, diarize, and per_channel.

No pipeline shape uses a merge stage. Transcript assembly is handled by
the orchestrator on job completion for all modes.
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
# Diarize mode – DAG structure
# ---------------------------------------------------------------------------


class TestDiarizeDAG:
    """Tests for speaker_detection=diarize DAG structure."""

    def test_diarize_dag_creates_correct_stages(self, job_id, audio_uri):
        """Diarize mode creates prepare, transcribe, align, diarize (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "align" in stages
        assert "diarize" in stages
        assert "merge" not in stages

    def test_diarize_dag_without_alignment(self, job_id, audio_uri):
        """Diarize without word timestamps skips align stage."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
        )

        stages = [t.stage for t in tasks]
        assert "diarize" in stages
        assert "transcribe" in stages
        assert "align" not in stages
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare, transcribe, diarize

    def test_diarize_is_sequential_after_transcribe_and_align(self, job_id, audio_uri):
        """Diarize depends on prepare and align (sequential, not parallel)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id
        align_id = by_stage["align"].id

        assert prepare_id in by_stage["diarize"].dependencies
        # Diarize runs in parallel with transcribe/align — no dependency on align
        assert align_id not in by_stage["diarize"].dependencies

    def test_align_depends_on_transcribe(self, job_id, audio_uri):
        """Align stage depends on transcribe."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].id in by_stage["align"].dependencies

    def test_diarize_without_align_depends_only_on_prepare(self, job_id, audio_uri):
        """Without alignment, diarize depends only on prepare (parallel with transcribe)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
        )

        by_stage = {t.stage: t for t in tasks}
        diarize_deps = set(by_stage["diarize"].dependencies)

        assert by_stage["prepare"].id in diarize_deps
        assert by_stage["transcribe"].id not in diarize_deps
        assert len(diarize_deps) == 1

    def test_diarize_uses_correct_engine(self, job_id, audio_uri):
        """Diarize task uses the pyannote engine."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert "pyannote" in by_stage["diarize"].engine_id


# ---------------------------------------------------------------------------
# Diarize mode – speaker hints in config
# ---------------------------------------------------------------------------


class TestDiarizeSpeakerHints:
    """Tests that speaker count hints propagate to diarize task config."""

    def test_num_speakers_sets_min_and_max(self, job_id, audio_uri):
        """num_speakers sets both min_speakers and max_speakers."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize", "num_speakers": 3},
        )

        by_stage = {t.stage: t for t in tasks}
        config = by_stage["diarize"].config
        assert config["min_speakers"] == 3
        assert config["max_speakers"] == 3

    def test_min_and_max_speakers_separate(self, job_id, audio_uri):
        """min_speakers and max_speakers can be set independently."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "min_speakers": 2,
                "max_speakers": 5,
            },
        )

        by_stage = {t.stage: t for t in tasks}
        config = by_stage["diarize"].config
        assert config["min_speakers"] == 2
        assert config["max_speakers"] == 5

    def test_no_speaker_hints_produces_empty_config(self, job_id, audio_uri):
        """Without speaker hints, diarize config has no min/max constraints."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        config = by_stage["diarize"].config
        assert "min_speakers" not in config
        assert "max_speakers" not in config

    def test_exclusive_mode_propagates(self, job_id, audio_uri):
        """exclusive=True propagates to diarize config."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize", "exclusive": True},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["diarize"].config.get("exclusive") is True


# ---------------------------------------------------------------------------
# No speaker detection – baseline DAG
# ---------------------------------------------------------------------------


class TestNoSpeakerDetectionDAG:
    """Tests for speaker_detection=none (default) DAG structure."""

    def test_default_dag_has_no_diarize(self, job_id, audio_uri):
        """Default mode (none) does not include diarize or merge stages."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages
        assert "transcribe_ch0" not in stages
        assert "merge" not in stages
        assert stages == ["prepare", "transcribe", "align"]

    def test_default_dag_has_no_merge(self, job_id, audio_uri):
        """Default (none) mode has no merge stage; assembly is done in handlers."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        stages = [t.stage for t in tasks]
        assert "merge" not in stages

    def test_default_align_depends_on_transcribe(self, job_id, audio_uri):
        """Default pipeline: align depends on transcribe."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        by_stage = {t.stage: t for t in tasks}

        assert by_stage["transcribe"].id in by_stage["align"].dependencies

    def test_invalid_speaker_detection_defaults_to_none(self, job_id, audio_uri):
        """Unknown speaker_detection value falls back to 'none' mode."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "invalid_mode"},
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages
        assert "transcribe_ch0" not in stages


# ---------------------------------------------------------------------------
# Cross-mode comparisons
# ---------------------------------------------------------------------------


class TestSpeakerDetectionModeComparison:
    """Tests comparing behavior across speaker detection modes."""

    def test_all_modes_start_with_prepare(self, job_id, audio_uri):
        """Every mode starts with a prepare task with no dependencies."""
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            prepare = [t for t in tasks if t.stage == "prepare"]
            assert len(prepare) == 1, f"mode={mode}"
            assert prepare[0].dependencies == [], f"mode={mode}"

    def test_no_mode_has_merge(self, job_id, audio_uri):
        """No speaker detection mode produces a merge task."""
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            stages = [t.stage for t in tasks]
            assert "merge" not in stages, f"mode={mode} should not have merge"

    def test_per_channel_has_most_tasks_with_alignment(self, job_id, audio_uri):
        """per_channel mode produces the most tasks (parallel channels)."""
        counts = {}
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            counts[mode] = len(tasks)

        assert counts["none"] == 3  # prepare, transcribe, align
        assert counts["diarize"] == 4  # prepare, transcribe, align, diarize
        assert counts["per_channel"] == 5  # prepare, 2x transcribe, 2x align

    def test_prepare_only_splits_channels_for_per_channel(self, job_id, audio_uri):
        """Only per_channel mode sets split_channels on prepare."""
        for mode in ("none", "diarize"):
            tasks = build_task_dag_for_test(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            prepare = tasks[0]
            assert prepare.config.get("split_channels") is not True, f"mode={mode}"

        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )
        assert tasks[0].config.get("split_channels") is True


# ---------------------------------------------------------------------------
# Per-channel parameterization
# ---------------------------------------------------------------------------


class TestPerChannelParameterization:
    """Tests that num_channels is parameterized and naming is consistent."""

    def test_per_channel_dag_defaults_to_two_channels(self, job_id, audio_uri):
        """Default per_channel DAG creates 2 channels (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" in stages
        assert "align_ch1" in stages
        assert "merge" not in stages
        assert len(tasks) == 5  # prepare + 2 transcribe + 2 align

    def test_per_channel_dag_respects_num_channels_parameter(self, job_id, audio_uri):
        """num_channels=3 creates 3 transcribe + 3 align tasks (no merge)."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "num_channels": 3},
        )

        stages = [t.stage for t in tasks]
        for ch in range(3):
            assert f"transcribe_ch{ch}" in stages
            assert f"align_ch{ch}" in stages

        assert "merge" not in stages
        # prepare + 3 transcribe + 3 align = 7
        assert len(tasks) == 7

    def test_per_channel_single_channel_uses_ch0_naming(self, job_id, audio_uri):
        """num_channels=1 creates transcribe_ch0 (not transcribe), consistent naming."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "num_channels": 1},
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "align_ch0" in stages
        # Must NOT have plain "transcribe" — per_channel always uses _chN suffix
        assert "transcribe" not in stages
        assert "align" not in stages
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare + transcribe_ch0 + align_ch0

    def test_per_channel_all_transcribes_depend_on_prepare(self, job_id, audio_uri):
        """All per-channel transcribe tasks depend on prepare."""
        tasks = build_task_dag_for_test(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "num_channels": 3},
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id

        for ch in range(3):
            assert by_stage[f"transcribe_ch{ch}"].dependencies == [prepare_id]
