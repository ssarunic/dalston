# M90 Mixed-Precision Diarization — Benchmark Runbook

Validates the precision change planned in [M90](../plan/milestones/M90-diarize-mixed-precision.md) on real GPU instances before flipping the production default.

## What we're measuring

For each `(GPU class, dtype)` pair, on each of 10 podcast episodes:

- **Wall-clock GPU inference time** for the pyannote pipeline call.
- **Drift DER** = `DiarizationErrorRate(fp32_output, mixed_precision_output)` on the same instance. This is **not absolute accuracy** — we have no ground truth — it measures how much the precision change shifts the diarization.
- **Δ speakers** = `num_speakers(mixed) − num_speakers(fp32)`.
- **Peak VRAM** during inference (via `nvidia-smi --query-gpu=memory.used`).

## Acceptance thresholds

| Metric | g4dn (fp16) | g6 (bf16) |
| --- | --- | --- |
| Mean speedup vs fp32 | ≥ 1.4× | ≥ 1.6× |
| Mean drift DER | < 1.5% | < 1.0% |
| Worst-file drift DER | < 3.0% | < 2.0% |
| Δ speakers = 0 | ≥ 9 / 10 files | ≥ 9 / 10 files |
| NaN / inf in output | 0 | 0 |

Failing any threshold blocks promoting `auto` to default; the offending audio becomes a regression fixture.

---

## 1. One-time prep (do on your laptop)

### 1.1 Download the 10 sample podcasts

```bash
mkdir -p bench/audio/raw bench/audio/wav
cd bench/audio/raw

cat > urls.txt <<'EOF'
https://flex.acast.com/audio.guim.co.uk/2026/05/14-60341-v2140526.mp3
https://sphinx.acast.com/p/acast/s/dannyinthevalley/e/69fc80f6dcc3292c132dabf6/media.mp3
https://sphinx.acast.com/p/acast/s/dannyinthevalley/e/69f20f93be5ab6849c55f6ab/media.mp3
https://cdn.simplecast.com/media/audio/transcoded/b3414ac6-61c8-4752-8722-491e1457c3bf/2c08ad29-5b79-42c0-a40a-6c1af4327f2f/episodes/audio/group/e003e405-cd36-4aa4-8163-ed60d9bedfb7/group-item/c022af70-bcd6-4f6a-b999-a31e2e2a868b/128_default_tc.mp3
https://audio.transistor.fm/m/shows/64902/daba206a727f710a01334b30fc7e0f8a.mp3
https://audio.transistor.fm/m/shows/64902/18d77bbade78e3360bac5f0f2fe3edf1.mp3
https://cdn.podcast.co/media/28cdceb9-501a-4191-87e4-7cb1f77eeee5/final/3b9443fc-d296-4d21-bc69-6088399d7389.mp3
https://cdn.podcast.co/media/28cdceb9-501a-4191-87e4-7cb1f77eeee5/final/5f50cdf7-f678-4635-a180-02c3c3e248fe.mp3
https://content.blubrry.com/takeituneasy/lex_ai_lars_brownworth.mp3
https://content.blubrry.com/takeituneasy/lex_ai_jensen_huang.mp3
EOF

# Stable filenames: 01.mp3 … 10.mp3 so RTTM keys stay short
nl -ba urls.txt | while read i url; do
  printf -v name "%02d.mp3" "$i"
  curl -fL --retry 3 -o "$name" "$url"
done
```

### 1.2 Convert to 16 kHz mono WAV (matches dalston prepare-stage output)

```bash
cd bench/audio
for f in raw/*.mp3; do
  out="wav/$(basename "${f%.mp3}").wav"
  ffmpeg -y -loglevel error -i "$f" -ac 1 -ar 16000 -c:a pcm_s16le "$out"
done

# Verify durations — sanity check ratio of long vs short podcasts
for f in wav/*.wav; do
  printf "%s  %s\n" "$(basename $f)" \
    "$(ffprobe -v error -show_entries format=duration -of csv=p=0 $f)"
done
```

