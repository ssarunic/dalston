"""Mixed-precision diarization benchmark (M90).

Validates the autocast change before flipping the production default.
Records wall-clock GPU inference time and peak VRAM per
``(dtype, audio_file)`` pair, and writes one RTTM per run so a separate
comparison step can compute drift DER against the fp32 reference.

Run on a GPU instance (the benchmark requires CUDA — fp16/bf16 paths
are no-ops on CPU)::

    python -m dalston.tools.bench_diarize_precision \\
        --instance-tag g4dn \\
        --dtypes fp32,fp16 \\
        --audio-dir /path/to/wavs \\
        --out-dir bench_out/

For drift DER and a markdown summary, see
``dalston.tools.bench_diarize_compare``.

Requirements::

    pip install pyannote.audio torch
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


def _run_one(
    pipeline: Any,
    audio_path: Path,
    dtype_name: str,
    autocast_ctx: Any,
) -> tuple[float, int, Any]:
    """Run a single diarization and return (elapsed_s, peak_mb, result)."""
    import torch

    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    with autocast_ctx(dtype_name):
        result = pipeline(str(audio_path))
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    peak_mb = torch.cuda.max_memory_allocated() // (1024 * 1024)
    return elapsed, peak_mb, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance-tag", required=True, help="e.g. g4dn, g6")
    parser.add_argument(
        "--dtypes",
        required=True,
        help="Comma-separated dtypes to run. fp32 first so it sets the reference.",
    )
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--model-id",
        default="pyannote/speaker-diarization-community-1",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="Runs per (dtype, audio). The slowest pass (cold cache) is dropped.",
    )
    parser.add_argument(
        "--bypass-chunking",
        action="store_true",
        help=(
            "Force single-pass diarization by setting "
            "DALSTON_MAX_DIARIZE_CHUNK_S to infinity. Only safe on instances "
            "with enough VRAM for the longest audio."
        ),
    )
    args = parser.parse_args()

    if args.bypass_chunking:
        os.environ["DALSTON_MAX_DIARIZE_CHUNK_S"] = "999999"

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise SystemExit(
            "HF_TOKEN is required to load pyannote/speaker-diarization-community-1"
        )

    import torch
    from pyannote.audio import Pipeline

    from dalston.engine_sdk.diarize_chunking import extract_annotation
    from dalston.engine_sdk.diarize_dtype import autocast_for_diarize

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for this benchmark.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = Path(args.audio_dir)
    audios = sorted(audio_dir.glob("*.wav"))
    if not audios:
        raise SystemExit(f"No .wav files found in {audio_dir}")

    dtype_names = [d.strip() for d in args.dtypes.split(",") if d.strip()]
    for d in dtype_names:
        if d not in {"fp32", "fp16", "bf16"}:
            raise SystemExit(f"Unknown dtype: {d!r}")

    print(
        f"[{args.instance_tag}] loading {args.model_id} "
        f"on {torch.cuda.get_device_name(0)} "
        f"(bf16_supported={torch.cuda.is_bf16_supported()})"
    )
    pipeline = Pipeline.from_pretrained(
        args.model_id,
        token=hf_token,
        revision="main",
    ).to(torch.device("cuda"))

    # Warm-up — JIT, kernel autotune, lazy module init. Discard timing.
    print(f"[{args.instance_tag}] warm-up pass on {audios[0].name}")
    _run_one(pipeline, audios[0], "fp32", autocast_for_diarize)

    results: list[dict[str, Any]] = []
    for dtype_name in dtype_names:
        for audio in audios:
            timings: list[float] = []
            peak: int = 0
            result: Any = None
            for _ in range(args.passes):
                elapsed, peak, result = _run_one(
                    pipeline, audio, dtype_name, autocast_for_diarize
                )
                timings.append(elapsed)

            # Drop the slowest pass (typically the cold-cache run)
            if len(timings) > 1:
                trimmed = sorted(timings)[:-1]
            else:
                trimmed = timings
            chosen = sum(trimmed) / len(trimmed)

            sd = extract_annotation(result)
            rttm_path = out_dir / f"{args.instance_tag}_{dtype_name}_{audio.stem}.rttm"
            with open(rttm_path, "w") as fh:
                sd.write_rttm(fh)

            row = {
                "instance": args.instance_tag,
                "dtype": dtype_name,
                "audio": audio.stem,
                "wall_s": round(chosen, 3),
                "all_passes_s": [round(t, 3) for t in timings],
                "peak_vram_mb": peak,
                "num_speakers": len(sd.labels()),
                "num_turns": sum(1 for _ in sd.itertracks()),
            }
            results.append(row)
            print(json.dumps(row))

    summary_path = out_dir / f"results_{args.instance_tag}.json"
    with open(summary_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"[{args.instance_tag}] wrote {summary_path}")


if __name__ == "__main__":
    main()
