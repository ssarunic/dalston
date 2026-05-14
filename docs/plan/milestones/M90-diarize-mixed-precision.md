# M90: Mixed-Precision Diarization (fp16 / bf16)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Run pyannote diarization in fp16 (T4) or bf16 (A10G / L4) to cut GPU wall time by 1.4–2× without changing diarization quality |
| **Duration**       | 2–3 days                                                     |
| **Dependencies**   | M84 (VRAM Budget Management & Diarization Chunking) — uses its chunked path; M89 (GPU-Aware VRAM Budgets) — uses its GPU-family detection |
| **Deliverable**    | Autocast-gated diarization, runtime dtype auto-detect, env override, benchmark runbook, validation results checked in |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator running diarization on g4dn / g5 / g6 spot instances interchangeably, I want pyannote to use the fastest safe precision for the GPU I happen to be on, so that batch throughput goes up without me having to tune anything per-instance and without diarization quality degrading on any audio."*

---

## Outcomes

| Scenario | Current | After M90 (operator opt-in) |
| -------- | ------- | ---------- |
| Out-of-the-box behaviour on first deploy (no env override) | fp32 | **Still fp32** — the precision change is opt-in until the M90.5 per-GPU validation is checked in; operators flip via `DALSTON_DIARIZE_DTYPE` once results pass acceptance thresholds |
| 90-minute podcast on g4dn (T4) with `DALSTON_DIARIZE_DTYPE=fp16` | fp32, RTF ≈ 0.15 → ~14 min GPU time | autocast-fp16, RTF ≈ 0.10 → ~9 min GPU time (~1.5× speedup) |
| 90-minute podcast on g5 (A10G) or g6 (L4) with `DALSTON_DIARIZE_DTYPE=bf16` | fp32, RTF ≈ 0.13 → ~12 min | autocast-bf16, RTF ≈ 0.07 → ~6 min (~1.8× speedup) |
| Operator moves from g4dn to g6 mid-week with `DALSTON_DIARIZE_DTYPE=auto` | No precision change; misses 30–40% headroom on Ampere/Ada GPUs | Engine auto-picks bf16 on capable GPUs, fp16 on T4 — no manual config |
| Quiet / noisy audio that would NaN under naive fp16 cast | N/A (fp32 today) | Autocast keeps reduction ops (softmax accumulators, layer-norm stats) in fp32 → no NaN regression; explicit per-job fallback path if a file does misbehave |
| VRAM headroom on T4 with `max_chunk_s=900` and fp16 enabled | ~1–2 GB headroom on long audio (pyannote reconstruction spike) | Activations roughly halved → headroom grows; chunk cap can safely rise (deferred to M84+ tuning, not part of M90) |

---

## Motivation