### 1.3 Upload to S3

```bash
aws s3 sync bench/audio/wav/ s3://dalston-bench/audio/m90/ \
  --exclude "*" --include "*.wav"
```

This is shared across both instances so neither pays the download + transcode cost on GPU time.

---

## 2. Benchmark script

Lives at `dalston/tools/bench_diarize_precision.py`. Self-contained — only depends on `pyannote.audio`, `torch`, and `pyannote.metrics`.

```python
"""Diarization precision benchmark for M90.

Usage:
    python -m dalston.tools.bench_diarize_precision \
        --instance-tag g4dn \
        --dtypes fp32,fp16 \
        --audio-dir bench/audio/wav \
        --out-dir bench_out \
        [--bypass-chunking]

Outputs:
    bench_out/results_<instance_tag>.json     summary table
    bench_out/<instance_tag>_<dtype>_<stem>.rttm  per-run diarization
"""

from __future__ import annotations
import argparse, json, os, subprocess, time
from contextlib import nullcontext
from pathlib import Path

import torch
from pyannote.audio import Pipeline


DTYPE_MAP = {"fp32": None, "fp16": torch.float16, "bf16": torch.bfloat16}


def autocast_ctx(dtype):
    if dtype is None:
        return nullcontext()
    return torch.autocast("cuda", dtype=dtype)


def peak_vram_mb() -> int:
    """Sample GPU memory.used from nvidia-smi (works inside Docker)."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip().split("\n")[0])


def diarize(pipeline, audio_path: Path, dtype, params: dict):
    with autocast_ctx(dtype):
        return pipeline(str(audio_path), **params)


def get_annotation(result):
    """pyannote 4.0 returns DiarizationResponse; 3.x returns Annotation directly."""
    return result.speaker_diarization if hasattr(result, "speaker_diarization") else result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-tag", required=True, help="e.g. g4dn, g6")
    ap.add_argument("--dtypes", required=True, help="comma-separated: fp32,fp16 or fp32,bf16")
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--bypass-chunking", action="store_true",
                    help="Force single-pass diarization (only on instances with enough VRAM)")
    ap.add_argument("--passes", type=int, default=2,
                    help="Runs per (dtype, audio) — median of last (passes-1) is reported")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audios = sorted(Path(args.audio_dir).glob("*.wav"))
    assert len(audios) > 0, f"no .wav files in {args.audio_dir}"

    if args.bypass_chunking:
        os.environ["DALSTON_MAX_DIARIZE_CHUNK_S"] = "999999"

    print(f"[{args.instance_tag}] loading pipeline...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-community-1",
        token=os.environ["HF_TOKEN"], revision="main",
    ).to(torch.device("cuda"))
    print(f"[{args.instance_tag}] GPU: {torch.cuda.get_device_name(0)}, "
          f"bf16_supported={torch.cuda.is_bf16_supported()}")

    # Warm-up — JIT + kernel autotune. Discard.
    print(f"[{args.instance_tag}] warm-up pass...")
    diarize(pipeline, audios[0], None, {})
    torch.cuda.synchronize()

    results = []
    for dtype_name in args.dtypes.split(","):
        dtype = DTYPE_MAP[dtype_name]
        for audio in audios:
            timings = []
            for p in range(args.passes):
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                result = diarize(pipeline, audio, dtype, {})
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - t0
                timings.append(elapsed)
                peak = torch.cuda.max_memory_allocated() // (1024 * 1024)

            # Use the median of all but the first pass (cold-cache filter)
            sorted_runs = sorted(timings[1:]) if args.passes > 1 else timings
            chosen = sorted_runs[len(sorted_runs) // 2]

            # Persist RTTM from the final run
            sd = get_annotation(result)
            rttm_path = out_dir / f"{args.instance_tag}_{dtype_name}_{audio.stem}.rttm"
            with open(rttm_path, "w") as f:
                sd.write_rttm(f)

            row = {
                "instance": args.instance_tag,
                "dtype": dtype_name,
                "audio": audio.stem,
                "wall_s": round(chosen, 2),
                "all_passes_s": [round(t, 2) for t in timings],
                "peak_vram_mb": peak,
                "num_speakers": len(sd.labels()),
                "num_turns": sum(1 for _ in sd.itertracks()),
            }
            results.append(row)
            print(json.dumps(row))

    summary_path = out_dir / f"results_{args.instance_tag}.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[{args.instance_tag}] wrote {summary_path}")


if __name__ == "__main__":
    main()
```

