"""Unit tests for the co-located NeMo + pyannote GPU profiler."""

from __future__ import annotations

import pytest

from dalston.common.pipeline_types import DiarizationRequest
from dalston.tools.profile_colocated_gpu import (
    _aggregate_repeated_cells,
    _parse_float_grid,
    _parse_int_grid,
    _synthetic_cell,
    build_env_recommendation,
    run_profile,
    select_recommendation,
)


def test_parse_int_grid_dedupes_and_sorts() -> None:
    assert _parse_int_grid("4, 1, 2, 2") == [1, 2, 4]


def test_parse_float_grid_dedupes_and_sorts() -> None:
    assert _parse_float_grid("900,300,300") == [300.0, 900.0]


def test_parse_grid_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _parse_int_grid(" , ")


def _cell(
    *,
    batch: int,
    peak: int,
    throughput: float,
    fits: bool = True,
    nemo_inflight: int = 1,
    pyannote_inflight: int = 1,
) -> dict:
    return {
        "params": {
            "nemo_batch_size": batch,
            "pyannote_max_chunk_s": 600.0,
            "nemo_inflight": nemo_inflight,
            "pyannote_inflight": pyannote_inflight,
        },
        "peak_vram_mb": peak,
        "threshold_mb": 13000,
        "throughput_audio_s_per_s": throughput,
        "speedup_vs_realtime": throughput,
        "fits": fits,
    }


def test_select_recommendation_ignores_rejected_cells() -> None:
    best = select_recommendation(
        [
            _cell(batch=1, peak=7000, throughput=40),
            _cell(batch=8, peak=15000, throughput=80, fits=False),
            _cell(batch=4, peak=9000, throughput=60),
        ]
    )
    assert best is not None
    assert best["params"]["nemo_batch_size"] == 4


def test_select_recommendation_requires_all_repeats_to_fit() -> None:
    flaky = _cell(batch=4, peak=9000, throughput=80)
    flaky_failed = _cell(batch=4, peak=14000, throughput=0, fits=False)
    stable = _cell(batch=2, peak=8500, throughput=60)

    best = select_recommendation([flaky, flaky_failed, stable])

    assert best is not None
    assert best["params"]["nemo_batch_size"] == 2


def test_aggregate_repeated_cells_uses_worst_peak_and_slowest_throughput() -> None:
    aggregate = _aggregate_repeated_cells(
        [
            _cell(batch=2, peak=8000, throughput=70),
            _cell(batch=2, peak=8500, throughput=60),
        ]
    )[0]
    assert aggregate["fits"] is True
    assert aggregate["peak_vram_mb"] == 8500
    assert aggregate["throughput_audio_s_per_s"] == 60


def test_select_recommendation_prefers_lower_peak_on_tie() -> None:
    best = select_recommendation(
        [
            _cell(batch=2, peak=9000, throughput=60),
            _cell(batch=4, peak=8000, throughput=60),
        ]
    )
    assert best is not None
    assert best["params"]["nemo_batch_size"] == 4


def test_build_env_recommendation_returns_operator_knobs() -> None:
    best = _cell(
        batch=4,
        peak=9150,
        throughput=70,
        nemo_inflight=1,
        pyannote_inflight=1,
    )
    rec = build_env_recommendation(
        best,
        safety_margin=0.85,
        budget_headroom_mb=500,
    )
    assert rec["status"] == "ok"
    assert rec["env"]["nemo"]["DALSTON_NEMO_BATCH_SIZE"] == "4"
    assert rec["env"]["nemo"]["DALSTON_BATCH_MAX_INFLIGHT"] == "1"
    assert rec["env"]["pyannote"]["DALSTON_MAX_DIARIZE_CHUNK_S"] == "600"
    assert rec["basis"]["measured_total_peak_budget_mb"] == 10000


def test_build_env_recommendation_handles_no_safe_cell() -> None:
    rec = build_env_recommendation(
        None,
        safety_margin=0.85,
        budget_headroom_mb=500,
    )
    assert rec["status"] == "no_safe_cell"
    assert rec["env"] == {}


def test_diarization_request_accepts_calibration_chunk_override() -> None:
    params = DiarizationRequest.model_validate({"max_chunk_s": 300})
    assert params.max_chunk_s == 300


def test_synthetic_cell_marks_over_threshold_as_rejected() -> None:
    cell = _synthetic_cell(
        audio_duration_s=600,
        nemo_batch_size=16,
        pyannote_chunk_s=900,
        nemo_inflight=2,
        pyannote_inflight=2,
        gpu_vram_mb=15360,
        safety_margin=0.5,
    )
    assert cell["fits"] is False
    assert cell["peak_vram_mb"] > cell["threshold_mb"]


def test_run_profile_dry_run_emits_recommendation() -> None:
    profile = run_profile(
        nemo_url="http://nemo",
        pyannote_url="http://pyannote",
        nemo_model="nvidia/parakeet-tdt-0.6b-v3",
        pyannote_model="pyannote/speaker-diarization-community-1",
        audio_duration_s=1.0,
        audio_file=None,
        nemo_batch_sizes=[1, 2],
        pyannote_chunk_sizes=[300.0],
        nemo_inflight_values=[1],
        pyannote_inflight_values=[1],
        repeats=1,
        gpu_id=0,
        safety_margin=0.85,
        budget_headroom_mb=500,
        dry_run=True,
    )
    assert profile["profile_type"] == "colocated_gpu"
    assert profile["recommendation"]["status"] == "ok"
    assert len(profile["measurements"]) == 2