Today the pyannote engine runs at fp32 by default. [`engines/stt-diarize/pyannote-4.0/engine.py:107-110`](../../../engines/stt-diarize/pyannote-4.0/engine.py#L107-L110) loads weights and moves them to the GPU without any precision change, and the inference calls at [`engine.py:185`](../../../engines/stt-diarize/pyannote-4.0/engine.py#L185) and [`diarize_chunking.py:614`](../../../dalston/engine_sdk/diarize_chunking.py#L614) run in plain fp32. This is the safest setting but leaves significant throughput on the table on every GPU we deploy on:

- **T4 (g4dn)**: dedicated fp16 Tensor Cores, ~8× peak fp16 throughput vs fp32. Real diarization gain ~1.4–1.6× end-to-end.
- **A10G (g5) and L4 (g6)**: native bf16 — same range as fp32, ~1.6–2.2× end-to-end gain.

Two things make this safe to enable now:

1. **Pyannote's pipeline is autocast-tolerant.** The segmentation network is a Conformer-style model and the embedding network is a ResNet34 — both are exactly the workloads `torch.autocast` was designed for. Autocast keeps weights in fp32 and only casts activations on whitelist ops, so the failure modes of a naive `.half()` (NaN on softmax accumulators, dtype mismatch in custom ops) don't apply.
2. **bf16 covers the historic fp16 NaN risk on modern GPUs.** Quiet-audio overflow in fp16 logits is a known pyannote failure mode. bf16 has fp32's exponent range, so on Ampere+ GPUs we use the safer dtype anyway — we only fall back to fp16 on Turing where it's the only Tensor Core path.

Cost story: on the long-tail podcast workload (lex_ai-style 2–3 hour episodes), a 1.6× diarization speedup converts roughly 1:1 into spot-hour savings on the diarize engine, which dominates GPU time for long audio.

---

## Architecture

### Runtime dtype selection

```
┌──────────────────────────────────────────────────────────────────┐
│  PyannoteEngine.__init__                                         │
│                                                                  │
│  self._dtype = resolve_diarize_dtype()                           │
│                                                                  │
│      DALSTON_DIARIZE_DTYPE=fp32  (default — opt-in safety gate)  │
│        │                                                         │
│        └─ returns fp32 unconditionally                           │
│                                                                  │
│      DALSTON_DIARIZE_DTYPE=auto  (operator opt-in, post-M90.5)   │
│        │                                                         │
│        ├─ CUDA absent ─────────────────────────▶ fp32            │
│        ├─ torch.cuda.is_bf16_supported() ──────▶ bf16            │
│        ├─ compute capability ≥ 7 (Turing+) ────▶ fp16            │
│        └─ else ────────────────────────────────▶ fp32            │
│                                                                  │
│      DALSTON_DIARIZE_DTYPE={fp32,fp16,bf16} forces a choice;     │
│      bf16 on unsupported HW falls back to fp16 with a warning.   │
└──────────────────────────────────────────────────────────────────┘
```

### Inference path

```
┌──────────────────────────────────────────────────────────────────┐
│  PyannoteEngine.process                                          │
│                                                                  │
│  with autocast_for_diarize(self._dtype):                         │
│      diarization = pipeline(str(audio_path), **diarization_params)│
│                                                                  │
│  # — or, in the chunked path —                                   │
│                                                                  │
│  run_chunked_diarization(..., dtype=self._dtype)                 │
│      └─▶ with autocast_for_diarize(dtype):                       │
│              raw_result = pipeline(str(chunk_path), ...)         │
└──────────────────────────────────────────────────────────────────┘
```

`autocast_for_diarize(dtype)` is a tiny contextmanager helper that returns:

- `torch.autocast("cuda", dtype=dtype)` when `dtype in {fp16, bf16}`
- a `nullcontext()` when `dtype is None` (fp32 path — unchanged behaviour)

### Per-job override

`DiarizeParams` gains an optional `dtype: Literal["fp32", "fp16", "bf16"] | None`. If the gateway sets it, that wins over engine default. Used by the rare bad-audio fallback path and by the benchmark harness.

---

## Steps

### 90.1: Add `autocast_for_diarize` helper and dtype resolver

**Files modified:**

- `dalston/engine_sdk/diarize_dtype.py` *(new)*

**Deliverables:**

A small module with two pure functions plus a context manager. No imports from the engine, so it's safely importable from both `engine.py` and `diarize_chunking.py`.

```python
# dalston/engine_sdk/diarize_dtype.py
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from typing import Literal
import os
import structlog

DTypeName = Literal["fp32", "fp16", "bf16"]
logger = structlog.get_logger()


def resolve_diarize_dtype(override: str | None = None) -> DTypeName:
    """Pick the fastest safe dtype for diarization on the current GPU.

    Resolution order:
      1. explicit override argument (used by per-job dtype param)
      2. DALSTON_DIARIZE_DTYPE env var
      3. auto-detect based on torch.cuda capabilities

    bf16 on a GPU without hardware bf16 support falls back to fp16
    with a warning (rather than silently using emulated bf16, which
    is slower than fp32).
    """
    requested = (override or os.environ.get("DALSTON_DIARIZE_DTYPE", "auto")).lower()
    if requested not in {"auto", "fp32", "fp16", "bf16"}:
        logger.warning("invalid_diarize_dtype", requested=requested, fallback="auto")
        requested = "auto"

    try:
        import torch
    except ImportError:
        return "fp32"

    if not torch.cuda.is_available():
        return "fp32"

    bf16_supported = torch.cuda.is_bf16_supported()
    cap_major, _ = torch.cuda.get_device_capability()
    fp16_supported = cap_major >= 7  # Volta+

    if requested == "auto":
        if bf16_supported:
            return "bf16"
        if fp16_supported:
            return "fp16"
        return "fp32"

    if requested == "bf16" and not bf16_supported:
        logger.warning("bf16_unsupported_falling_back_to_fp16",
                       device_cap=cap_major)
        return "fp16" if fp16_supported else "fp32"

    if requested == "fp16" and not fp16_supported:
        logger.warning("fp16_unsupported_falling_back_to_fp32",
                       device_cap=cap_major)
        return "fp32"

    return requested  # type: ignore[return-value]


@contextmanager
def autocast_for_diarize(dtype_name: DTypeName):
    """Yield an autocast context for fp16/bf16; nullcontext for fp32."""
    if dtype_name == "fp32":
        with nullcontext():
            yield
        return
    import torch
    torch_dtype = torch.float16 if dtype_name == "fp16" else torch.bfloat16
    with torch.autocast("cuda", dtype=torch_dtype):
        yield
```

---

### 90.2: Wire dtype into the pyannote engine

**Files modified:**

- `engines/stt-diarize/pyannote-4.0/engine.py` — resolve dtype at init, wrap inference call
- `dalston/engine_sdk/diarize_chunking.py` — accept `dtype` param, wrap per-chunk inference

**Deliverables:**

Engine init logs the chosen dtype alongside device:

```python
# engine.py — additions around existing __init__
from dalston.engine_sdk.diarize_dtype import (
    autocast_for_diarize,
    resolve_diarize_dtype,
)

class PyannoteEngine(Engine):
    def __init__(self) -> None:
        super().__init__()
        ...
        self._dtype = resolve_diarize_dtype()
        self.logger.info(
            "pyannote_4_0_engine_initialized",
            device=self._device,
            dtype=self._dtype,
            max_chunk_s=self._max_chunk_s,
        )
```

Both inference call sites get wrapped:

```python
# engine.py — replaces line 185
with autocast_for_diarize(self._dtype):
    diarization = pipeline(str(audio_path), **diarization_params)

# diarize_chunking.py — replaces line 614, threaded through run_chunked_diarization signature
with autocast_for_diarize(dtype):
    raw_result = pipeline(str(chunk_path), **diarization_params)
```

`run_chunked_diarization` gains a `dtype: DTypeName = "fp32"` keyword argument; the engine passes `self._dtype` through.

---

### 90.3: Per-job dtype override (end-to-end)

**Files modified:**

- `dalston/common/pipeline_types.py` — add `dtype` field to `DiarizationRequest`, bump `PIPELINE_SCHEMA_VERSION` 2 → 3
- `dalston/gateway/models/requests.py` — `TranscriptionCreateParams.diarize_dtype` (`Literal["fp32","fp16","bf16"] | None`), forwarded by `to_job_parameters()`
- `dalston/orchestrator/dag.py` — `parameters.get("diarize_dtype")` flows into the diarize task `config`
- `dalston/engine_sdk/http_diarize.py` — `dtype` `Form` field on `POST /v1/diarize` for direct engine calls
- `engines/stt-diarize/pyannote-4.0/engine.yaml` — declare in `config_schema`
- `engines/stt-diarize/pyannote-4.0/engine.py` — `params.dtype or self._dtype` precedence

**Deliverables:**

```yaml
# engine.yaml — additions to config_schema.properties
dtype:
  type: string
  enum: [fp32, fp16, bf16]
  description: |
    Override engine default dtype for this job. Used for bad-audio
    fallback (force fp32) or benchmarking. Defaults to engine setting
    (DALSTON_DIARIZE_DTYPE, "auto" by default).
```

```python
# engine.py — inside process(), before autocast wrap
job_dtype = resolve_diarize_dtype(override=params.dtype) if params.dtype else self._dtype
```

---

### 90.4: Benchmark harness

**Files modified:**

- `dalston/tools/bench_diarize_precision.py` *(new)*

**Deliverables:**

A standalone Python script (importable + `__main__`) that:

1. Loads `pyannote/speaker-diarization-community-1` once.
2. Iterates over `(dtype, audio_file)` pairs.
3. Wraps each diarization call in `torch.cuda.synchronize() → time.perf_counter() → autocast → synchronize() → record`.
4. Writes one RTTM per `(instance, dtype, audio)` and a `results_<instance>.json` summary.
5. Includes a `--bypass-chunking` flag (sets `max_chunk_s` to infinity) so we can isolate the GPU-only speedup from chunk-extraction overhead.

Full usage and the companion drift-comparison script live in [docs/testing/M90-mixed-precision-benchmark.md](../../testing/M90-mixed-precision-benchmark.md).

---

### 90.5: Run the benchmark and record results

**Files modified:**

- `docs/testing/M90-mixed-precision-results.md` *(new)*

**Deliverables:**

Markdown table in the format defined in [docs/testing/M90-mixed-precision-benchmark.md](../../testing/M90-mixed-precision-benchmark.md), with one row per audio file × instance class × dtype, plus aggregate speedup and drift-DER means. Acceptance thresholds:

- **Speedup** ≥ 1.4× on g4dn (fp16), ≥ 1.6× on g6 (bf16).
- **Drift DER** mean < 1.5% (fp16) and < 1.0% (bf16); no single file > 3%.
- **Δ speakers** = 0 on ≥ 9 of 10 files; ±1 acceptable on at most 1 file.

If thresholds fail on a specific audio, that file becomes a regression-test fixture and we either tighten autocast (drop offending op back to fp32) or document the limitation.

---

## Non-Goals

- **Full `.half()` model cast.** Riskier than autocast for marginal extra speedup; only revisit if profiling shows autocast overhead is significant (unlikely for inference workloads of this size).
- **fp8 on L4 / H100.** Pyannote doesn't expose fp8 hooks; would require torch.compile + custom kernels. Out of scope.
- **Tuning `max_chunk_s` upward to exploit halved activation memory.** Belongs in M84 / M89 chunking tuning, not in the precision change. The benchmark records VRAM headroom to inform that follow-up.
- **Training / fine-tuning at mixed precision.** Inference-only.
- **Other engines (nemo, ONNX VAD).** Each has its own dtype story; covered separately if useful. Naive fp16 cast of NeMo diarizers has known issues.
- **Changing the diarization "reference" output of dalston.** fp32 stays the documented and tested reference; fp16/bf16 are throughput-equivalent variants.

---

## Deployment

Backwards-compatible by construction — `DALSTON_DIARIZE_DTYPE` defaults to `fp32`, so the first restart after rollout produces byte-for-byte identical diarization to today. **Mixed precision is opt-in**: operators flip the env var to `auto` (or to an explicit `fp16` / `bf16`) only after the M90.5 per-GPU benchmark results show drift DER and speedup within the acceptance thresholds.

Rollout sequence:

1. Land this PR — production behaviour is unchanged.
2. Run the benchmark on g4dn and g6 per [docs/testing/M90-mixed-precision-benchmark.md](../../testing/M90-mixed-precision-benchmark.md).
3. If results pass, follow up with a small PR that sets `DALSTON_DIARIZE_DTYPE=auto` in the deploy templates (or flips the in-code default).

Kill switch (if needed mid-rollout):

```bash
# Roll a single engine back to fp32 without redeploying
export DALSTON_DIARIZE_DTYPE=fp32
docker compose restart stt-diarize-pyannote-4-0
```

No data migration. No coordinated multi-service rollout required.

---

## Verification

```bash
make dev

# 1. Confirm the default (fp32, opt-in safety) is preserved
docker compose logs stt-diarize-pyannote-4-0 | grep pyannote_4_0_engine_initialized
# Expected anywhere (default — env var unset): ... dtype=fp32 ...

# 2. Opt-in to mixed precision and confirm the engine picks it up
DALSTON_DIARIZE_DTYPE=fp16 make rebuild ENGINE=stt-diarize-pyannote-4-0
docker compose logs stt-diarize-pyannote-4-0 | grep pyannote_4_0_engine_initialized | tail -1
# Expected on T4 GPU: ... dtype=fp16 ...
# Expected on CPU dev box: ... dtype=fp32 ... (CUDA absent → forced to fp32)

DALSTON_DIARIZE_DTYPE=auto make rebuild ENGINE=stt-diarize-pyannote-4-0
docker compose logs stt-diarize-pyannote-4-0 | grep pyannote_4_0_engine_initialized | tail -1
# Expected on T4: ... dtype=fp16 ...
# Expected on A10G/L4: ... dtype=bf16 ...
# Expected on CPU dev box: ... dtype=fp32 ...

# 3. Submit a real job and confirm autocast is engaged
dalston transcribe --audio docs/testing/fixtures/short_2speaker.wav --diarize

# 4. Run the benchmark harness on a g4dn (sets dtype per-job, ignores engine default):
python -m dalston.tools.bench_diarize_precision \
    --instance-tag g4dn \
    --dtypes fp32,fp16 \
    --audio-dir s3://dalston-bench/audio/ \
    --out-dir bench_out/
# Expect: results_g4dn.json with all 10 audios × 2 dtypes; per-file speedup ≥ 1.4×
```

Detailed runbook in [docs/testing/M90-mixed-precision-benchmark.md](../../testing/M90-mixed-precision-benchmark.md).

---

## Checkpoint

- [ ] `dalston/engine_sdk/diarize_dtype.py` exposes `resolve_diarize_dtype` and `autocast_for_diarize`
- [ ] `PyannoteEngine.__init__` logs the resolved dtype and uses it for both single-pass and chunked paths
- [ ] `run_chunked_diarization` accepts and threads a `dtype` keyword argument
- [ ] `DiarizeParams.dtype` is plumbed end-to-end (gateway → engine → autocast)
- [ ] `engine.yaml` declares the `dtype` config field
- [ ] `dalston/tools/bench_diarize_precision.py` runs on a fresh g4dn / g6 instance with one command
- [ ] g4dn results recorded in `docs/testing/M90-mixed-precision-results.md`, meet thresholds
- [ ] g6 results recorded, meet thresholds
- [ ] `DALSTON_DIARIZE_DTYPE=fp32` cleanly disables autocast (rollback path verified)