### Drift / speedup analysis (run anywhere, after both instances are done)

```python
# dalston/tools/bench_diarize_compare.py
"""Pairwise drift DER + speedup table for M90 results."""

from __future__ import annotations
import argparse, json
from pathlib import Path

from pyannote.database.util import load_rttm
from pyannote.metrics.diarization import DiarizationErrorRate


def load_results(path: Path) -> dict[tuple[str, str], dict]:
    out = {}
    for row in json.loads(path.read_text()):
        out[(row["dtype"], row["audio"])] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="results_<instance>.json")
    ap.add_argument("--rttm-dir", required=True)
    ap.add_argument("--reference-dtype", default="fp32")
    ap.add_argument("--target-dtype", required=True, help="fp16 or bf16")
    args = ap.parse_args()

    results = load_results(Path(args.results))
    rttm_dir = Path(args.rttm_dir)
    inst = list(results.values())[0]["instance"]

    der_metric = DiarizationErrorRate()
    print(f"| audio | dur s | {args.reference_dtype} s | {args.target_dtype} s | speedup | drift DER | Δ spk |")
    print("|---|---|---|---|---|---|---|")

    speedups, drifts = [], []
    for (dtype, audio), row in sorted(results.items()):
        if dtype != args.target_dtype:
            continue
        ref_row = results[(args.reference_dtype, audio)]

        ref_rttm = rttm_dir / f"{inst}_{args.reference_dtype}_{audio}.rttm"
        hyp_rttm = rttm_dir / f"{inst}_{args.target_dtype}_{audio}.rttm"
        ref = next(iter(load_rttm(str(ref_rttm)).values()))
        hyp = next(iter(load_rttm(str(hyp_rttm)).values()))
        drift = der_metric(ref, hyp) * 100

        speedup = ref_row["wall_s"] / row["wall_s"]
        delta_spk = row["num_speakers"] - ref_row["num_speakers"]

        speedups.append(speedup)
        drifts.append(drift)
        print(f"| {audio} | ? | {ref_row['wall_s']} | {row['wall_s']} | "
              f"{speedup:.2f}× | {drift:.2f}% | {delta_spk:+d} |")

    print(f"\n**mean speedup**: {sum(speedups)/len(speedups):.2f}×  "
          f"**mean drift**: {sum(drifts)/len(drifts):.2f}%")


if __name__ == "__main__":
    main()
```

---

## 3. Run on g4dn (T4) — fp32 vs fp16

```bash
# Spot instance recommended: g4dn.xlarge (~$0.20/hr spot)
./infra/scripts/dalston-aws launch gpu \
    --gpu-type g4dn.xlarge --spot \
    --tag m90-bench

# SSH in, then:
ssh ubuntu@<ip>

# Pull bench audio from S3 to local NVMe
mkdir -p ~/bench/{audio,out}
aws s3 sync s3://dalston-bench/audio/m90/ ~/bench/audio/

# Persistent HF cache so second dtype run doesn't redownload
export HF_HOME=/mnt/nvme/hf_cache
mkdir -p $HF_HOME
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# Make sure nothing else is on the GPU
nvidia-smi   # expect zero processes

cd ~/dalston   # assumes repo is cloned; otherwise copy bench script over

# Run the benchmark — 10 audios × 2 dtypes × 2 passes ≈ 2–4 hours
python -m dalston.tools.bench_diarize_precision \
    --instance-tag g4dn \
    --dtypes fp32,fp16 \
    --audio-dir ~/bench/audio \
    --out-dir ~/bench/out

# Pull results back to laptop
scp -r ubuntu@<ip>:~/bench/out ./bench/results_g4dn/
```

