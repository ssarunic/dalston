# M89: GPU-Aware VRAM Budgets and Throughput-Driven Preset Tuning

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Co-locate engines on any GPU shape with budgets that are calibrated, not guessed |
| **Duration**       | 4–7 days                                                     |
| **Dependencies**   | M84 (VRAM Budget Management) — uses its profile format and runtime budget reader |
| **Deliverable**    | Per-GPU budget map in `dalston-aws` presets, throughput-sweep mode in `calibrate_vram.py`, `sync_vram_presets` CLI |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator co-locating nemo + pyannote on a g4dn.xlarge (T4) spot box, I want the launch script to pick VRAM budgets that fit my GPU automatically, and I want a profiler I can run later to refine those budgets toward maximum throughput without rewriting the script by hand."*

---

## Outcomes

| Scenario | Current | After M89 |
| -------- | ------- | ---------- |
| `dalston-aws launch gpu --gpu-type g4dn.xlarge --engines nemo,pyannote` | nemo's preset budget is `20000` MB (sized for L4); on T4 (15 GB) this either OOMs or forces operator to set `DALSTON_OVERRIDE__nemo__VRAM_BUDGET_MB` every launch | Script picks the T4 + co-located cell from `vram_budget_by_gpu`, e.g. `nemo=10000, pyannote=3000`; launch fits without any env override |
| New NVIDIA GPU shape (g6e / p5 / g5g) | Operator must edit `GPU_ENGINE_PRESETS` inline before launching | Operator runs the calibrator on one instance, `sync_vram_presets` rewrites the per-GPU cell, future launches inherit it |
| Tuning for throughput, not just "fits in VRAM" | Per-engine intra-task batch sizes (`DALSTON_VAD_BATCH_SIZE` for ONNX, NeMo `batch_size`, pyannote chunk duration) are static guesses in the preset | Calibrator sweeps the engine-specific intra-task knob under synthetic load, records mean RTF and peak VRAM, picks the highest-throughput config that stays under `gpu_vram × safety_margin` |
| Engine running with no profile present for its (model, GPU) pair | Logs `vram_profile_not_found`, falls back to heuristics, may underutilise GPU | Same fallback (M89 doesn't change runtime behaviour) — but the sync tool makes it cheap to produce a profile so the fallback path is rarely hit |
| Reproducing operator's tuning on a new laptop / new operator | Each operator must re-derive override env vars from scratch | Tuned budgets live in the repo as part of `GPU_ENGINE_PRESETS`; `git pull` is the only handoff |

---

## Motivation

Two related problems block clean co-location on smaller GPUs:

**1. `GPU_ENGINE_PRESETS` is GPU-blind.** The `DALSTON_VRAM_BUDGET_MB` value baked into each engine entry assumes a 24 GB GPU. On a T4 (15 GB), nemo's 20 GB budget consumes more than the whole card before pyannote even starts. The runtime override (`DALSTON_OVERRIDE__<eng>__VRAM_BUDGET_MB`, [`_apply_budget_overrides`](../../../infra/scripts/dalston-aws)) works, but it's per-launch operator state — easy to forget and impossible to share via `git`.

**2. The existing calibrator measures VRAM peaks, not throughput.** [`dalston/tools/calibrate_vram.py`](../../../dalston/tools/calibrate_vram.py) fits `peak_vram = S + α·duration` from a single inflight at a time. That's the right input for the OOM-avoidance side of M84's runtime budget calculator — but it can't tell you which `BATCH_MAX_INFLIGHT × VAD_BATCH_SIZE` combination maximises tasks-per-second. Operator-facing tuning today means manually editing the preset, redeploying, watching dashboards, repeat.

The cost story matters too: a `g4dn.xlarge` spot is ~$0.20/hr vs ~$1.00/hr for the `g5.xlarge` on-demand we currently use for co-located nemo+pyannote. Once T4 co-location is reliable, the same workload runs at one-fifth the price.

---

## Architecture

### Preset lookup at launch time

```
┌──────────────────────────────────────────────────────────────────┐
│  dalston-aws launch gpu --gpu-type g4dn.xlarge \                 │
│                         --engines nemo,pyannote                  │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│  _generate_docker_run_block(engine, gpu_type, co_engines)        │
│                                                                  │
│  gpu_name = GPU_FAMILY_TO_NAME[gpu_type.split('.')[0]]           │
│             # "g4dn.xlarge" -> "T4"                              │
│                                                                  │
│  cell = preset["vram_budget_by_gpu"].get(gpu_name)               │
│         # {"solo": 11000, "coloc_with_pyannote-4.0": 9000}       │
│                                                                  │
│  budget = cell["coloc_with_<other_id>"] if co-located            │
│           else cell["solo"]                                      │
│           else preset["extra_env"]["DALSTON_VRAM_BUDGET_MB"]     │
│                                                                  │
│  # DALSTON_OVERRIDE__<eng>__VRAM_BUDGET_MB still wins.           │
└──────────────────────────────────────────────────────────────────┘
```

### Throughput sweep

```
┌──────────────────────────────────────────────────────────────────┐
│  python -m dalston.tools.calibrate_vram \                        │
│      --throughput-sweep \                                        │
│      --engine-url http://localhost:9100 \                        │
│      --stage transcribe --engine-id nemo \                       │
│      --output /data/vram_profiles/transcribe-nemo-T4.json        │
│                                                                  │
│  Per-engine sweep axis (intra-task GPU batching only):           │
│      transcribe-onnx:           DALSTON_VAD_BATCH_SIZE           │
│      transcribe-nemo:           NeMo internal batch_size         │
│      transcribe-faster-whisper: faster-whisper batch_size        │
│      diarize-pyannote:          DALSTON_MAX_DIARIZE_CHUNK_S      │
│                                                                  │
│  Admission-level knobs (BATCH_MAX_INFLIGHT, TOTAL_CAPACITY) are  │
│  NOT swept — runtime pins them and they don't drive parallel    │
│  GPU inference today. See Non-Goals.                             │
│                                                                  │
│  Per cell, sequentially:                                         │
│      1. baseline_mb = nvmlDeviceGetMemoryInfo().used             │
│      2. send synthetic workload (--workload-duration-s default   │
│         30 min, single-task sequential stream)                   │
│      3. peak_mb = max(nvmlDeviceGetMemoryInfo().used) during run │
│      4. delta_mb = peak_mb - baseline_mb  (subject's contribution│
│         only; baseline absorbs the background engine in coloc)   │
│      5. rtf = sum(audio_s) / wall_s                              │
│                                                                  │
│  Choose argmax(rtf) s.t. baseline_mb + delta_mb < gpu_vram ×    │
│  safety_margin (default 0.85).                                   │
│                                                                  │
│  Write profile JSON with new `throughput_optimal` block:         │
│      {                                                           │
│        "throughput_optimal": {                                   │
│          "solo":      {"axis": "vad_batch", "value": 16,         │
│                        "rtf": 42.3,                              │
│                        "delta_mb": 11200, "baseline_mb": 0},     │
│          "coloc_with_pyannote-4.0":                              │
│                       {"axis": "vad_batch", "value": 8,          │
│                        "rtf": 28.1,                              │
│                        "delta_mb": 6100, "baseline_mb": 3300}    │
│        },                                                        │
│        "recommended_budget_mb": {                                │
│          "solo": 12000, "coloc_with_pyannote-4.0": 7000          │
│        }                                                         │
│      }                                                           │
└──────────────────────────────────────────────────────────────────┘
```

`recommended_budget_mb` is derived from `delta_mb` (the subject engine's *incremental* footprint), never from `peak_mb` directly — that's what keeps the sync tool from feeding the background engine's memory back into the subject's preset.

### Sync tool

```
vram_profiles/transcribe-nemo-T4.json     ─┐
vram_profiles/diarize-pyannote-4.0-T4.json─┼─► sync_vram_presets ─► dalston-aws
vram_profiles/transcribe-nemo-A10G.json    │     reads `recommended_budget_mb`
...                                        │     writes `vram_budget_by_gpu`
                                          ─┘     under each preset entry
```

---

## Steps

### 89.1: Per-GPU budget map in `GPU_ENGINE_PRESETS`

**Files modified:**

- `infra/scripts/dalston-aws` — add `vram_budget_by_gpu` field to each preset; extend `_generate_docker_run_block` to look it up.

**Deliverables:**

Each preset entry gets an optional `vram_budget_by_gpu` map. Schema:

```python
"nemo": {
    ...,
    "extra_env": {
        "DALSTON_VRAM_BUDGET_MB": "20000",   # fallback for unknown GPUs
        ...
    },
    "vram_budget_by_gpu": {
        # Hand-seeded conservative values for 89.1; replaced by 89.3 output.
        "T4":   {"solo": 11000, "coloc_with_pyannote-4.0":  9000},
        "A10G": {"solo": 20000, "coloc_with_pyannote-4.0": 18000},
        "L4":   {"solo": 20000, "coloc_with_pyannote-4.0": 20000},
    },
},
"pyannote": {
    ...,
    "vram_budget_by_gpu": {
        "T4":   {"solo": 3500, "coloc_with_nemo": 3000},
        "A10G": {"solo": 4000, "coloc_with_nemo": 4000},
        "L4":   {"solo": 4000, "coloc_with_nemo": 4000},
    },
},
```

`_generate_docker_run_block` signature changes to accept `gpu_type: str, co_engines: list[str]`. Resolution order, top-down:

1. `DALSTON_OVERRIDE__<engine>__VRAM_BUDGET_MB` env var (existing behaviour preserved).
2. `vram_budget_by_gpu[gpu_name][coloc_key]` where `coloc_key = "coloc_with_<other_id>"` if co-located with exactly one other engine, else `"solo"`.
3. `vram_budget_by_gpu[gpu_name]["solo"]` if no co-location match.
4. `extra_env["DALSTON_VRAM_BUDGET_MB"]` (existing fallback).

Multi-engine co-location (3+ engines) is out of scope for 89.1 — `coloc_key` looks up `"solo"` and warns; revisit if it becomes a real shape.

**Tests:**

Unit test (new `tests/unit/test_dalston_aws_presets.py`):

- T4 + `nemo,pyannote` → nemo budget = 9000, pyannote budget = 3000.
- A10G + `nemo` solo → nemo budget = 20000.
- Unknown GPU (g4ad) → falls back to `extra_env` default.
- `DALSTON_OVERRIDE__nemo__VRAM_BUDGET_MB=7000` set → wins over map.

This step unblocks T4 co-location with hand-seeded values; subsequent steps refine them.

---

### 89.2: Throughput-sweep mode in `calibrate_vram.py`

**Files modified:**

- `dalston/tools/calibrate_vram.py` — new `--throughput-sweep` and `--mode` flags, sweep loop, baseline-subtraction in `VRAMMonitor`.
- `infra/scripts/calibrate-coloc-gpu.sh` — new orchestration flow that runs the two engines through the subject/background protocol described below.

**Deliverables:**

New CLI flags:

```
--throughput-sweep           Enable throughput sweep (default: off, peak-only)
--mode solo|coloc:<other_id> Sweep variant; defaults to solo
--sweep-axis auto|<env_var>  Per-engine axis chosen from a built-in map
                             (override only when adding a new engine)
--sweep-grid 4,8,16,32       Values for the chosen axis
--workload-duration-s 1800   Synthetic audio per cell (default 30 min)
--safety-margin 0.85         Max-VRAM threshold for "fits" filter
```

Per-engine default axis (`--sweep-axis auto`):

| Engine | Axis env var | Why this is real intra-task GPU concurrency |
| --- | --- | --- |
| transcribe-onnx | `DALSTON_VAD_BATCH_SIZE` | Batches VAD-segmented chunks of one file into a single ONNX call |
| transcribe-nemo | `DALSTON_NEMO_BATCH_SIZE` *(new)* | NeMo Lhotse manifest batch; logs already report `"batch_size": 4` per call |
| transcribe-faster-whisper | `DALSTON_FW_BATCH_SIZE` *(new)* | faster-whisper's `batch_size` kwarg |
| diarize-pyannote | `DALSTON_MAX_DIARIZE_CHUNK_S` | Longer chunks ⇒ larger reconstruction matrix ⇒ throughput/VRAM tradeoff |

Per cell, the calibrator:

1. **Re-applies the swept value via container restart.** No admin HTTP reload endpoint exists today — set the env var, `docker restart <container>`, wait for `engine_loop_starting` to reappear in logs. The two new `DALSTON_*_BATCH_SIZE` env vars are wired up to the existing inference call sites in step 89.2 alongside the calibrator changes.
2. **Snapshots baseline VRAM** via `pynvml.nvmlDeviceGetMemoryInfo()` *after* the subject restarts and reaches steady state, but *before* sending the workload. In `solo` mode this captures model weights of the subject only; in `coloc` mode it captures model weights of subject + background.
3. **Posts a sequential single-task workload** totalling `--workload-duration-s` synthetic audio. Sequential because the runtime processes one task at a time (`BATCH_MAX_INFLIGHT=1` is pinned in every preset); parallel posting wouldn't reflect actual on-GPU concurrency, just queue depth.
4. **Polls `nvmlDeviceGetMemoryInfo()` at 100 Hz** during the workload, records `peak_used_mb`.
5. **Computes** `delta_mb = peak_used_mb - baseline_mb`, `rtf = audio_s / wall_s`.

Selects the best cell as `argmax(rtf) s.t. baseline_mb + delta_mb < gpu_vram_mb × safety_margin`. `recommended_budget_mb` is reported as `round_up(delta_mb, 1000) + headroom` — never `peak_used_mb`, so the background engine's resident weights never leak into the subject's budget.

**Coloc protocol (used by `--mode coloc:<other_id>` and orchestrated by `calibrate-coloc-gpu.sh`):**

1. Bring up both engines, let both reach `engine_loop_starting`.
2. **Pause** task feeding to the background engine (no work in flight — its memory is at model-weights-only steady state).
3. Run the subject's sweep as above. The background engine's resident weights are captured by `baseline_mb` once; `delta_mb` isolates the subject's working set.
4. Optionally swap roles: pause the subject, sweep the background engine.
5. Resume normal operation.

This protocol intentionally does **not** drive both engines under load simultaneously. That measurement is interesting (worst-case contention) but isn't what the runtime budget calculator needs — and conflating the two engines' peaks back into either's `recommended_budget_mb` is the double-counting failure mode this revision avoids.

**Tests:**

- `--dry-run --throughput-sweep` produces a profile with `throughput_optimal` filled in by deterministic synthetic data (no GPU needed).
- Unit test for the `argmax(rtf) s.t. baseline + delta < threshold` picker with several mocked sweep results.
- Unit test asserting `recommended_budget_mb` is derived from `delta_mb` only, never from `peak_mb`, even when the mock baseline is non-zero.

**Out of scope for 89.2:**

- True concurrent contention measurement (both engines under load at once).
- Sweeping admission-level knobs (`DALSTON_BATCH_MAX_INFLIGHT`, `DALSTON_TOTAL_CAPACITY`) — pinned in presets, not a throughput axis today.
- Multi-axis sweeps. One axis per run; combinations require multiple runs.

---

### 89.3: `sync_vram_presets` CLI

**Files modified:**

- `dalston/tools/sync_vram_presets.py` *(new)*
- `infra/scripts/dalston-aws` — no functional change; this step rewrites the `vram_budget_by_gpu` literal in-place.

**Deliverables:**

```
python -m dalston.tools.sync_vram_presets \
    [--profiles-dir dalston/tools/vram_profiles] \
    [--target infra/scripts/dalston-aws] \
    [--dry-run]
```

Behaviour:

1. Walks `--profiles-dir`, **opens every `*.json` and keys on JSON contents only** — `engine_id`, `model_id`, normalised `gpu` field. Filename is informational metadata, not a parse target (existing profiles like `diarize-pyannote-4.0-T4.json` use a dotted engine_id and slash-bearing `model_id` that can't round-trip through filenames cleanly).
2. Normalises `gpu` to one of the keys in `GPU_FAMILY_TO_NAME` values (`T4`, `A10G`, `L4`, …) via a case-insensitive lookup; rejects profiles for unknown GPUs with a clear error.
3. Maps `engine_id` to the matching `GPU_ENGINE_PRESETS` entry by exact-match on the preset's `engine_id` field (already present in every preset). Multiple profiles per engine across GPUs is the expected case.
4. Builds `vram_budget_by_gpu[<gpu>] = {"solo": p.recommended_budget_mb.solo, "coloc_with_<other_engine_id>": ...}` from each profile, where `<other_engine_id>` matches the preset's `engine_id`, not the filename.
5. Rewrites the literal `vram_budget_by_gpu` block in `dalston-aws` using `libcst`. Idempotent — re-running on unchanged profiles produces no diff.
6. `--dry-run` prints the unified diff without writing.

Refusal cases (exit non-zero, no write):

- Profile is missing `recommended_budget_mb` or `engine_id` (older format from 89.2-pre).
- Profile's `gpu` doesn't normalise to a known `GPU_FAMILY_TO_NAME` value.
- Two profiles with the same `(engine_id, gpu, mode)` triple disagree on `recommended_budget_mb` — operator picks one by deleting the other before re-running.
- Target preset block in `dalston-aws` can't be located unambiguously (libcst match fails).

**Optional follow-up (not in 89.3):** if hand-editing the embedded Python dict proves brittle, lift `GPU_ENGINE_PRESETS` out to `infra/templates/gpu-engine-presets.yaml` and have both the launch script and the sync tool read it. Tracked as M89-follow-up rather than blocking this milestone.

**Tests:**

- Round-trip: hand-craft 4 profiles, run sync, verify the resulting `dalston-aws` matches a golden fixture.
- Idempotency: run sync twice, second run produces no diff.
- Conflict detection: two profiles for the same cell with different budgets → exits non-zero, prints both source files.

---

## Non-Goals

- **Runtime adaptive budget tuning.** The engine still reads its budget once at startup. Adaptive re-tuning per-task lives in M84.
- **Replacing `_apply_budget_overrides`.** The env-var override is the documented escape hatch for ad-hoc tuning; M89 changes the *defaults*, not the override mechanism.
- **YAML-ification of presets.** Tempting but not required — keep `GPU_ENGINE_PRESETS` in `dalston-aws` for now. Can be split out later if 89.3's AST rewriting causes friction.
- **GPU shapes beyond T4 / A10G / L4.** The map is open-ended; new shapes are added by running the calibrator and the sync tool. M89 doesn't pre-seed every shape AWS sells.
- **AMD / ROCm GPUs (g4ad, etc.).** The whole stack assumes NVIDIA + CUDA + NVML. Adding AMD support is a separate, much larger effort and is not in scope.
- **True concurrent-load contention sweeps.** Step 89.2 measures the subject engine with the background engine idle (model weights resident, no in-flight task). Running both under load simultaneously and attributing the joint peak is interesting but explicitly out of scope — see the discussion in 89.2.
- **Admission-level concurrency tuning.** `DALSTON_BATCH_MAX_INFLIGHT`, `DALSTON_TOTAL_CAPACITY`, `DALSTON_RT_RESERVATION` are pinned in presets and not part of the sweep. Revisiting them belongs in a follow-up to M37 / M84, not here.
- **vLLM-asr co-location.** `vllm-asr` reserves 90% of VRAM by design (`DALSTON_VLLM_GPU_MEMORY_UTILIZATION=0.9`) — co-locating it with another engine isn't viable without separate work to lower the reservation.

---

## Deployment

No coordinated rollout. Steps 89.1, 89.2, 89.3 ship independently:

- 89.1 changes only launch-time `docker run` env vars; no engine, gateway, or orchestrator changes.
- 89.2 is a tool, not a runtime component.
- 89.3 is a tool, not a runtime component.

Existing instances keep running with whatever budgets they booted with; new launches pick up the per-GPU map.

---

## Verification

```bash
# 89.1 — T4 co-location uses the right budget
./infra/scripts/dalston-aws launch gpu \
    --gpu-type g4dn.xlarge --engines nemo,pyannote --spot

# After boot, on the worker:
ssh -i ~/.dalston/dalston-key.pem ubuntu@dalston-gpu-nemo-pyannote \
    "sudo docker inspect stt-transcribe-nemo --format '{{range .Config.Env}}{{println .}}{{end}}'" \
    | grep DALSTON_VRAM_BUDGET_MB
# Expect: DALSTON_VRAM_BUDGET_MB=9000   (or whatever the calibrated T4+coloc value is)

# 89.2 — throughput sweep produces a profile with throughput_optimal
python -m dalston.tools.calibrate_vram \
    --engine-url http://localhost:9100 \
    --stage transcribe --engine-id nemo \
    --throughput-sweep --workload-duration-s 600 \
    --output /tmp/transcribe-nemo-T4.json
jq '.throughput_optimal.solo' /tmp/transcribe-nemo-T4.json
# Expect: {"inflight": ..., "vad_batch": ..., "rtf": ..., "peak_vram_mb": ...}

# 89.3 — sync round-trip is idempotent
python -m dalston.tools.sync_vram_presets --dry-run | grep -c '^[+-]' && echo "changes pending"
python -m dalston.tools.sync_vram_presets
python -m dalston.tools.sync_vram_presets --dry-run | grep -c '^[+-]'
# Expect: 0 (no further diff)
```

---

## Checkpoint

- [ ] `vram_budget_by_gpu` field added to each entry in `GPU_ENGINE_PRESETS` with hand-seeded T4 / A10G / L4 values
- [ ] `_generate_docker_run_block` looks up the right cell from `gpu_type` + co-engines and overrides `DALSTON_VRAM_BUDGET_MB`
- [ ] `DALSTON_OVERRIDE__<engine>__VRAM_BUDGET_MB` still takes precedence (regression test)
- [ ] `calibrate_vram.py --throughput-sweep` writes `throughput_optimal` + `recommended_budget_mb` into the profile JSON
- [ ] `calibrate-coloc-gpu.sh --concurrent` drives realistic contention during the sweep
- [ ] `dalston.tools.sync_vram_presets` rewrites `vram_budget_by_gpu` from profiles, with `--dry-run` and conflict detection
- [ ] T4 co-location of `nemo,pyannote` succeeds end-to-end at default budgets without any operator env-var override
- [ ] One full pass executed: launch on T4, sweep, sync, commit, re-launch — re-launch inherits the tuned values from `git`
- [ ] Coloc sweep protocol verified: subject's `recommended_budget_mb` derived from `delta_mb` only; background engine's resident weights captured in `baseline_mb` and not double-counted
