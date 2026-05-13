"""Profile co-located NeMo + pyannote GPU settings.

This is an end-to-end profiler for the single-GPU shape used by
``dalston-aws launch gpu --engines nemo,pyannote``. It sends concurrent
HTTP work to the NeMo transcription engine and the pyannote diarization
engine, samples NVML during the run, rejects cells that fail or exceed the
configured VRAM safety threshold, and emits the highest-throughput safe
configuration as concrete environment variables.

Example::

    python -m dalston.tools.profile_colocated_gpu \\
        --nemo-url http://localhost:9100 \\
        --pyannote-url http://localhost:9101 \\
        --duration-s 600 \\
        --output /data/vram_profiles/coloc-nemo-pyannote-T4.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dalston.tools.calibrate_vram import (
    STAGE_ENDPOINTS,
    VRAMMonitor,
    generate_wav,
    send_to_engine,
)

DEFAULT_NEMO_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
DEFAULT_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


@dataclass(frozen=True)
class WorkItem:
    """One HTTP request to one engine for a profiler cell."""

    engine: str
    url: str
    endpoint: str
    audio_path: Path
    model_id: str
    params: dict[str, Any]


def _parse_int_grid(raw: str) -> list[int]:
    """Parse a comma-separated positive integer grid."""
    values: list[int] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        value = int(stripped)
        if value <= 0:
            raise ValueError(f"grid values must be positive integers: {raw!r}")
        values.append(value)
    if not values:
        raise ValueError("grid must contain at least one value")
    return sorted(dict.fromkeys(values))


def _parse_float_grid(raw: str) -> list[float]:
    """Parse a comma-separated positive float grid."""
    values: list[float] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        value = float(stripped)
        if value <= 0:
            raise ValueError(f"grid values must be positive numbers: {raw!r}")
        values.append(value)
    if not values:
        raise ValueError("grid must contain at least one value")
    return sorted(dict.fromkeys(values))


def _round_up_1000(value: int) -> int:
    """Round a positive MB value up to the next GB-ish boundary."""
    if value <= 0:
        return 0
    return ((value + 999) // 1000) * 1000


def _timed_work(item: WorkItem) -> dict[str, Any]:
    """Run one engine request and return a compact result record."""
    start = time.monotonic()
    try:
        send_to_engine(
            item.url,
            item.endpoint,
            item.audio_path,
            item.model_id,
            item.params,
        )
    except Exception as exc:
        message = str(exc)
        return {
            "engine": item.engine,
            "ok": False,
            "elapsed_s": round(time.monotonic() - start, 3),
            "error": message[-500:],
            "oom": _looks_like_oom(message),
        }
    return {
        "engine": item.engine,
        "ok": True,
        "elapsed_s": round(time.monotonic() - start, 3),
        "error": "",
        "oom": False,
    }


def _looks_like_oom(message: str) -> bool:
    """Best-effort OOM classifier for HTTP/proxy error strings."""
    lower = message.lower()
    return "out of memory" in lower or "cuda oom" in lower or "cuda memory" in lower


def _build_work_items(
    *,
    nemo_url: str,
    pyannote_url: str,
    audio_path: Path,
    nemo_model: str,
    pyannote_model: str,
    nemo_batch_size: int,
    pyannote_chunk_s: float,
    nemo_inflight: int,
    pyannote_inflight: int,
) -> list[WorkItem]:
    """Build the concurrent HTTP request set for one profiler cell."""
    items: list[WorkItem] = []
    for _ in range(nemo_inflight):
        items.append(
            WorkItem(
                engine="nemo",
                url=nemo_url,
                endpoint=STAGE_ENDPOINTS["transcribe"],
                audio_path=audio_path,
                model_id=nemo_model,
                params={"vad_batch_size": nemo_batch_size},
            )
        )
    for _ in range(pyannote_inflight):
        items.append(
            WorkItem(
                engine="pyannote",
                url=pyannote_url,
                endpoint=STAGE_ENDPOINTS["diarize"],
                audio_path=audio_path,
                model_id=pyannote_model,
                params={"max_chunk_s": pyannote_chunk_s},
            )
        )
    return items


def measure_cell(
    *,
    nemo_url: str,
    pyannote_url: str,
    audio_path: Path,
    audio_duration_s: float,
    nemo_model: str,
    pyannote_model: str,
    nemo_batch_size: int,
    pyannote_chunk_s: float,
    nemo_inflight: int,
    pyannote_inflight: int,
    monitor: VRAMMonitor | None,
    gpu_vram_mb: int,
    safety_margin: float,
) -> dict[str, Any]:
    """Measure one grid cell and return a JSON-serializable record."""
    baseline_mb = monitor.current_used_mb() if monitor else 0
    items = _build_work_items(
        nemo_url=nemo_url,
        pyannote_url=pyannote_url,
        audio_path=audio_path,
        nemo_model=nemo_model,
        pyannote_model=pyannote_model,
        nemo_batch_size=nemo_batch_size,
        pyannote_chunk_s=pyannote_chunk_s,
        nemo_inflight=nemo_inflight,
        pyannote_inflight=pyannote_inflight,
    )

    if monitor:
        monitor.start()
    start = time.monotonic()
    results: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            futures = [pool.submit(_timed_work, item) for item in items]
            for future in as_completed(futures):
                results.append(future.result())
    finally:
        peak_mb = monitor.stop() if monitor else 0
    wall_s = time.monotonic() - start

    ok_by_engine = {
        "nemo": sum(1 for r in results if r["engine"] == "nemo" and r["ok"]),
        "pyannote": sum(1 for r in results if r["engine"] == "pyannote" and r["ok"]),
    }
    full_pipeline_jobs = min(ok_by_engine["nemo"], ok_by_engine["pyannote"])
    throughput_audio_s_per_s = (
        (full_pipeline_jobs * audio_duration_s) / wall_s if wall_s > 0 else 0.0
    )
    threshold_mb = int(gpu_vram_mb * safety_margin) if gpu_vram_mb else 0
    failed = any(not r["ok"] for r in results)
    oom = any(r.get("oom") for r in results)
    fits = (
        not failed
        and not oom
        and full_pipeline_jobs > 0
        and (threshold_mb <= 0 or peak_mb <= threshold_mb)
    )

    return {
        "params": {
            "nemo_batch_size": nemo_batch_size,
            "pyannote_max_chunk_s": pyannote_chunk_s,
            "nemo_inflight": nemo_inflight,
            "pyannote_inflight": pyannote_inflight,
        },
        "baseline_mb": baseline_mb,
        "peak_vram_mb": peak_mb,
        "delta_mb": peak_mb - baseline_mb,
        "threshold_mb": threshold_mb,
        "wall_s": round(wall_s, 3),
        "pipeline_jobs_successful": full_pipeline_jobs,
        "throughput_audio_s_per_s": round(throughput_audio_s_per_s, 3),
        "speedup_vs_realtime": round(throughput_audio_s_per_s, 1),
        "fits": fits,
        "failed": failed,
        "oom": oom,
        "results": sorted(results, key=lambda r: r["engine"]),
    }


def select_recommendation(
    measurements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the fastest cell whose every repeat fit under the threshold."""
    candidates = [m for m in _aggregate_repeated_cells(measurements) if m.get("fits")]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda m: (
            m.get("throughput_audio_s_per_s", 0),
            -m.get("peak_vram_mb", 0),
            -m["params"].get("nemo_inflight", 1),
            -m["params"].get("pyannote_inflight", 1),
        ),
    )


