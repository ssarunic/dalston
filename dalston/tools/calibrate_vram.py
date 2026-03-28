"""VRAM calibration script for GPU engines.

Measures peak VRAM usage across varying parameters by sending synthetic
audio to a running engine's HTTP endpoint and polling GPU memory via
pynvml.  Outputs a JSON calibration profile that the VRAM budget
calculator uses to auto-tune engine parameters.

Usage::

    # ONNX transcribe (default engine for transcribe stage)
    python -m dalston.tools.calibrate_vram \\
        --engine-url http://localhost:9100 \\
        --stage transcribe \\
        --model-id parakeet-onnx-tdt-0.6b-v3 \\
        --gpu-id 0 \\
        --output dalston/tools/vram_profiles/transcribe-parakeet-onnx-tdt-0.6b-v3-T4.json

    # Faster-whisper transcribe (specify --engine-id)
    python -m dalston.tools.calibrate_vram \\
        --engine-url http://localhost:9100 \\
        --stage transcribe --engine-id faster-whisper \\
        --model-id large-v3-turbo \\
        --output dalston/tools/vram_profiles/transcribe-large-v3-turbo-T4.json

    # Dry-run mode (no GPU needed, generates synthetic profile)
    python -m dalston.tools.calibrate_vram \\
        --stage transcribe --engine-id faster-whisper \\
        --model-id large-v3-turbo \\
        --dry-run \\
        --output /tmp/profile.json

Requirements:
    pip install nvidia-ml-py3 requests numpy
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Stage → endpoint mapping
# ---------------------------------------------------------------------------

STAGE_ENDPOINTS: dict[str, str] = {
    "transcribe": "/v1/transcribe",
    "diarize": "/v1/diarize",
    "align": "/v1/align",
}

# Per-engine calibration profiles: maps (stage, engine_id) to sweep params,
# model formula, and default engine_id guess.  New engines (NeMo, vLLM, etc.)
# slot in here without touching sweep/fit logic.
ENGINE_CALIBRATION: dict[str, dict[str, Any]] = {
    "onnx": {
        "stage": "transcribe",
        "params": {
            "audio_durations_s": [15, 30, 60, 120],
            "vad_batch_sizes": [1, 2, 4, 8, 16],
        },
        "formula": "S + alpha_batch * vad_batch_size",
        "sweep": "default",  # uses _sweep_default
    },
    "faster-whisper": {
        "stage": "transcribe",
        "params": {
            "audio_durations_s": [15, 30, 60, 120],
            "vad_batch_sizes": [1, 2, 4, 8, 16],
            "beam_sizes": [1, 3, 5],
        },
        "formula": "S + alpha_batch * vad_batch_size + alpha_beam * beam_size",
        "sweep": "faster-whisper",  # uses _sweep_faster_whisper
    },
    "pyannote-4.0": {
        "stage": "diarize",
        "params": {
            "audio_durations_s": [60, 180, 300, 600, 900],
        },
        "formula": "S + alpha_duration * audio_duration_s",
        "sweep": "default",
    },
    "phoneme-align": {
        "stage": "align",
        "params": {
            "audio_durations_s": [30, 60, 120, 300],
        },
        "formula": "S + alpha_duration * audio_duration_s",
        "sweep": "default",
    },
}

# Default engine_id per stage (used when --engine-id is not specified)
DEFAULT_ENGINE_FOR_STAGE: dict[str, str] = {
    "transcribe": "onnx",
    "diarize": "pyannote-4.0",
    "align": "phoneme-align",
}

REPEATS = 3  # Run each measurement N times, take max peak
POLL_INTERVAL_MS = 50  # VRAM polling interval


# ---------------------------------------------------------------------------
# Synthetic audio generation
# ---------------------------------------------------------------------------


def generate_wav(path: Path, duration_s: float, sample_rate: int = 16000) -> None:
    """Generate a WAV file with white noise at the given duration."""
    import numpy as np

    rng = np.random.default_rng(42)
    samples = rng.normal(0, 0.1, int(duration_s * sample_rate)).astype(np.float32)
    # Convert to 16-bit PCM
    pcm = (samples * 32767).clip(-32768, 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


# ---------------------------------------------------------------------------
# VRAM monitor (background thread)
# ---------------------------------------------------------------------------


class VRAMMonitor:
    """Polls GPU memory usage in a background thread."""

    def __init__(self, gpu_id: int = 0) -> None:
        import pynvml

        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
        self._gpu_name = pynvml.nvmlDeviceGetName(self._handle)
        if isinstance(self._gpu_name, bytes):
            self._gpu_name = self._gpu_name.decode()
        info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        self._total_mb = info.total // (1024 * 1024)

        self._peak_mb: int = 0
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def gpu_name(self) -> str:
        return self._gpu_name

    @property
    def total_mb(self) -> int:
        return self._total_mb

    def current_used_mb(self) -> int:
        import pynvml

        info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return int(info.used // (1024 * 1024))

    def start(self) -> None:
        """Start polling in background thread."""
        self._peak_mb = self.current_used_mb()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> int:
        """Stop polling and return peak VRAM in MB."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        return self._peak_mb

    def _poll_loop(self) -> None:
        while self._running:
            used = self.current_used_mb()
            if used > self._peak_mb:
                self._peak_mb = used
            time.sleep(POLL_INTERVAL_MS / 1000)


