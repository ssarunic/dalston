# M90 Mixed-Precision Diarization — Benchmark Results

**Date:** 2026-05-16
**Tester:** Sasa Sarunic (with Claude Code)
**Runbook:** [M90-mixed-precision-benchmark.md](M90-mixed-precision-benchmark.md)
**Milestone:** [M90-diarize-mixed-precision.md](../plan/milestones/M90-diarize-mixed-precision.md)

## Recommendation

**Do not promote `DALSTON_DIARIZE_DTYPE=auto` to default on g6 / L4-class GPUs.** bf16 fails three of five acceptance thresholds (mean speedup, mean drift DER, worst-file drift DER). The only material win is a ~38 % VRAM reduction; speedup is bottlenecked by CPU-side clustering (VBx / AHC / PLDA) which autocast does not address.

Keep the env var + per-job override in place as an opt-in for operators who explicitly want the VRAM headroom on coloc-tight hosts. The g4dn / T4 fp16 path was not validated in this round and is deferred to a follow-up.

---

## Summary

| Class | dtype | Mean speedup | Mean drift DER | Worst-file drift | Δ spk = 0 | NaN/inf | Verdict |
|---|---|---|---|---|---|---|---|
| g6 (L4) | fp32 → bf16 | **1.08×** ❌ (threshold ≥ 1.6×) | **5.06 %** ❌ (threshold < 1.0 %) | **12.67 %** ❌ (threshold < 2.0 %) | 10 / 10 ✅ | 0 ✅ | **FAIL — do not ship as default** |
| g4dn (T4) | fp32 → fp16 | — | — | — | — | — | **Deferred** |

---

## Environment

### g6.xlarge (NVIDIA L4)

