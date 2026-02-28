"""Integration tests for speaker detection modes (diarize and per_channel).

Tests DAG structure, dependency wiring, and merge configuration for all
speaker_detection modes: none, diarize, and per_channel.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from dalston.gateway.models.requests import TranscriptionCreateParams
from dalston.orchestrator.dag import build_task_dag


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
        """Diarize mode creates prepare, transcribe, align, diarize, merge."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        stages = [t.stage for t in tasks]
        assert stages == ["prepare", "diarize", "transcribe", "align", "merge"]

    def test_diarize_dag_without_alignment(self, job_id, audio_uri):
        """Diarize without word timestamps skips align stage."""
        tasks = build_task_dag(
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
        assert "merge" in stages
        assert len(tasks) == 4

    def test_diarize_runs_parallel_to_transcribe(self, job_id, audio_uri):
        """Diarize and transcribe both depend only on prepare (run in parallel)."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id

        assert by_stage["transcribe"].dependencies == [prepare_id]
        assert by_stage["diarize"].dependencies == [prepare_id]

    def test_align_depends_on_transcribe(self, job_id, audio_uri):
        """Align stage depends on transcribe."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["align"].dependencies == [by_stage["transcribe"].id]

    def test_merge_depends_on_prepare_transcribe_align_and_diarize(
        self, job_id, audio_uri
    ):
        """Merge depends on prepare, transcribe, align, and diarize."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe"].id in merge_deps
        assert by_stage["align"].id in merge_deps
        assert by_stage["diarize"].id in merge_deps
        assert len(merge_deps) == 4

    def test_merge_depends_on_transcribe_and_diarize_without_align(
        self, job_id, audio_uri
    ):
        """Without alignment, merge depends on prepare, transcribe, diarize."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "speaker_detection": "diarize",
                "timestamps_granularity": "segment",
            },
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe"].id in merge_deps
        assert by_stage["diarize"].id in merge_deps
        assert len(merge_deps) == 3

    def test_merge_config_has_diarize_speaker_detection(self, job_id, audio_uri):
        """Merge task config records speaker_detection=diarize."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["merge"].config["speaker_detection"] == "diarize"

    def test_diarize_uses_correct_engine(self, job_id, audio_uri):
        """Diarize task uses the pyannote engine."""
        tasks = build_task_dag(
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
        tasks = build_task_dag(
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
        tasks = build_task_dag(
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
        tasks = build_task_dag(
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
        tasks = build_task_dag(
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
        """Default mode (none) does not include diarize stage."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages
        assert "transcribe_ch0" not in stages
        assert stages == ["prepare", "transcribe", "align", "merge"]

    def test_default_merge_config_has_none_speaker_detection(self, job_id, audio_uri):
        """Default merge config has speaker_detection=none."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["merge"].config["speaker_detection"] == "none"

    def test_default_merge_depends_on_prepare_transcribe_and_align(
        self, job_id, audio_uri
    ):
        """Default merge depends on prepare, transcribe, and align."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe"].id in merge_deps
        assert by_stage["align"].id in merge_deps
        assert len(merge_deps) == 3

    def test_invalid_speaker_detection_defaults_to_none(self, job_id, audio_uri):
        """Unknown speaker_detection value falls back to 'none' mode."""
        tasks = build_task_dag(
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
            tasks = build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            prepare = [t for t in tasks if t.stage == "prepare"]
            assert len(prepare) == 1, f"mode={mode}"
            assert prepare[0].dependencies == [], f"mode={mode}"

    def test_all_modes_end_with_merge(self, job_id, audio_uri):
        """Every mode ends with a merge task."""
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            assert tasks[-1].stage == "merge", f"mode={mode}"

    def test_all_modes_include_speaker_detection_in_merge_config(
        self, job_id, audio_uri
    ):
        """Merge config always records the speaker_detection mode."""
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            merge = tasks[-1]
            assert merge.config["speaker_detection"] == mode, f"mode={mode}"

    def test_per_channel_has_most_tasks_with_alignment(self, job_id, audio_uri):
        """per_channel mode produces the most tasks (parallel channels)."""
        counts = {}
        for mode in ("none", "diarize", "per_channel"):
            tasks = build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            counts[mode] = len(tasks)

        assert counts["none"] == 4  # prepare, transcribe, align, merge
        assert counts["diarize"] == 5  # + diarize
        assert counts["per_channel"] == 6  # prepare, 2x transcribe, 2x align, merge

    def test_prepare_only_splits_channels_for_per_channel(self, job_id, audio_uri):
        """Only per_channel mode sets split_channels on prepare."""
        for mode in ("none", "diarize"):
            tasks = build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"speaker_detection": mode},
            )
            prepare = tasks[0]
            assert prepare.config.get("split_channels") is not True, f"mode={mode}"

        tasks = build_task_dag(
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
        """Default per_channel DAG creates 2 channels."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" in stages
        assert "align_ch1" in stages
        assert len(tasks) == 6  # prepare + 2 transcribe + 2 align + merge

    def test_per_channel_dag_respects_num_channels_parameter(self, job_id, audio_uri):
        """num_channels=3 creates 3 transcribe + 3 align tasks."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "num_channels": 3},
        )

        stages = [t.stage for t in tasks]
        for ch in range(3):
            assert f"transcribe_ch{ch}" in stages
            assert f"align_ch{ch}" in stages

        # prepare + 3 transcribe + 3 align + merge = 8
        assert len(tasks) == 8

        # Merge config should reflect 3 channels
        merge = tasks[-1]
        assert merge.config["channel_count"] == 3

    def test_per_channel_single_channel_uses_ch0_naming(self, job_id, audio_uri):
        """num_channels=1 creates transcribe_ch0 (not transcribe), consistent naming."""
        tasks = build_task_dag(
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
        assert len(tasks) == 4  # prepare + transcribe_ch0 + align_ch0 + merge

    def test_per_channel_three_channels_merge_depends_on_all(self, job_id, audio_uri):
        """Merge depends on prepare and all 3 transcribe + 3 align tasks."""
        tasks = build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "num_channels": 3},
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        for ch in range(3):
            assert by_stage[f"transcribe_ch{ch}"].id in merge_deps
            assert by_stage[f"align_ch{ch}"].id in merge_deps

        # prepare + 3 transcribe + 3 align = 7
        assert len(merge_deps) == 7