# ---------------------------------------------------------------------------
# Engine HTTP client
# ---------------------------------------------------------------------------


def send_to_engine(
    engine_url: str,
    endpoint: str,
    audio_path: Path,
    model_id: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send audio to an engine HTTP endpoint and return the response."""
    import requests

    url = f"{engine_url.rstrip('/')}{endpoint}"
    files = {"file": ("test.wav", audio_path.open("rb"), "audio/wav")}
    data: dict[str, Any] = {}
    if model_id:
        data["model"] = model_id
    if extra_params:
        data.update(extra_params)

    resp = requests.post(url, files=files, data=data, timeout=600)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def measure_once(
    engine_url: str,
    endpoint: str,
    audio_path: Path,
    model_id: str | None,
    monitor: VRAMMonitor | None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one inference and return VRAM measurements."""
    baseline_mb = monitor.current_used_mb() if monitor else 0

    if monitor:
        monitor.start()

    try:
        t0 = time.monotonic()
        send_to_engine(engine_url, endpoint, audio_path, model_id, extra_params)
        elapsed = time.monotonic() - t0
    finally:
        peak_mb = monitor.stop() if monitor else 0

    post_mb = monitor.current_used_mb() if monitor else 0

    return {
        "baseline_mb": baseline_mb,
        "peak_mb": peak_mb,
        "post_mb": post_mb,
        "delta_mb": peak_mb - baseline_mb,
        "elapsed_s": round(elapsed, 2),
    }


def _resolve_engine(stage: str, engine_id: str | None) -> tuple[str, dict[str, Any]]:
    """Resolve engine_id and its calibration config for the given stage."""
    eid = engine_id or DEFAULT_ENGINE_FOR_STAGE.get(stage, stage)
    cal = ENGINE_CALIBRATION.get(eid)
    if not cal:
        raise ValueError(
            f"Unknown engine '{eid}'. Available: {list(ENGINE_CALIBRATION)}"
        )
    if cal["stage"] != stage:
        raise ValueError(f"Engine '{eid}' is a {cal['stage']} engine, not {stage}")
    return eid, cal


def run_calibration(
    engine_url: str,
    stage: str,
    model_id: str | None,
    gpu_id: int,
    dry_run: bool = False,
    engine_id: str | None = None,
) -> dict[str, Any]:
    """Run the full calibration sweep and return the profile dict."""
    eid, cal = _resolve_engine(stage, engine_id)
    endpoint = STAGE_ENDPOINTS.get(stage)
    if not endpoint:
        raise ValueError(f"Unknown stage: {stage}. Supported: {list(STAGE_ENDPOINTS)}")

    params = cal["params"]
    durations = params.get("audio_durations_s", [60])

    monitor: VRAMMonitor | None = None
    gpu_name = "unknown"
    gpu_total_mb = 0

    if not dry_run:
        monitor = VRAMMonitor(gpu_id)
        gpu_name = monitor.gpu_name
        gpu_total_mb = monitor.total_mb
        print(f"GPU: {gpu_name} ({gpu_total_mb} MB)")
    else:
        print("DRY RUN: generating synthetic profile")
        gpu_name = "dry-run"
        gpu_total_mb = 16384

    measurements: list[dict[str, Any]] = []
    baseline_mb = monitor.current_used_mb() if monitor else 800

    if cal["sweep"] == "faster-whisper":
        measurements = _sweep_faster_whisper(
            engine_url,
            endpoint,
            model_id,
            monitor,
            durations,
            params,
            dry_run,
            baseline_mb,
        )
    else:
        measurements = _sweep_default(
            engine_url,
            endpoint,
            model_id,
            monitor,
            durations,
            stage,
            dry_run,
            baseline_mb,
            vad_batch_sizes=params.get("vad_batch_sizes"),
        )

    # Fit linear model
    coefficients, r_squared = _fit_model(measurements, eid)

    profile = {
        "schema_version": "1.0",
        "engine_id": eid,
        "model_id": model_id or "",
        "stage": stage,
        "gpu": gpu_name,
        "gpu_vram_mb": gpu_total_mb,
        "cuda_overhead_mb": baseline_mb,
        "calibrated_at": datetime.now(UTC).isoformat(),
        "measurements": measurements,
        "model": {
            "weights_mb": baseline_mb,
            "formula": cal["formula"],
            "coefficients": coefficients,
            "r_squared": round(r_squared, 4),
            "safety_margin": 0.15,
        },
    }
    return profile


def _sweep_default(
    engine_url: str,
    endpoint: str,
    model_id: str | None,
    monitor: VRAMMonitor | None,
    durations: list[int],
    stage: str,
    dry_run: bool,
    baseline_mb: int,
    vad_batch_sizes: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Standard calibration sweep.

    When ``vad_batch_sizes`` is provided (transcribe engines), sweeps
    vad_batch_size at a fixed 60s duration, then sweeps durations at
    default settings.  Otherwise sweeps duration only (diarize, align).
    """
    measurements: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dalston_calibrate_") as tmp:
        work_dir = Path(tmp)

        # Phase 1: vad_batch_size sweep (if applicable)
        if vad_batch_sizes:
            audio_60s = work_dir / "test_60s.wav"
            generate_wav(audio_60s, 60)

            print("Phase 1: vad_batch_size sweep (60s audio)")
            for vad_batch in vad_batch_sizes:
                for _repeat in range(REPEATS):
                    if dry_run:
                        m = _synthetic_measurement(60, baseline_mb, stage)
                        # Adjust peak for batch size
                        m["peak_mb"] += (vad_batch - 1) * 55
                        m["delta_mb"] = m["peak_mb"] - m["baseline_mb"]
                    else:
                        m = measure_once(
                            engine_url,
                            endpoint,
                            audio_60s,
                            model_id,
                            monitor,
                            extra_params={"vad_batch_size": vad_batch},
                        )

                    measurements.append(
                        {
                            "params": {"audio_s": 60, "vad_batch_size": vad_batch},
                            "peak_vram_mb": m["peak_mb"],
                            "delta_mb": m["delta_mb"],
                            "elapsed_s": m["elapsed_s"],
                        }
                    )
                    print(
                        f"  vad_batch={vad_batch} peak={m['peak_mb']}MB "
                        f"delta={m['delta_mb']}MB t={m['elapsed_s']}s"
                    )
            print()

        # Phase 2: duration sweep
        phase_label = "Phase 2: duration sweep" if vad_batch_sizes else ""
        if phase_label:
            print(phase_label)

        for dur in durations:
            audio_path = work_dir / f"test_{dur}s.wav"
            if not audio_path.exists():
                generate_wav(audio_path, dur)

            extra = {"vad_batch_size": 1} if vad_batch_sizes else None

            for _repeat in range(REPEATS):
                if dry_run:
                    m = _synthetic_measurement(dur, baseline_mb, stage)
                else:
                    m = measure_once(
                        engine_url,
                        endpoint,
                        audio_path,
                        model_id,
                        monitor,
                        extra_params=extra,
                    )

                params_record: dict[str, Any] = {"audio_s": dur}
                if vad_batch_sizes:
                    params_record["vad_batch_size"] = 1

                measurements.append(
                    {
                        "params": params_record,
                        "peak_vram_mb": m["peak_mb"],
                        "delta_mb": m["delta_mb"],
                        "elapsed_s": m["elapsed_s"],
                    }
                )
                print(
                    f"  duration={dur}s peak={m['peak_mb']}MB "
                    f"delta={m['delta_mb']}MB t={m['elapsed_s']}s"
                )
    return measurements


def _sweep_faster_whisper(
    engine_url: str,
    endpoint: str,
    model_id: str | None,
    monitor: VRAMMonitor | None,
    durations: list[int],
    params: dict[str, list],
    dry_run: bool,
    baseline_mb: int,
) -> list[dict[str, Any]]:
    """Faster-whisper calibration sweep: vary vad_batch_size × beam_size × duration.

    Uses a fixed 60s audio clip for vad_batch_size/beam_size sweeps (VRAM is
    dominated by batch parallelism and beam width, not audio length).
    Then sweeps durations at default settings to confirm duration-independence.
    """
    measurements: list[dict[str, Any]] = []
    vad_batch_sizes = params.get("vad_batch_sizes", [1, 2, 4, 8, 16])
    beam_sizes = params.get("beam_sizes", [1, 3, 5])
    default_beam = beam_sizes[-1]  # highest beam in grid (5) used for phase 2

    with tempfile.TemporaryDirectory(prefix="dalston_calibrate_fw_") as tmp:
        work_dir = Path(tmp)

        # Phase 1: vad_batch_size × beam_size sweep at fixed 60s duration
        audio_60s = work_dir / "test_60s.wav"
        generate_wav(audio_60s, 60)

        print("Phase 1: vad_batch_size × beam_size sweep (60s audio)")
        for beam in beam_sizes:
            for vad_batch in vad_batch_sizes:
                for _repeat in range(REPEATS):
                    if dry_run:
                        m = _synthetic_fw_measurement(60, baseline_mb, vad_batch, beam)
                    else:
                        m = measure_once(
                            engine_url,
                            endpoint,
                            audio_60s,
                            model_id,
                            monitor,
                            extra_params={
                                "vad_batch_size": vad_batch,
                                "beam_size": beam,
                            },
                        )

                    measurements.append(
                        {
                            "params": {
                                "audio_s": 60,
                                "vad_batch_size": vad_batch,
                                "beam_size": beam,
                            },
                            "peak_vram_mb": m["peak_mb"],
                            "delta_mb": m["delta_mb"],
                            "elapsed_s": m["elapsed_s"],
                        }
                    )
                    print(
                        f"  beam={beam} vad_batch={vad_batch} peak={m['peak_mb']}MB "
                        f"delta={m['delta_mb']}MB t={m['elapsed_s']}s"
                    )

        # Phase 2: duration sweep at default settings
        # to confirm VRAM is duration-independent with VAD chunking
        print(f"\nPhase 2: duration sweep (vad_batch=1, beam={default_beam})")
        for dur in durations:
            audio_path = work_dir / f"test_{dur}s.wav"
            if not audio_path.exists():
                generate_wav(audio_path, dur)

            for _repeat in range(REPEATS):
                if dry_run:
                    m = _synthetic_fw_measurement(dur, baseline_mb, 1, default_beam)
                else:
                    m = measure_once(
                        engine_url,
                        endpoint,
                        audio_path,
                        model_id,
                        monitor,
                        extra_params={"vad_batch_size": 1, "beam_size": default_beam},
                    )

                measurements.append(
                    {
                        "params": {
                            "audio_s": dur,
                            "vad_batch_size": 1,
                            "beam_size": default_beam,
                        },
                        "peak_vram_mb": m["peak_mb"],
                        "delta_mb": m["delta_mb"],
                        "elapsed_s": m["elapsed_s"],
                    }
                )
                print(
                    f"  duration={dur}s peak={m['peak_mb']}MB "
                    f"delta={m['delta_mb']}MB t={m['elapsed_s']}s"
                )

    return measurements


def _synthetic_measurement(
    duration_s: float, baseline_mb: int, stage: str
) -> dict[str, Any]:
    """Generate a synthetic measurement for dry-run mode."""
    if stage == "transcribe":
        # Transcribe: ~constant due to VAD chunking, slight increase with duration
        peak = baseline_mb + 1200 + int(duration_s * 0.5)
        delta = peak - baseline_mb
    elif stage == "diarize":
        # Diarize: scales with duration (pyannote reconstruction)
        peak = baseline_mb + 800 + int(duration_s * 5.5)
        delta = peak - baseline_mb
    else:
        peak = baseline_mb + 600 + int(duration_s * 1.0)
        delta = peak - baseline_mb

    return {
        "baseline_mb": baseline_mb,
        "peak_mb": peak,
        "post_mb": baseline_mb + 50,
        "delta_mb": delta,
        "elapsed_s": round(duration_s * 0.05 + 0.5, 2),
    }


def _synthetic_fw_measurement(
    duration_s: float,
    baseline_mb: int,
    vad_batch_size: int,
    beam_size: int,
) -> dict[str, Any]:
    """Generate a synthetic faster-whisper measurement for dry-run mode.

    Models CTranslate2 VRAM: weights are constant, decoder KV-cache scales
    with beam_size, and VAD batch parallelism scales with vad_batch_size.
    Synthetic formula: peak = baseline + S + alpha_batch * vad_batch_size + alpha_beam * beam_size
    """
    S = 800  # Activation overhead (MB)
    alpha_batch = 120  # Per-batch-slot VRAM (MB)
    alpha_beam = 80  # Per-beam VRAM (MB)
    peak = baseline_mb + S + alpha_batch * vad_batch_size + alpha_beam * beam_size
    delta = peak - baseline_mb

    # Throughput scales ~linearly with vad_batch_size, inversely with beam_size
    rtf_base = 0.03
    effective_rtf = rtf_base * (beam_size / 5) / max(vad_batch_size, 1)
    elapsed = round(duration_s * effective_rtf + 0.3, 2)

    return {
        "baseline_mb": baseline_mb,
        "peak_mb": peak,
        "post_mb": baseline_mb + 50,
        "delta_mb": delta,
        "elapsed_s": elapsed,
    }


def _lstsq_r2(X: Any, y: Any) -> tuple[Any, float]:
    """Least-squares fit returning (coefficients, R²)."""
    import numpy as np

    result, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    y_pred = X @ result
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    return result, r2


def _fit_model(
    measurements: list[dict[str, Any]], engine_id: str
) -> tuple[dict[str, float], float]:
    """Fit a linear model to the measurements via least squares.

    The fit strategy is determined by ``ENGINE_CALIBRATION[engine_id]``:
    - Engines with ``beam_sizes`` in params → 2D fit (vad_batch_size + beam_size)
    - Engines with ``vad_batch_sizes`` → 1D fit (vad_batch_size)
    - Everything else → duration fit (audio_s)
    """
    import numpy as np

    if not measurements:
        return {}, 0.0

    cal = ENGINE_CALIBRATION.get(engine_id, {})
    cal_params = cal.get("params", {})

    # Group by params and take max peak per param set (worst case)
    grouped: dict[str, int] = {}
    for m in measurements:
        key = json.dumps(m["params"], sort_keys=True)
        grouped[key] = max(grouped.get(key, 0), m["peak_vram_mb"])

    has_beam = "beam_sizes" in cal_params
    has_vad_batch = "vad_batch_sizes" in cal_params

    if has_beam and has_vad_batch:
        # 2D fit: peak = S + alpha_batch * vad_batch_size + alpha_beam * beam_size
        # Group by (vad_batch_size, beam_size) — ignoring audio_s — so phase-2
        # duration-sweep points don't overweight a single combination.
        by_params: dict[tuple[int, int], int] = {}
        for key, peak in grouped.items():
            p = json.loads(key)
            combo = (p.get("vad_batch_size", 1), p.get("beam_size", 5))
            by_params[combo] = max(by_params.get(combo, 0), peak)

        batch_vals = [k[0] for k in by_params]
        beam_vals = [k[1] for k in by_params]
        peaks = list(by_params.values())

        if len(peaks) < 3:
            return {
                "S": float(peaks[0]) if peaks else 0,
                "alpha_batch": 0.0,
                "alpha_beam": 0.0,
            }, 1.0

        X = np.column_stack(
            [
                np.ones(len(peaks)),
                np.array(batch_vals, dtype=float),
                np.array(beam_vals, dtype=float),
            ]
        )
        y = np.array(peaks, dtype=float)
        coeffs, r2 = _lstsq_r2(X, y)

        return {
            "S": round(float(coeffs[0]), 1),
            "alpha_batch": round(float(coeffs[1]), 1),
            "alpha_beam": round(float(coeffs[2]), 1),
        }, r2

    elif has_vad_batch:
        # 1D fit: peak = S + alpha_batch * vad_batch_size
        # Extract vad_batch_size from params where available; points without
        # it (duration-sweep phase) are treated as vad_batch_size=1.
        batch_vals = []
        peaks = []
        for key, peak in grouped.items():
            p = json.loads(key)
            batch_vals.append(p.get("vad_batch_size", 1))
            peaks.append(peak)

        if len(peaks) < 2:
            return {"S": float(peaks[0]) if peaks else 0, "alpha_batch": 55.0}, 1.0

        X = np.column_stack([np.ones(len(peaks)), np.array(batch_vals, dtype=float)])
        y = np.array(peaks, dtype=float)
        coeffs, r2 = _lstsq_r2(X, y)

        return {
            "S": round(float(coeffs[0]), 1),
            "alpha_batch": round(float(coeffs[1]), 1),
        }, r2

    else:
        # Duration fit: peak = S + alpha_duration * audio_s
        durations = []
        peaks = []
        for key, peak in grouped.items():
            params = json.loads(key)
            durations.append(params.get("audio_s", 60))
            peaks.append(peak)

        if len(durations) < 2:
            return {"S": float(peaks[0]) if peaks else 0}, 1.0

        X = np.column_stack([np.ones(len(durations)), durations])
        y = np.array(peaks, dtype=float)
        coeffs, r2 = _lstsq_r2(X, y)

        return {
            "S": round(float(coeffs[0]), 1),
            "alpha_duration": round(float(coeffs[1]), 3),
        }, r2


# ---------------------------------------------------------------------------
# Leak detection mode
# ---------------------------------------------------------------------------


WARMUP_ITERATIONS = 5  # Ignore first N iterations (allocator warm-up)
TAIL_WINDOW = 10  # Evaluate last N iterations for plateau detection


def _linear_fit(
    values: list[int | float],
) -> tuple[float, float, float]:
    """Return (slope, intercept, r²) for a sequence via least-squares."""
    import numpy as np

    if len(values) < 3:
        return 0.0, float(values[0]) if values else 0.0, 0.0
    x = np.arange(1, len(values) + 1, dtype=float)
    y = np.array(values, dtype=float)
    coeffs = np.polyfit(x, y, 1)
    y_pred = np.polyval(coeffs, x)
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return float(coeffs[0]), float(coeffs[1]), r2


def _get_engine_debug(engine_url: str) -> dict[str, Any]:
    """Query the engine's /debug/status endpoint, returning {} on failure."""
    import requests

    try:
        resp = requests.get(f"{engine_url.rstrip('/')}/debug/status", timeout=5)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return {}


def _extract_vad_shape(response: dict[str, Any]) -> tuple[int, float, float]:
    """Extract segment count, max and mean segment duration from engine response."""
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    segments = data.get("segments", []) if isinstance(data, dict) else []
    segment_count = len(segments)
    seg_durations = [
        s.get("end", 0) - s.get("start", 0)
        for s in segments
        if isinstance(s, dict) and "start" in s and "end" in s
    ]
    max_seg_s = round(max(seg_durations), 1) if seg_durations else 0.0
    mean_seg_s = (
        round(sum(seg_durations) / len(seg_durations), 1) if seg_durations else 0.0
    )
    return segment_count, max_seg_s, mean_seg_s


def run_leak_detection(
    engine_url: str,
    stage: str,
    model_id: str | None,
    gpu_id: int,
    iterations: int = 30,
    audio_duration_s: float = 60.0,
    warmup: int = WARMUP_ITERATIONS,
    audio_file: str | None = None,
    engine_id: str | None = None,
) -> dict[str, Any]:
    """Run the same inference N times and track VRAM, threads, and RSS trends.

    Uses warmup iterations to let the CUDA allocator stabilize before
    measuring. Evaluates both full-range and tail-window slopes to
    distinguish genuine leaks from allocator growth that plateaus.

    Args:
        engine_url: Engine HTTP endpoint.
        stage: Pipeline stage (transcribe, diarize, align).
        model_id: Model ID to test.
        gpu_id: GPU device index.
        iterations: Total iterations including warmup (default: 30).
        audio_duration_s: Duration of synthetic test audio (default: 60).
        warmup: Iterations to ignore for trend analysis (default: 5).
        audio_file: Path to a real audio file instead of synthetic.
        engine_id: Engine identifier (e.g. onnx, faster-whisper).
    """
    import numpy as np

    endpoint = STAGE_ENDPOINTS.get(stage)
    if not endpoint:
        raise ValueError(f"Unknown stage: {stage}. Supported: {list(STAGE_ENDPOINTS)}")

    monitor = VRAMMonitor(gpu_id)
    print(f"GPU: {monitor.gpu_name} ({monitor.total_mb} MB)")
    print(f"Leak detection: {iterations} iterations ({warmup} warmup) × ", end="")
    if audio_file:
        print(f"file={audio_file}")
    else:
        print(f"{audio_duration_s}s synthetic audio")
    print()

    # Resolve audio: use provided file or generate synthetic
    with tempfile.TemporaryDirectory(prefix="dalston_leak_") as tmp:
        if audio_file:
            audio_path = Path(audio_file)
            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found: {audio_file}")
            print(f"Using real audio: {audio_path}")
        else:
            audio_path = Path(tmp) / f"test_{int(audio_duration_s)}s.wav"
            generate_wav(audio_path, audio_duration_s)
            print(f"Generated {audio_duration_s}s synthetic audio")

        header = (
            f"{'#':>3s}  {'VRAM base':>10s}  {'VRAM peak':>10s}  "
            f"{'VRAM post':>10s}  {'delta':>7s}  {'leaked':>7s}  "
            f"{'threads':>8s}  {'RSS':>9s}  {'VAD shape':>18s}  {'elapsed':>8s}"
        )
        print(header)
        print("-" * len(header))

        measurements: list[dict[str, Any]] = []
        initial_post_mb: int | None = None

        for i in range(iterations):
            baseline_mb = monitor.current_used_mb()
            monitor.start()

            try:
                t0 = time.monotonic()
                response = send_to_engine(engine_url, endpoint, audio_path, model_id)
                elapsed = time.monotonic() - t0
                status = "ok"
            except Exception as e:
                response = {}
                elapsed = time.monotonic() - t0
                status = f"err: {type(e).__name__}"
            finally:
                peak_mb = monitor.stop()

            # Small delay to let GPU memory settle
            time.sleep(0.5)
            post_mb = monitor.current_used_mb()

            if initial_post_mb is None:
                initial_post_mb = post_mb
            leaked_mb = post_mb - initial_post_mb

            segment_count, max_seg_s, mean_seg_s = _extract_vad_shape(response)

            debug = _get_engine_debug(engine_url)
            thread_count = debug.get("thread_count", "?")
            rss_mb = debug.get("rss_mb")
            warmup_label = " (warmup)" if i < warmup else ""

            m = {
                "iteration": i + 1,
                "warmup": i < warmup,
                "baseline_mb": baseline_mb,
                "peak_mb": peak_mb,
                "post_mb": post_mb,
                "delta_mb": peak_mb - baseline_mb,
                "leaked_mb": leaked_mb,
                "thread_count": thread_count,
                "rss_mb": rss_mb,
                "segment_count": segment_count,
                "max_segment_s": max_seg_s,
                "mean_segment_s": mean_seg_s,
                "elapsed_s": round(elapsed, 2),
                "status": status,
            }
            measurements.append(m)

            tc_str = str(thread_count).rjust(8)
            rss_str = f"{rss_mb:6d} MB" if isinstance(rss_mb, int) else "     ? MB"
            seg_str = (
                f"{segment_count:3d}seg max={max_seg_s:5.1f}s"
                if segment_count
                else "  no segments"
            )
            print(
                f"{i + 1:3d}  {baseline_mb:7d} MB  {peak_mb:7d} MB  "
                f"{post_mb:7d} MB  {peak_mb - baseline_mb:+5d} MB  "
                f"{leaked_mb:+5d} MB  {tc_str}  {rss_str}  "
                f"{seg_str}  {elapsed:7.1f}s  {status}{warmup_label}"
            )

        # Analyze trends (skip warmup iterations)
        print()
        all_post = [m["post_mb"] for m in measurements]
        stable_measurements = measurements[warmup:]
        post_values = [m["post_mb"] for m in stable_measurements]

        # Full range (post-warmup)
        slope, intercept, r2 = _linear_fit(post_values)

        # Tail window (last N iterations) — detects plateau vs continued growth
        tail = (
            post_values[-TAIL_WINDOW:]
            if len(post_values) > TAIL_WINDOW
            else post_values
        )
        tail_slope, _, tail_r2 = _linear_fit(tail)

        # Thread trend (post-warmup)
        thread_values = [
            m["thread_count"]
            for m in stable_measurements
            if isinstance(m["thread_count"], int)
        ]
        thread_slope, _, _ = (
            _linear_fit(thread_values) if len(thread_values) >= 3 else (0.0, 0.0, 0.0)
        )

        # RSS trend (post-warmup)
        rss_values = [
            m.get("rss_mb")
            for m in stable_measurements
            if isinstance(m.get("rss_mb"), int)
        ]
        rss_slope, _, _ = (
            _linear_fit(rss_values) if len(rss_values) >= 3 else (0.0, 0.0, 0.0)
        )

        # VAD shape correlation: does peak VRAM correlate with max segment length?
        stable_peaks = [m["peak_mb"] for m in stable_measurements]
        stable_max_segs = [m["max_segment_s"] for m in stable_measurements]
        peak_seg_corr = 0.0
        if len(stable_peaks) >= 3 and any(s > 0 for s in stable_max_segs):
            peak_arr = np.array(stable_peaks, dtype=float)
            seg_arr = np.array(stable_max_segs, dtype=float)
            if np.std(peak_arr) > 1.0 and np.std(seg_arr) > 0.1:
                peak_seg_corr = float(np.corrcoef(peak_arr, seg_arr)[0, 1])

        # Verdict: leak requires both post-warmup AND tail slopes to be positive.
        # If full slope is positive but tail is flat, it's allocator warm-up, not a leak.
        total_leaked = post_values[-1] - post_values[0] if len(post_values) >= 2 else 0
        warmup_growth = all_post[warmup] - all_post[0] if len(all_post) > warmup else 0
        has_vram_leak = slope > 5.0 and r2 > 0.7 and tail_slope > 2.0
        has_thread_leak = thread_slope > 0.3
        plateau_detected = slope > 5.0 and tail_slope < 2.0

        print("=" * 60)
        print(f"LEAK DETECTION RESULTS  (warmup={warmup}, tail={TAIL_WINDOW})")
        print("=" * 60)
        print(
            f"  Warmup growth (iter 1→{warmup}): {warmup_growth:+d} MB  (allocator settling)"
        )
        print(f"  Post-warmup trend: {slope:+.1f} MB/iter (R²={r2:.3f})")
        print(
            f"  Tail trend (last {len(tail)}): {tail_slope:+.1f} MB/iter (R²={tail_r2:.3f})"
        )
        print(
            f"  Total post-warmup growth: {total_leaked:+d} MB over {len(post_values)} iterations"
        )
        if thread_values:
            print(
                f"  Thread count: {thread_values[0]} → {thread_values[-1]} (slope={thread_slope:+.2f}/iter)"
            )
        if rss_values:
            print(
                f"  RSS: {rss_values[0]} → {rss_values[-1]} MB (slope={rss_slope:+.1f} MB/iter)"
            )
        if any(m["segment_count"] > 0 for m in stable_measurements):
            avg_segs = sum(m["segment_count"] for m in stable_measurements) / len(
                stable_measurements
            )
            avg_max = sum(m["max_segment_s"] for m in stable_measurements) / len(
                stable_measurements
            )
            print(
                f"  VAD shape: avg {avg_segs:.0f} segments, avg max_seg={avg_max:.1f}s"
            )
            if abs(peak_seg_corr) > 0.5:
                print(f"  Peak VRAM ↔ max segment correlation: {peak_seg_corr:+.2f}")
        print()
        if has_vram_leak:
            remaining = monitor.total_mb - post_values[-1]
            eta = int(remaining / tail_slope) if tail_slope > 0 else 0
            print("  ⚠ VRAM LEAK DETECTED: post-inference VRAM grows steadily")
            print(f"    Rate: ~{tail_slope:.1f} MB per job (tail)")
            if eta > 0:
                print(f"    Projected OOM in ~{eta} more jobs")
        elif plateau_detected:
            print("  ~ PLATEAU: VRAM grew during warmup then stabilized")
            print("    This is normal allocator behavior, not a leak")
        else:
            print("  ✓ No VRAM leak detected")
        if has_thread_leak:
            print("  ⚠ THREAD LEAK DETECTED: thread count grows with each job")
        elif thread_values:
            print("  ✓ No thread leak detected")
        if rss_values and rss_slope > 5.0:
            print(f"  ⚠ RSS LEAK: host memory grows at {rss_slope:.1f} MB/iter")
        print()

        return {
            "mode": "leak_detection",
            "gpu": monitor.gpu_name,
            "gpu_vram_mb": monitor.total_mb,
            "iterations": iterations,
            "warmup": warmup,
            "audio_duration_s": audio_duration_s,
            "audio_file": audio_file,
            "model_id": model_id or "",
            "stage": stage,
            "measurements": measurements,
            "analysis": {
                "warmup_growth_mb": warmup_growth,
                "vram_slope_mb_per_iter": round(slope, 2),
                "vram_intercept_mb": round(intercept, 1),
                "vram_r_squared": round(r2, 4),
                "tail_slope_mb_per_iter": round(tail_slope, 2),
                "tail_r_squared": round(tail_r2, 4),
                "vram_total_growth_mb": total_leaked,
                "plateau_detected": plateau_detected,
                "thread_slope_per_iter": round(thread_slope, 3),
                "thread_start": thread_values[0] if thread_values else None,
                "thread_end": thread_values[-1] if thread_values else None,
                "rss_slope_mb_per_iter": round(rss_slope, 2) if rss_values else None,
                "peak_vram_vs_max_segment_corr": round(peak_seg_corr, 3),
                "has_vram_leak": has_vram_leak,
                "has_thread_leak": has_thread_leak,
            },
            "run_at": datetime.now(UTC).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate VRAM usage for a Dalston engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--engine-url",
        default="http://localhost:9100",
        help="Engine HTTP endpoint (default: http://localhost:9100)",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=list(STAGE_ENDPOINTS),
        help="Pipeline stage (transcribe, diarize, align)",
    )
    parser.add_argument(
        "--engine-id",
        default=None,
        choices=list(ENGINE_CALIBRATION),
        help="Engine identifier (default: inferred from stage)",
    )
    parser.add_argument("--model-id", help="Model ID to calibrate")
    parser.add_argument("--gpu-id", type=int, default=0, help="GPU device index")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate synthetic profile without GPU or engine",
    )
    parser.add_argument(
        "--leak-detect",
        action="store_true",
        help="Run leak detection: same inference N times, track VRAM/thread growth",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=30,
        help="Total iterations including warmup (default: 30)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=WARMUP_ITERATIONS,
        help=f"Warmup iterations to skip in analysis (default: {WARMUP_ITERATIONS})",
    )
    parser.add_argument(
        "--audio-duration",
        type=float,
        default=60.0,
        help="Synthetic audio duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--audio-file",
        default=None,
        help="Path to a real audio file (overrides --audio-duration)",
    )

    args = parser.parse_args()

    engine_id = args.engine_id or DEFAULT_ENGINE_FOR_STAGE.get(args.stage)

    if args.leak_detect:
        print(
            f"Leak detection: stage={args.stage} engine={engine_id} model={args.model_id}"
        )
        result = run_leak_detection(
            engine_url=args.engine_url,
            stage=args.stage,
            model_id=args.model_id,
            gpu_id=args.gpu_id,
            iterations=args.iterations,
            audio_duration_s=args.audio_duration,
            warmup=args.warmup,
            audio_file=args.audio_file,
            engine_id=engine_id,
        )
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, indent=2) + "\n")
            print(f"Report written to {output_path}")
    else:
        print(
            f"Calibrating: stage={args.stage} engine={engine_id} model={args.model_id}"
        )
        profile = run_calibration(
            engine_url=args.engine_url,
            stage=args.stage,
            model_id=args.model_id,
            gpu_id=args.gpu_id,
            dry_run=args.dry_run,
            engine_id=engine_id,
        )

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"\nProfile written to {output_path}")
        print(f"Coefficients: {json.dumps(profile['model']['coefficients'])}")
        print(f"R²: {profile['model']['r_squared']}")


if __name__ == "__main__":
    main()