| Item | Value |
|---|---|
| Instance type | `g6.xlarge` |
| GPU | NVIDIA L4 (Ada, sm_89), 24 GB VRAM |
| Driver | `580.126.09` |
| Host CUDA | `13.0` |
| Container | `ghcr.io/ssarunic/dalston/stt-diarize-pyannote:latest` (built from `4a8effa0`) |
| torch | `2.11.0+cu126` |
| pyannote.audio | `4.0.4` |
| Model | `pyannote/speaker-diarization-community-1` |
| `--bypass-chunking` | not used (L4 24 GB headroom ample; pyannote's internal chunker still ran) |
| Passes per (dtype, audio) | 2 (slowest dropped) |
| `torch.cuda.is_bf16_supported()` | `True` |

### g4dn.xlarge (NVIDIA T4)

Deferred. No g4dn / fp16 run was completed in this round; the g4dn instance was terminated before the bench was prepared. See [Follow-ups](#follow-ups).

---

## g6.xlarge — fp32 vs bf16

### Per-file results

| audio | fp32 s | bf16 s | speedup | drift DER | Δ speakers | Δ turns | bf16 peak VRAM |
|---|---:|---:|---:|---:|---:|---:|---:|
| 01 | 25.728 | 24.272 | 1.06× | 1.60 % | +0 | +13 (60 → 73) | 1005 MB |
| 02 | 48.998 | 45.570 | 1.08× | **5.83 %** | +0 | +68 (311 → 379) | 1005 MB |
| 03 | 48.920 | 45.476 | 1.08× | **5.10 %** | +0 | +94 (245 → 339) | 1005 MB |
| 04 | 48.693 | 45.140 | 1.08× | **3.17 %** | +0 | +60 (100 → 160) | 1005 MB |
| 05 | 48.856 | 45.219 | 1.08× | **3.56 %** | +0 | +58 (120 → 178) | 1005 MB |
| 06 | 49.123 | 45.384 | 1.08× | **6.89 %** | +0 | +154 (208 → 362) | 1005 MB |
| 07 | 49.102 | 45.278 | 1.08× | 1.65 % | +0 | +46 (52 → 98) | 1005 MB |
| 08 | 28.989 | 26.801 | 1.08× | 1.21 % | +0 | +18 (85 → 103) | 1005 MB |
| 09 | 49.581 | 45.635 | 1.09× | **8.95 %** | +0 | +168 (184 → 352) | 1005 MB |
| 10 | 49.138 | 44.982 | 1.09× | **12.67 %** | +0 | +281 (219 → 500) | 1005 MB |
| **mean** | **44.61** | **41.38** | **1.08×** | **5.06 %** | +0 | — | 1005 MB |

fp32 peak VRAM was 1629 MB across all files; bf16 was 1005 MB across all files (**−38 %**).

### Acceptance thresholds (g6 / bf16)

| Metric | Threshold | Actual | Status |
|---|---|---|---|
| Mean speedup vs fp32 | ≥ 1.6× | 1.08× | ❌ FAIL (33 % short) |
| Mean drift DER | < 1.0 % | 5.06 % | ❌ FAIL (5× over) |
| Worst-file drift DER | < 2.0 % | 12.67 % (file 10) | ❌ FAIL (6× over) |
| Δ speakers = 0 | ≥ 9 / 10 files | 10 / 10 | ✅ |
| NaN / inf in output | 0 | 0 | ✅ |

Files passing per-file drift threshold (< 2.0 %): **3 / 10** (01, 07, 08 — all short / single-conversation episodes).

---

## Diagnosis

### Why speedup is only 1.08×

The pyannote 4.0 pipeline interleaves three workloads:

1. **GPU segmentation** (Conformer-style net) — bf16 helps.
2. **GPU embedding** (ResNet34) — bf16 helps.
3. **CPU clustering** (VBx / AHC) + **PLDA scoring** — bf16 does not apply.

Wall-time profile suggests CPU stages 3 + I/O dominate, so doubling GPU forward speed moves total wall time by single digits. This is structural — no amount of dtype tuning fixes it. Speedup would only matter if either (a) the GPU forward grew enough to dominate (longer audio, larger model), or (b) the CPU clustering moved to GPU.

### Why drift DER is high

Same `num_speakers` on every file (✅) but consistently more turn boundaries in bf16 (+13 to +281). The segmentation softmax is producing slightly different boundary probabilities under reduced precision; small differences accumulate into many more boundary flips, which inflates DER even though the speaker assignment is right.

Drift correlates loosely with episode length and turn density:

- Short / clean episodes (01, 07, 08): drift < 2 %.
- Long / dense conversations (09, 10): drift 9–13 %.

File 10 is the worst case: 281 extra turns, 12.67 % DER. Worth keeping as a regression fixture before any future precision change ships.

### What worked

- **No NaN / inf** on any file — autocast (with fp32 weights) avoids the failure class that a naive `model.half()` would hit on quiet audio. This validates the design choice in `dalston/engine_sdk/diarize_dtype.py`.
- **Δ speakers = 0 on all 10 files** — even with shifted boundaries, speaker identity recovery is unaffected.
- **VRAM −38 %** — bf16 activations are half the size; clustering buffers stay fp32 so the saving is real and consistent (1005 MB vs 1629 MB on every file).

---

## Known bugs found during validation

### Bug 1: `Got unsupported ScalarType BFloat16` on first bf16 call

**Symptom:** running the engine with `DALSTON_DIARIZE_DTYPE=bf16` crashes on the first job:

```
File "/usr/local/lib/python3.12/dist-packages/pyannote/audio/core/inference.py", line 211, in __convert
    return conversion(output).cpu().numpy()
TypeError: Got unsupported ScalarType BFloat16
```

**Cause:** pyannote-audio 4.0.4 calls `.cpu().numpy()` on segmentation outputs without upcasting. numpy has no bf16 dtype. Under autocast the model output is bf16, so the conversion raises.

**Bench workaround (already applied):** `dalston/tools/bench_diarize_precision.py` monkey-patches `torch.Tensor.numpy` at startup to upcast bf16/fp16 → fp32 transparently.

**Engine fix required (not yet shipped):** The same workaround needs to land in the engine path. Without it, the existing `DALSTON_DIARIZE_DTYPE` kill-switch / per-job `dtype` override is non-functional for bf16 (and likely fp16 too — same code path). Single follow-up edit to `dalston/engine_sdk/diarize_dtype.py` to install the patch alongside `autocast_for_diarize`.

---

## Follow-ups

1. **Land the bf16 numpy patch in the engine** so `DALSTON_DIARIZE_DTYPE=bf16` and per-job `dtype` overrides actually work end-to-end. Without this the kill-switch is broken for the precisely the path it's meant to gate. *(Required before anything bf16-related ships, regardless of default.)*
2. **Run g4dn / fp16 benchmark** on a fresh g4dn.xlarge spot instance. fp16 on Turing has a different speedup-vs-drift profile and may justify keeping bf16-equivalent ops on T4 (where VRAM is genuinely tight under the M89 coloc budgets — 10G nemo / 2G pyannote on 16G).
3. **Pin file 10 as a regression fixture.** 281 extra turns / 12.67 % drift is the strongest signal; add to whatever regression set guards future precision or model-revision changes.
4. **Decide on opt-in surface.** Leave `DALSTON_DIARIZE_DTYPE=auto` as an opt-in env var (default stays `fp32`), or remove autocast entirely if the VRAM saving doesn't matter to ops. Decision deferred until after (2).

---

## Artifacts

- Per-run RTTM files: `bench/results_g6/g6_{fp32,bf16}_{01..10}.rttm`
- Summary JSON: `bench/results_g6/results_g6.json`
- Compare table (raw): regenerate via `python -m dalston.tools.bench_diarize_compare --results bench/results_g6/results_g6.json --rttm-dir bench/results_g6 --reference-dtype fp32 --target-dtype bf16`
