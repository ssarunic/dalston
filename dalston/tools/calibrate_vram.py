"""VRAM calibration script for GPU engines.

Measures peak VRAM usage across varying parameters by sending synthetic
audio to a running engine's HTTP endpoint and polling GPU memory via
pynvml.  Outputs a JSON calibration profile that the VRAM budget
calculator uses to auto-tune engine parameters.

Usage::

    # Against a running engine container with GPU
    python -m dalston.tools.calibrate_vram \\
        --engine-url http://localhost:9100 \\
        --stage transcribe \\
        --model-id parakeet-onnx-tdt-0.6b-v3 \\
        --gpu-id 0 \\
        --output dalston/tools/vram_profiles/transcribe-parakeet-onnx-tdt-0.6b-v3-T4.json

    # Dry-run mode (no GPU needed, generates synthetic profile)
    python -m dalston.tools.calibrate_vram \\
        --stage transcribe \\
        --model-id parakeet-onnx-tdt-0.6b-v3 \\
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

# Parameter grids per stage
STAGE_PARAMS: dict[str, dict[str, list]] = {
    "transcribe": {
        "audio_durations_s": [15, 30, 60, 120],
        "vad_batch_sizes": [1, 2, 4, 8, 16],
    },
    "diarize": {
        "audio_durations_s": [60, 180, 300, 600, 900],
    },
    "align": {
        "audio_durations_s": [30, 60, 120, 300],
    },
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
) -> dict[str, Any]:
    """Run one inference and return VRAM measurements."""
    baseline_mb = monitor.current_used_mb() if monitor else 0

    if monitor:
        monitor.start()

    try:
        t0 = time.monotonic()
        send_to_engine(engine_url, endpoint, audio_path, model_id)
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


def run_calibration(
    engine_url: str,
    stage: str,
    model_id: str | None,
    gpu_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full calibration sweep and return the profile dict."""
    endpoint = STAGE_ENDPOINTS.get(stage)
    if not endpoint:
        raise ValueError(f"Unknown stage: {stage}. Supported: {list(STAGE_ENDPOINTS)}")

    params = STAGE_PARAMS.get(stage, {})
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

    with tempfile.TemporaryDirectory(prefix="dalston_calibrate_") as tmp:
        work_dir = Path(tmp)

        for dur in durations:
            audio_path = work_dir / f"test_{dur}s.wav"
            generate_wav(audio_path, dur)

            for _repeat in range(REPEATS):
                if dry_run:
                    m = _synthetic_measurement(dur, baseline_mb, stage)
                else:
                    m = measure_once(
                        engine_url, endpoint, audio_path, model_id, monitor
                    )

                measurements.append(
                    {
                        "params": {"audio_s": dur},
                        "peak_vram_mb": m["peak_mb"],
                        "delta_mb": m["delta_mb"],
                        "elapsed_s": m["elapsed_s"],
                    }
                )
                print(
                    f"  duration={dur}s peak={m['peak_mb']}MB delta={m['delta_mb']}MB t={m['elapsed_s']}s"
                )

    # Fit linear model
    coefficients, r_squared = _fit_model(measurements, stage)

    profile = {
        "schema_version": "1.0",
        "engine_id": _engine_id_from_stage(stage),
        "model_id": model_id or "",
        "stage": stage,
        "gpu": gpu_name,
        "gpu_vram_mb": gpu_total_mb,
        "cuda_overhead_mb": baseline_mb,
        "calibrated_at": datetime.now(UTC).isoformat(),
        "measurements": measurements,
        "model": {
            "weights_mb": baseline_mb,
            "formula": _formula_string(stage),
            "coefficients": coefficients,
            "r_squared": round(r_squared, 4),
            "safety_margin": 0.15,
        },
    }
    return profile


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


def _fit_model(
    measurements: list[dict[str, Any]], stage: str
) -> tuple[dict[str, float], float]:
    """Fit a linear model to the measurements via least squares."""
    import numpy as np

    if not measurements:
        return {}, 0.0

    # Group by params and take max peak per param set (worst case)
    grouped: dict[str, int] = {}
    for m in measurements:
        key = json.dumps(m["params"], sort_keys=True)
        grouped[key] = max(grouped.get(key, 0), m["peak_vram_mb"])

    if stage == "transcribe":
        # Fit: peak = S + alpha_batch * vad_batch_size
        # For now we measure at default batch_size, so fit: peak = S (constant)
        # The alpha_batch comes from varying batch_size in future calibrations
        peaks = list(grouped.values())
        S = float(np.mean(peaks))
        alpha_batch = 55.0  # Default estimate until batch-size variation is calibrated

        ss_res = sum((p - S) ** 2 for p in peaks)
        ss_tot = sum((p - np.mean(peaks)) ** 2 for p in peaks)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

        return {"S": round(S, 1), "alpha_batch": alpha_batch}, r2

    else:
        # Diarize / align / generic: fit peak = S + alpha_duration * duration_s
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
        result, _, _, _ = np.linalg.lstsq(X, y, rcond=None)

        y_pred = X @ result
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

        return {
            "S": round(float(result[0]), 1),
            "alpha_duration": round(float(result[1]), 3),
        }, r2


def _engine_id_from_stage(stage: str) -> str:
    """Default engine_id guess from stage."""
    return {
        "transcribe": "onnx",
        "diarize": "pyannote-4.0",
        "align": "phoneme-align",
    }.get(stage, stage)


def _formula_string(stage: str) -> str:
    if stage == "transcribe":
        return "S + alpha_batch * vad_batch_size"
    return "S + alpha_duration * audio_duration_s"


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
        help="Engine stage type",
    )
    parser.add_argument("--model-id", help="Model ID to calibrate")
    parser.add_argument("--gpu-id", type=int, default=0, help="GPU device index")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate synthetic profile without GPU or engine",
    )

    args = parser.parse_args()

    print(f"Calibrating: stage={args.stage} model={args.model_id}")
    profile = run_calibration(
        engine_url=args.engine_url,
        stage=args.stage,
        model_id=args.model_id,
        gpu_id=args.gpu_id,
        dry_run=args.dry_run,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, indent=2) + "\n")
    print(f"\nProfile written to {output_path}")
    print(f"Coefficients: {json.dumps(profile['model']['coefficients'])}")
    print(f"R²: {profile['model']['r_squared']}")


if __name__ == "__main__":
    main()
