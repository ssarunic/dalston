"""Tests for M89.2.1 throughput-optimal cell selection in calibrate_vram.

The helper picks the highest-RTF measurement that fits under
``gpu_vram_mb * safety_margin``. RTF is ``audio_s / elapsed_s`` from each
measurement; threshold filtering uses ``peak_vram_mb``.
"""

from __future__ import annotations

from typing import Any

from dalston.tools.calibrate_vram import _compute_throughput_optimal


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
