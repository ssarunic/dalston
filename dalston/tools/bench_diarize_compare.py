"""Pairwise drift-DER + speedup table for M90 results.

Consumes the JSON + RTTM files produced by
``dalston.tools.bench_diarize_precision`` and prints a markdown summary.

The "drift DER" computed here is *not* absolute diarization error —
there are no ground-truth labels. It measures how much the mixed-precision
output disagrees with the fp32 output on the same instance. A value
below the M90 acceptance thresholds means the precision change did
not meaningfully alter the diarization.

Usage::

    python -m dalston.tools.bench_diarize_compare \\
        --results bench_out/results_g4dn.json \\
        --rttm-dir bench_out \\
        --target-dtype fp16
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_rttm(path: Path) -> Any:
    """Load an RTTM and return the single Annotation it contains."""
    from pyannote.database.util import load_rttm

    parsed = load_rttm(str(path))
    if not parsed:
        raise RuntimeError(f"No annotations parsed from {path}")
    return next(iter(parsed.values()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--results",
        required=True,
        help="Path to results_<instance>.json from bench_diarize_precision.",
    )
    parser.add_argument("--rttm-dir", required=True)
    parser.add_argument("--reference-dtype", default="fp32")
    parser.add_argument(
        "--target-dtype",
        required=True,
        help="fp16 or bf16 — the dtype being compared against the reference.",
    )
    args = parser.parse_args()

    from pyannote.metrics.diarization import DiarizationErrorRate

    rows = json.loads(Path(args.results).read_text())
    by_key: dict[tuple[str, str], dict[str, Any]] = {
        (row["dtype"], row["audio"]): row for row in rows
    }
    instance_tag = rows[0]["instance"]
    rttm_dir = Path(args.rttm_dir)

    ref_dtype = args.reference_dtype
    tgt_dtype = args.target_dtype

    print(
        f"| audio | {ref_dtype} s | {tgt_dtype} s | speedup | "
        "drift DER | Δ speakers | Δ turns |"
    )
    print("|---|---|---|---|---|---|---|")

    speedups: list[float] = []
    drifts: list[float] = []
    delta_spk_zero = 0
    total = 0
    der_metric = DiarizationErrorRate()
    worst: tuple[str, float] = ("", 0.0)

    audios = sorted({audio for (_, audio) in by_key.keys()})
    for audio in audios:
        ref_row = by_key.get((ref_dtype, audio))
        tgt_row = by_key.get((tgt_dtype, audio))
        if ref_row is None or tgt_row is None:
            continue

        ref_rttm = rttm_dir / f"{instance_tag}_{ref_dtype}_{audio}.rttm"
        tgt_rttm = rttm_dir / f"{instance_tag}_{tgt_dtype}_{audio}.rttm"
        ref_ann = _load_rttm(ref_rttm)
        tgt_ann = _load_rttm(tgt_rttm)
        drift = float(der_metric(ref_ann, tgt_ann)) * 100.0

        speedup = ref_row["wall_s"] / tgt_row["wall_s"]
        delta_spk = tgt_row["num_speakers"] - ref_row["num_speakers"]
        delta_turns = tgt_row["num_turns"] - ref_row["num_turns"]

        speedups.append(speedup)
        drifts.append(drift)
        total += 1
        if delta_spk == 0:
            delta_spk_zero += 1
        if drift > worst[1]:
            worst = (audio, drift)

        print(
            f"| {audio} | {ref_row['wall_s']} | {tgt_row['wall_s']} | "
            f"{speedup:.2f}× | {drift:.2f}% | "
            f"{delta_spk:+d} | {delta_turns:+d} |"
        )

    if total == 0:
        raise SystemExit("No paired rows found — check --target-dtype.")

    mean_speedup = sum(speedups) / len(speedups)
    mean_drift = sum(drifts) / len(drifts)
    print()
    print(f"**mean speedup**: {mean_speedup:.2f}×")
    print(f"**mean drift DER**: {mean_drift:.2f}%")
    print(f"**worst-file drift**: {worst[0]} → {worst[1]:.2f}%")
    print(f"**Δ speakers = 0**: {delta_spk_zero} / {total}")


if __name__ == "__main__":
    main()