# ---------------------------------------------------------------------------
# Speaker count validation
# ---------------------------------------------------------------------------


class TestSpeakerCountValidation:
    """Tests that min_speakers > max_speakers is rejected at the API model level."""

    def test_min_speakers_greater_than_max_speakers_rejected(self):
        """min_speakers > max_speakers raises ValidationError."""
        with pytest.raises(
            ValidationError, match="min_speakers.*must not exceed.*max_speakers"
        ):
            TranscriptionCreateParams(min_speakers=5, max_speakers=2)

    def test_min_speakers_equal_to_max_speakers_accepted(self):
        """min_speakers == max_speakers is valid (exact speaker count)."""
        params = TranscriptionCreateParams(min_speakers=3, max_speakers=3)
        assert params.min_speakers == 3
        assert params.max_speakers == 3

    def test_min_speakers_less_than_max_speakers_accepted(self):
        """min_speakers < max_speakers is the normal valid range."""
        params = TranscriptionCreateParams(min_speakers=2, max_speakers=5)
        assert params.min_speakers == 2
        assert params.max_speakers == 5

    def test_only_min_speakers_accepted(self):
        """Only min_speakers set is valid."""
        params = TranscriptionCreateParams(min_speakers=2)
        assert params.min_speakers == 2
        assert params.max_speakers is None

    def test_only_max_speakers_accepted(self):
        """Only max_speakers set is valid."""
        params = TranscriptionCreateParams(max_speakers=5)
        assert params.min_speakers is None
        assert params.max_speakers == 5

    def test_neither_speakers_set_accepted(self):
        """Neither min nor max set is valid (auto-detect)."""
        params = TranscriptionCreateParams()
        assert params.min_speakers is None
        assert params.max_speakers is None

    def test_to_job_parameters_preserves_valid_range(self):
        """Valid speaker range propagates through to_job_parameters()."""
        params = TranscriptionCreateParams(
            min_speakers=2, max_speakers=5, speaker_detection="diarize"
        )
        job_params = params.to_job_parameters()
        assert job_params["min_speakers"] == 2
        assert job_params["max_speakers"] == 5
