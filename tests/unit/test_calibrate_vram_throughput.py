"""Tests for the M89.2 throughput-sweep additions to calibrate_vram.

Covers the argmax picker (89.2.1) and the mode-parsing + merge-write
plumbing that lets a single profile JSON accumulate ``throughput_optimal``
data across multiple modes (89.2.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dalston.tools.calibrate_vram import (
    _compute_recommended_budget_mb,
    _compute_throughput_optimal,
    _merge_throughput_into_existing,
    _parse_mode,
)


def _measurement(
    *,
    vad_batch: int,
    audio_s: int = 60,
    peak_mb: int = 5000,
    elapsed_s: float = 2.0,
    delta_mb: int = 1500,
) -> dict[str, Any]:
    return {
        "params": {"audio_s": audio_s, "vad_batch_size": vad_batch},
        "peak_vram_mb": peak_mb,
        "delta_mb": delta_mb,
        "elapsed_s": elapsed_s,
    }


def test_picks_highest_rtf_among_fitting_cells() -> None:
    measurements = [
        _measurement(vad_batch=1, peak_mb=4000, elapsed_s=4.0),  # rtf=15
        _measurement(vad_batch=4, peak_mb=6000, elapsed_s=2.0),  # rtf=30 ← best
        _measurement(
            vad_batch=8, peak_mb=7500, elapsed_s=1.5
        ),  # rtf=40 but cut by threshold
    ]
    # 15360 MB T4 * 0.50 = 7680 MB threshold → vad_batch=8 (7500MB) fits, RTF=40.
    optimal = _compute_throughput_optimal(
        measurements, gpu_vram_mb=15360, safety_margin=0.5
    )
    assert optimal is not None
    assert optimal["axis"] == "vad_batch_size"
    assert optimal["value"] == 8
    assert optimal["rtf"] == 40.0


def test_threshold_filters_out_oversized_cells() -> None:
    measurements = [
        _measurement(vad_batch=1, peak_mb=4000, elapsed_s=4.0),  # rtf=15, fits
        _measurement(vad_batch=4, peak_mb=6000, elapsed_s=2.0),  # rtf=30, fits
        _measurement(vad_batch=8, peak_mb=14000, elapsed_s=1.5),  # rtf=40 but oversize
    ]
    # 15360 * 0.85 = 13056 MB threshold → vad_batch=8 (14000) excluded; best fitting is vad_batch=4.
    optimal = _compute_throughput_optimal(
        measurements, gpu_vram_mb=15360, safety_margin=0.85
    )
    assert optimal is not None
    assert optimal["value"] == 4
    assert optimal["rtf"] == 30.0
    assert optimal["peak_vram_mb"] == 6000


def test_returns_none_when_nothing_fits() -> None:
    measurements = [
        _measurement(vad_batch=1, peak_mb=15000, elapsed_s=4.0),
        _measurement(vad_batch=4, peak_mb=15500, elapsed_s=2.0),
    ]
    # All cells exceed 15360 * 0.85 = 13056 MB.
    optimal = _compute_throughput_optimal(
        measurements, gpu_vram_mb=15360, safety_margin=0.85
    )
    assert optimal is None


def test_returns_none_when_no_measurements() -> None:
    assert _compute_throughput_optimal([], gpu_vram_mb=15360) is None


def test_returns_none_when_gpu_vram_zero() -> None:
    # Dry-run paths sometimes pass gpu_vram_mb=0 — guard so we don't divide
    # by zero or return spurious optima.
    measurements = [_measurement(vad_batch=1)]
    assert _compute_throughput_optimal(measurements, gpu_vram_mb=0) is None


def test_skips_measurements_with_zero_elapsed_time() -> None:
    # Synthetic dry-run measurements may have elapsed_s=0; the picker
    # must ignore them rather than dividing by zero or treating them as
    # infinite-RTF.
    measurements = [
        _measurement(vad_batch=1, peak_mb=4000, elapsed_s=0.0),
        _measurement(vad_batch=4, peak_mb=5000, elapsed_s=2.0),  # rtf=30
    ]
    optimal = _compute_throughput_optimal(
        measurements, gpu_vram_mb=15360, safety_margin=0.85
    )
    assert optimal is not None
    assert optimal["value"] == 4


def test_emits_threshold_and_safety_margin_for_traceability() -> None:
    measurements = [_measurement(vad_batch=4, peak_mb=5000, elapsed_s=2.0)]
    optimal = _compute_throughput_optimal(
        measurements, gpu_vram_mb=15360, safety_margin=0.75
    )
    assert optimal is not None
    assert optimal["safety_margin"] == 0.75
    assert optimal["threshold_mb"] == int(15360 * 0.75)


def test_axis_detection_for_duration_only_sweep() -> None:
    # Pyannote sweeps duration without a second axis; axis should resolve to
    # "" with value=None rather than crashing the picker.
    measurements = [
        {
            "params": {"audio_s": 600},
            "peak_vram_mb": 1500,
            "delta_mb": 200,
            "elapsed_s": 1.0,
        },
        {
            "params": {"audio_s": 900},
            "peak_vram_mb": 1500,
            "delta_mb": 200,
            "elapsed_s": 1.5,
        },
    ]
    # Both fit; best RTF is the one with higher audio_s/elapsed ratio:
    # 600/1.0 = 600 vs 900/1.5 = 600 — tie; whichever max() picks is fine.
    optimal = _compute_throughput_optimal(measurements, gpu_vram_mb=15360)
    assert optimal is not None
    assert optimal["axis"] == ""
    assert optimal["value"] is None
    assert optimal["rtf"] == 600.0


# ---------------------------------------------------------------------------
# M89.2.2: --mode parsing
# ---------------------------------------------------------------------------


def test_parse_mode_solo() -> None:
    assert _parse_mode("solo") == ("solo", None)


def test_parse_mode_coloc_pyannote() -> None:
    assert _parse_mode("coloc:pyannote") == ("coloc_with_pyannote", "pyannote")


def test_parse_mode_coloc_strips_whitespace() -> None:
    assert _parse_mode("coloc:  nemo  ") == ("coloc_with_nemo", "nemo")


def test_parse_mode_rejects_bare_coloc() -> None:
    with pytest.raises(ValueError, match="non-empty key"):
        _parse_mode("coloc:")


def test_parse_mode_rejects_unknown_form() -> None:
    with pytest.raises(ValueError, match="solo' or 'coloc"):
        _parse_mode("with_pyannote")


# ---------------------------------------------------------------------------
# M89.2.2: profile merge-write
# ---------------------------------------------------------------------------


def _profile(
    *, engine_id: str, gpu: str, throughput_optimal: dict[str, Any] | None = None
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "schema_version": "1.0",
        "engine_id": engine_id,
        "model_id": "test-model",
        "stage": "transcribe",
        "gpu": gpu,
        "gpu_vram_mb": 15360,
        "cuda_overhead_mb": 700,
        "measurements": [],
        "model": {
            "weights_mb": 700,
            "formula": "S",
            "coefficients": {},
            "r_squared": 1.0,
            "safety_margin": 0.15,
        },
    }
    if throughput_optimal is not None:
        p["throughput_optimal"] = throughput_optimal
    return p


def test_merge_preserves_modes_not_in_new_profile(tmp_path: Path) -> None:
    # Pre-existing profile has a solo block from an earlier run.
    existing = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    # New run only computes coloc_with_pyannote.
    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "coloc_with_pyannote": {"axis": "vad_batch_size", "value": 2, "rtf": 22.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert set(merged["throughput_optimal"].keys()) == {"solo", "coloc_with_pyannote"}
    assert merged["throughput_optimal"]["solo"]["value"] == 4
    assert merged["throughput_optimal"]["coloc_with_pyannote"]["value"] == 2


def test_merge_new_run_overwrites_same_mode(tmp_path: Path) -> None:
    existing = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    # Re-running solo replaces the old solo block (operator re-ran the sweep).
    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 8, "rtf": 38.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert merged["throughput_optimal"]["solo"]["value"] == 8
    assert merged["throughput_optimal"]["solo"]["rtf"] == 38.0


def test_merge_skipped_when_engine_id_differs(tmp_path: Path) -> None:
    # Existing file is for pyannote, new run is for nemo — no merge, just overwrite.
    existing = _profile(
        engine_id="pyannote-4.0",
        gpu="T4",
        throughput_optimal={"solo": {"axis": "audio_s", "value": 600, "rtf": 600.0}},
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert merged["throughput_optimal"] == {
        "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
    }


def test_merge_skipped_when_gpu_differs(tmp_path: Path) -> None:
    existing = _profile(
        engine_id="nemo",
        gpu="A10G",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 8, "rtf": 60.0}
        },
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert merged["throughput_optimal"] == {
        "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
    }


def test_merge_no_op_when_output_missing(tmp_path: Path) -> None:
    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, tmp_path / "nope.json")
    assert merged["throughput_optimal"] == {
        "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
    }


def test_merge_no_op_when_existing_file_is_corrupt(tmp_path: Path) -> None:
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text("not json {{{")

    new_profile = _profile(
        engine_id="nemo",
        gpu="T4",
        throughput_optimal={
            "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
        },
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert merged["throughput_optimal"] == {
        "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
    }


# ---------------------------------------------------------------------------
# M89.2.3: recommended budget derivation
# ---------------------------------------------------------------------------


def test_budget_rounds_up_to_nearest_1000() -> None:
    # 9000 peak + 500 headroom = 9500 → rounds up to 10000.
    assert _compute_recommended_budget_mb(9000) == 10000
    # 10000 peak + 500 = 10500 → 11000.
    assert _compute_recommended_budget_mb(10000) == 11000
    # 11999 peak + 500 = 12499 → 13000 (next 1000).
    assert _compute_recommended_budget_mb(11999) == 13000


def test_budget_respects_custom_headroom() -> None:
    # 5000 peak + 1500 headroom = 6500 → 7000.
    assert _compute_recommended_budget_mb(5000, headroom_mb=1500) == 7000


def test_budget_clamps_negatives_to_zero_before_rounding() -> None:
    # Defensive: synthetic / malformed peak values shouldn't yield negatives.
    assert _compute_recommended_budget_mb(-100, headroom_mb=-50) == 0


def test_budget_exact_thousand_stays_at_thousand() -> None:
    # 9500 + 500 = 10000 exactly → ceil-to-1000 returns 10000, not 11000.
    # Documents the rounding policy: only excess gets bumped.
    assert _compute_recommended_budget_mb(9500) == 10000


# ---------------------------------------------------------------------------
# M89.2.3: merge-write preserves baselines + recommended_budget_mb
# ---------------------------------------------------------------------------


def test_merge_preserves_baselines_and_recommended_budget(tmp_path: Path) -> None:
    existing = _profile(engine_id="nemo", gpu="T4")
    existing.update(
        {
            "throughput_optimal": {
                "solo": {"axis": "vad_batch_size", "value": 4, "rtf": 30.0}
            },
            "recommended_budget_mb": {"solo": 11000},
            "baselines": {"solo": {"start_mb": 5200}},
        }
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    # Coloc run brings new mode; should merge all three blocks.
    new_profile = _profile(engine_id="nemo", gpu="T4")
    new_profile.update(
        {
            "throughput_optimal": {
                "coloc_with_pyannote": {
                    "axis": "vad_batch_size",
                    "value": 2,
                    "rtf": 22.0,
                }
            },
            "recommended_budget_mb": {"coloc_with_pyannote": 11000},
            "baselines": {"coloc_with_pyannote": {"start_mb": 6800}},
        }
    )
    merged = _merge_throughput_into_existing(new_profile, out)

    assert set(merged["throughput_optimal"].keys()) == {
        "solo",
        "coloc_with_pyannote",
    }
    assert merged["recommended_budget_mb"] == {
        "solo": 11000,
        "coloc_with_pyannote": 11000,
    }
    assert merged["baselines"] == {
        "solo": {"start_mb": 5200},
        "coloc_with_pyannote": {"start_mb": 6800},
    }


def test_merge_overwrites_same_mode_in_recommended_budget(tmp_path: Path) -> None:
    existing = _profile(engine_id="nemo", gpu="T4")
    existing.update(
        {
            "recommended_budget_mb": {"solo": 11000},
            "baselines": {"solo": {"start_mb": 5200}},
        }
    )
    out = tmp_path / "transcribe-nemo-T4.json"
    out.write_text(json.dumps(existing))

    # Re-run solo with a different headroom — should overwrite, not stack.
    new_profile = _profile(engine_id="nemo", gpu="T4")
    new_profile.update(
        {
            "recommended_budget_mb": {"solo": 12000},
            "baselines": {"solo": {"start_mb": 5300}},
        }
    )
    merged = _merge_throughput_into_existing(new_profile, out)
    assert merged["recommended_budget_mb"] == {"solo": 12000}
    assert merged["baselines"] == {"solo": {"start_mb": 5300}}
