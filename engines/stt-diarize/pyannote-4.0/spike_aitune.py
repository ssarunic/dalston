"""M89 AITune spike — benchmark pyannote Community-1 with and without AITune.

Throwaway: runs outside the engine HTTP shell and is not wired into Docker.
Execute on a GPU host with PyTorch 2.7+, TensorRT 10.5+, and an HF_TOKEN that
has accepted the pyannote/speaker-diarization-community-1 gated-model licence.

Usage:
    scripts/fetch_ami_spike.sh
    export HF_TOKEN=hf_xxx
    python engines/stt-diarize/pyannote-4.0/spike_aitune.py \
        --corpus tests/audio/ami \
        --first-seconds 300 \
        --out results.json

Output: a JSON list of {backend, file, audio_seconds, elapsed_s,
ms_per_audio_s, peak_vram_gb, num_speakers, der}. DER is only present when a
reference RTTM is found at <corpus>/rttm/<stem>.rttm.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import torch
import torchaudio
from pyannote.audio import Pipeline


def load_cropped(audio_path: Path, first_seconds: int | None) -> dict[str, Any]:
    waveform, sr = torchaudio.load(str(audio_path))
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if first_seconds is not None:
        waveform = waveform[:, : first_seconds * sr]
    return {"waveform": waveform, "sample_rate": sr}


def extract_annotation(result: Any):
    # pyannote 4.0 community pipeline returns DiarizationResponse with
    # .speaker_diarization; 3.x returns an Annotation directly.
    return getattr(result, "speaker_diarization", result)


def bench_one(
    pipeline: Any,
    audio_path: Path,
    first_seconds: int | None,
) -> dict[str, Any]:
    inputs = load_cropped(audio_path, first_seconds)
    audio_seconds = inputs["waveform"].shape[-1] / inputs["sample_rate"]

    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    result = pipeline(inputs)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    peak_vram = torch.cuda.max_memory_allocated()

    annotation = extract_annotation(result)
    speakers = {label for _, _, label in annotation.itertracks(yield_label=True)}

    return {
        "audio_seconds": round(audio_seconds, 2),
        "elapsed_s": round(elapsed, 3),
        "ms_per_audio_s": round(1000 * elapsed / audio_seconds, 2),
        "peak_vram_gb": round(peak_vram / (1024**3), 3),
        "num_speakers": len(speakers),
        "_annotation": annotation,  # stripped before JSON dump
    }


def compute_der(hypothesis, rttm_path: Path) -> float | None:
    try:
        from pyannote.database.util import load_rttm
        from pyannote.metrics.diarization import DiarizationErrorRate
    except ImportError:
        return None

    references = load_rttm(str(rttm_path))
    if not references:
        return None
    reference = next(iter(references.values()))
    metric = DiarizationErrorRate()
    return float(metric(reference, hypothesis))


def tune_pipeline(pipeline: Any, backend: str) -> Any:
    # AITune exposes a single-line tuning entry point per its README. If the
    # actual import path differs in the shipped version, adjust here — this is
    # the one thing most likely to need a fix on the host.
    import aitune

    return aitune.tune(pipeline, backend=backend)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--backends",
        nargs="+",
        default=["baseline", "inductor", "torch_tensorrt"],
    )
    parser.add_argument("--first-seconds", type=int, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--model", default="pyannote/speaker-diarization-community-1"
    )
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args()

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("HF_TOKEN environment variable required", file=sys.stderr)
        return 1

    if not torch.cuda.is_available():
        print("CUDA device required", file=sys.stderr)
        return 1

    audio_files = sorted(args.corpus.glob("*.wav"))
    if not audio_files:
        print(f"No .wav files in {args.corpus}", file=sys.stderr)
        return 1

    rttm_dir = args.corpus / "rttm"

    results: list[dict[str, Any]] = []
    for backend in args.backends:
        print(f"\n=== backend={backend} ===", flush=True)
        pipeline = Pipeline.from_pretrained(
            args.model, token=hf_token, revision="main"
        ).to(torch.device("cuda"))

        if backend != "baseline":
            try:
                pipeline = tune_pipeline(pipeline, backend)
            except Exception as exc:
                print(f"  tune failed for {backend}: {exc}", file=sys.stderr)
                results.append({"backend": backend, "error": str(exc)})
                continue

        # Warm up on the smallest file so first-call compile/cache cost
        # doesn't pollute the benchmark.
        warmup_file = min(audio_files, key=lambda p: p.stat().st_size)
        for _ in range(args.warmup):
            bench_one(pipeline, warmup_file, args.first_seconds)

        for audio in audio_files:
            print(f"  {audio.name}", flush=True)
            row = bench_one(pipeline, audio, args.first_seconds)
            hypothesis = row.pop("_annotation")

            rttm_path = rttm_dir / f"{audio.stem}.rttm"
            if rttm_path.exists():
                row["der"] = compute_der(hypothesis, rttm_path)

            row["backend"] = backend
            row["file"] = audio.name
            results.append(row)

        del pipeline
        torch.cuda.empty_cache()

    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