Tear down the instance immediately when done:

```bash
./infra/scripts/dalston-aws teardown --tag m90-bench
```

---

## 4. Run on g6 (L4) — fp32 vs bf16

```bash
./infra/scripts/dalston-aws launch gpu \
    --gpu-type g6.xlarge --spot \
    --tag m90-bench

# Same procedure as g4dn, with bf16 as the second dtype:
python -m dalston.tools.bench_diarize_precision \
    --instance-tag g6 \
    --dtypes fp32,bf16 \
    --audio-dir ~/bench/audio \
    --out-dir ~/bench/out

scp -r ubuntu@<ip>:~/bench/out ./bench/results_g6/
./infra/scripts/dalston-aws teardown --tag m90-bench
```

---

## 5. Compare and write up

```bash
# Drift + speedup for g4dn
python -m dalston.tools.bench_diarize_compare \
    --results bench/results_g4dn/results_g4dn.json \
    --rttm-dir bench/results_g4dn \
    --reference-dtype fp32 --target-dtype fp16 \
    > bench/g4dn_table.md

# Same for g6
python -m dalston.tools.bench_diarize_compare \
    --results bench/results_g6/results_g6.json \
    --rttm-dir bench/results_g6 \
    --reference-dtype fp32 --target-dtype bf16 \
    > bench/g6_table.md
```

Paste both tables into `docs/testing/M90-mixed-precision-results.md` along with:

- GPU model, driver version, CUDA version, torch version per instance
- Whether `--bypass-chunking` was used and on which files
- Any audio that failed thresholds (file, drift, suspected cause)
- VRAM headroom column (`peak_vram_mb` from JSON, compared to GPU total)

---

## 6. Gotchas to watch for

- **Wall time inflation from chunking.** Audios > 15 min hit the chunked path, which spawns `ffmpeg` subprocesses and runs CPU-bound cross-chunk speaker linking. That work doesn't benefit from autocast. Either report two columns (chunked + bypass-chunking) for long audio, or only use `--bypass-chunking` on the instances with enough VRAM (g6.2xlarge+) — T4 may OOM on a 3-hour single-pass.
- **First call ≠ steady state.** The pass-0 column in `all_passes_s` will always be slower than the rest. The harness already takes the median of pass ≥ 1 for the headline number.
- **Don't forget `torch.cuda.synchronize()`.** It's in the harness around timing — if you write your own version, omitting it makes fp16 look ~4× faster than reality because you're measuring async launch latency, not actual work.
- **HF model download race.** On the first run after instance boot, the pipeline downloads ~600 MB. Keep `HF_HOME` on persistent storage so the second dtype run reuses it.
- **NaN detection.** If a run produces zero speakers or zero turns on audio that fp32 handled fine, that's an autocast failure on that file. Re-run with `--dtypes fp32` only to confirm, then file it as a regression-test fixture.
- **GPU thermal/clock variance.** Cold instances boost higher than warmed ones. The 2-pass median absorbs most of this, but if you see >15% variance between passes 1 and 2, run a third pass.

---

## 7. What success looks like at write-up time

A short report in `docs/testing/M90-mixed-precision-results.md` that:

1. States the GPU + driver + torch versions for both instance classes.
2. Has two markdown tables (g4dn fp16 vs fp32, g6 bf16 vs fp32) with per-file rows and aggregate means.
3. Confirms all acceptance thresholds met (or names the specific file(s) that miss, with diagnosis).
4. Names the offending op or audio characteristic for any failure (e.g. "softmax overflow on lex_ai_jensen_huang at t=4823s, caused by sustained loud audio").
5. Closes with one-line recommendation: ship `auto` as default, or hold pending fix.