def _aggregate_repeated_cells(
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse repeated measurements by params, requiring all repeats to fit."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in measurements:
        key = json.dumps(m.get("params", {}), sort_keys=True)
        grouped.setdefault(key, []).append(m)

    aggregates: list[dict[str, Any]] = []
    for group in grouped.values():
        first = group[0]
        fits = all(bool(m.get("fits")) for m in group)
        throughputs = [float(m.get("throughput_audio_s_per_s", 0)) for m in group]
        peaks = [int(m.get("peak_vram_mb", 0)) for m in group]
        thresholds = [int(m.get("threshold_mb", 0)) for m in group]
        jobs = [int(m.get("pipeline_jobs_successful", 0)) for m in group]
        aggregates.append(
            {
                "params": first["params"],
                "repeat_count": len(group),
                "fits": fits,
                "failed": any(bool(m.get("failed")) for m in group),
                "oom": any(bool(m.get("oom")) for m in group),
                "peak_vram_mb": max(peaks) if peaks else 0,
                "threshold_mb": min(thresholds) if thresholds else 0,
                # Conservative throughput for reliability: the slowest
                # successful repeat is the one to beat.
                "throughput_audio_s_per_s": round(min(throughputs), 3)
                if throughputs
                else 0.0,
                "speedup_vs_realtime": round(min(throughputs), 1)
                if throughputs
                else 0.0,
                "pipeline_jobs_successful": min(jobs) if jobs else 0,
            }
        )
    return aggregates


def build_env_recommendation(
    best: dict[str, Any] | None,
    *,
    safety_margin: float,
    budget_headroom_mb: int,
) -> dict[str, Any]:
    """Convert the selected cell into operator-facing environment variables."""
    if best is None:
        return {
            "status": "no_safe_cell",
            "reason": "No measured cell completed without failure under the VRAM threshold.",
            "env": {},
        }

    params = best["params"]
    total_capacity = max(int(params["nemo_inflight"]), 1)
    budget_mb = _round_up_1000(int(best["peak_vram_mb"]) + budget_headroom_mb)
    return {
        "status": "ok",
        "basis": {
            "safety_margin": safety_margin,
            "budget_headroom_mb": budget_headroom_mb,
            "peak_vram_mb": best["peak_vram_mb"],
            "measured_total_peak_budget_mb": budget_mb,
            "threshold_mb": best["threshold_mb"],
            "throughput_audio_s_per_s": best["throughput_audio_s_per_s"],
            "speedup_vs_realtime": best["speedup_vs_realtime"],
        },
        "env": {
            "nemo": {
                "DALSTON_NEMO_BATCH_SIZE": str(params["nemo_batch_size"]),
                "DALSTON_BATCH_MAX_INFLIGHT": str(params["nemo_inflight"]),
                "DALSTON_TOTAL_CAPACITY": str(total_capacity),
                "DALSTON_RT_RESERVATION": "0",
            },
            "pyannote": {
                "DALSTON_MAX_DIARIZE_CHUNK_S": str(
                    int(params["pyannote_max_chunk_s"])
                    if float(params["pyannote_max_chunk_s"]).is_integer()
                    else params["pyannote_max_chunk_s"]
                ),
            },
        },
        "notes": [
            "measured_total_peak_budget_mb is the total co-located process budget, not a per-engine split.",
            "For Redis queue workers, Dalston processes one task at a time per engine container; inflight mainly matters for direct HTTP/unified-runner admission.",
        ],
    }


def run_profile(
    *,
    nemo_url: str,
    pyannote_url: str,
    nemo_model: str,
    pyannote_model: str,
    audio_duration_s: float,
    audio_file: str | None,
    nemo_batch_sizes: list[int],
    pyannote_chunk_sizes: list[float],
    nemo_inflight_values: list[int],
    pyannote_inflight_values: list[int],
    repeats: int,
    gpu_id: int,
    safety_margin: float,
    budget_headroom_mb: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the co-located profiler and return the profile document."""
    monitor: VRAMMonitor | None = None
    if dry_run:
        gpu_name = "dry-run"
        gpu_vram_mb = 15360
    else:
        monitor = VRAMMonitor(gpu_id)
        gpu_name = monitor.gpu_name
        gpu_vram_mb = monitor.total_mb

    measurements: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dalston_coloc_profile_") as tmp:
        audio_path = Path(tmp) / "profile.wav"
        if audio_file:
            from dalston.tools.calibrate_vram import _slice_audio

            _slice_audio(Path(audio_file), audio_path, audio_duration_s)
        else:
            generate_wav(audio_path, audio_duration_s)

        for nemo_batch in nemo_batch_sizes:
            for py_chunk in pyannote_chunk_sizes:
                for nemo_inflight in nemo_inflight_values:
                    for py_inflight in pyannote_inflight_values:
                        for repeat in range(1, repeats + 1):
                            if dry_run:
                                m = _synthetic_cell(
                                    audio_duration_s=audio_duration_s,
                                    nemo_batch_size=nemo_batch,
                                    pyannote_chunk_s=py_chunk,
                                    nemo_inflight=nemo_inflight,
                                    pyannote_inflight=py_inflight,
                                    gpu_vram_mb=gpu_vram_mb,
                                    safety_margin=safety_margin,
                                )
                            else:
                                m = measure_cell(
                                    nemo_url=nemo_url,
                                    pyannote_url=pyannote_url,
                                    audio_path=audio_path,
                                    audio_duration_s=audio_duration_s,
                                    nemo_model=nemo_model,
                                    pyannote_model=pyannote_model,
                                    nemo_batch_size=nemo_batch,
                                    pyannote_chunk_s=py_chunk,
                                    nemo_inflight=nemo_inflight,
                                    pyannote_inflight=py_inflight,
                                    monitor=monitor,
                                    gpu_vram_mb=gpu_vram_mb,
                                    safety_margin=safety_margin,
                                )
                            m["repeat"] = repeat
                            measurements.append(m)
                            _print_cell(m)

    best = select_recommendation(measurements)
    recommendation = build_env_recommendation(
        best,
        safety_margin=safety_margin,
        budget_headroom_mb=budget_headroom_mb,
    )

    return {
        "schema_version": "1.0",
        "profile_type": "colocated_gpu",
        "engines": {
            "nemo": {"engine_id": "nemo", "model_id": nemo_model},
            "pyannote": {
                "engine_id": "pyannote-4.0",
                "model_id": pyannote_model,
            },
        },
        "gpu": gpu_name,
        "gpu_vram_mb": gpu_vram_mb,
        "safety_margin": safety_margin,
        "audio_duration_s": audio_duration_s,
        "calibrated_at": datetime.now(UTC).isoformat(),
        "grid": {
            "nemo_batch_sizes": nemo_batch_sizes,
            "pyannote_chunk_sizes": pyannote_chunk_sizes,
            "nemo_inflight_values": nemo_inflight_values,
            "pyannote_inflight_values": pyannote_inflight_values,
            "repeats": repeats,
        },
        "measurements": measurements,
        "recommendation": recommendation,
    }


def _synthetic_cell(
    *,
    audio_duration_s: float,
    nemo_batch_size: int,
    pyannote_chunk_s: float,
    nemo_inflight: int,
    pyannote_inflight: int,
    gpu_vram_mb: int,
    safety_margin: float,
) -> dict[str, Any]:
    """Dry-run synthetic cell for CLI/test smoke runs without a GPU."""
    baseline = 6200
    nemo_activation = 900 + 450 * nemo_batch_size * nemo_inflight
    pyannote_activation = 500 + 0.4 * min(pyannote_chunk_s, audio_duration_s)
    pyannote_activation *= pyannote_inflight
    peak = int(baseline + nemo_activation + pyannote_activation)
    wall_s = max(
        audio_duration_s / (80 * max(1, nemo_batch_size)),
        audio_duration_s / 45,
    )
    wall_s *= 1.0 + 0.25 * max(0, nemo_inflight + pyannote_inflight - 2)
    threshold = int(gpu_vram_mb * safety_margin)
    full_jobs = min(nemo_inflight, pyannote_inflight)
    throughput = full_jobs * audio_duration_s / wall_s
    fits = peak <= threshold
    return {
        "params": {
            "nemo_batch_size": nemo_batch_size,
            "pyannote_max_chunk_s": pyannote_chunk_s,
            "nemo_inflight": nemo_inflight,
            "pyannote_inflight": pyannote_inflight,
        },
        "baseline_mb": baseline,
        "peak_vram_mb": peak,
        "delta_mb": peak - baseline,
        "threshold_mb": threshold,
        "wall_s": round(wall_s, 3),
        "pipeline_jobs_successful": full_jobs,
        "throughput_audio_s_per_s": round(throughput, 3),
        "speedup_vs_realtime": round(throughput, 1),
        "fits": fits,
        "failed": False,
        "oom": False,
        "results": [
            {"engine": "nemo", "ok": True, "elapsed_s": round(wall_s, 3), "error": ""},
            {
                "engine": "pyannote",
                "ok": True,
                "elapsed_s": round(wall_s, 3),
                "error": "",
            },
        ],
    }


def _print_cell(cell: dict[str, Any]) -> None:
    params = cell["params"]
    status = "fit" if cell["fits"] else "reject"
    print(
        "  "
        f"nemo_batch={params['nemo_batch_size']} "
        f"py_chunk={params['pyannote_max_chunk_s']} "
        f"inflight={params['nemo_inflight']}+{params['pyannote_inflight']} "
        f"peak={cell['peak_vram_mb']}MB "
        f"threshold={cell['threshold_mb']}MB "
        f"speedup={cell['speedup_vs_realtime']}x "
        f"{status}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dalston.tools.profile_colocated_gpu",
        description="Profile co-located NeMo + pyannote settings on one GPU.",
    )
    parser.add_argument("--nemo-url", default="http://localhost:9100")
    parser.add_argument("--pyannote-url", default="http://localhost:9101")
    parser.add_argument("--nemo-model", default=DEFAULT_NEMO_MODEL)
    parser.add_argument("--pyannote-model", default=DEFAULT_PYANNOTE_MODEL)
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--audio-file", default=None)
    parser.add_argument("--nemo-batch-sizes", default="1,2,4")
    parser.add_argument("--pyannote-chunk-sizes", default="300,600,900")
    parser.add_argument("--nemo-inflight", default="1")
    parser.add_argument("--pyannote-inflight", default="1")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--safety-margin", type=float, default=0.85)
    parser.add_argument("--budget-headroom-mb", type=int, default=500)
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    profile = run_profile(
        nemo_url=args.nemo_url,
        pyannote_url=args.pyannote_url,
        nemo_model=args.nemo_model,
        pyannote_model=args.pyannote_model,
        audio_duration_s=args.duration_s,
        audio_file=args.audio_file,
        nemo_batch_sizes=_parse_int_grid(args.nemo_batch_sizes),
        pyannote_chunk_sizes=_parse_float_grid(args.pyannote_chunk_sizes),
        nemo_inflight_values=_parse_int_grid(args.nemo_inflight),
        pyannote_inflight_values=_parse_int_grid(args.pyannote_inflight),
        repeats=args.repeats,
        gpu_id=args.gpu_id,
        safety_margin=args.safety_margin,
        budget_headroom_mb=args.budget_headroom_mb,
        dry_run=args.dry_run,
    )

    rec = profile["recommendation"]
    print()
    if rec["status"] == "ok":
        print("Recommendation:")
        for engine, env in rec["env"].items():
            print(f"  [{engine}]")
            for key, value in env.items():
                print(f"    {key}={value}")
    else:
        print(f"No safe recommendation: {rec['reason']}")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(profile, indent=2) + "\n")
        print(f"\nProfile written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
