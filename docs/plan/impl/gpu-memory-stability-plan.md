# GPU Memory Stability — Implementation Plan

Reference: [`docs/guides/gpu-memory-stability.md`](../guides/gpu-memory-stability.md)

## Summary

Three-phase implementation to make Dalston's GPU engines resilient to OOM conditions. Each phase is independently valuable and builds on the previous.

---

## Phase 1: Immediate Hardening (High Value, Low Effort)

**Goal**: eliminate the most common OOM triggers with configuration and cleanup fixes that require no new infrastructure.

### 1.1 Set `PYTORCH_CUDA_ALLOC_CONF` in all GPU engine containers

**What**: add the environment variable to every GPU engine service in `docker-compose.yml` and `infra/docker/docker-compose.aws.yml`.

```yaml
environment:
  PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.8"
```

**Where** (every `stt-*` service that uses a GPU):
- `docker-compose.yml` — all GPU engine service definitions
- `infra/docker/docker-compose.aws.yml` — AWS equivalents

**Why `expandable_segments:True` is the priority**: it directly addresses the variable-length allocation pattern that ASR inference creates, where batch sizes and audio durations change between requests.

### 1.2 Fix `gc.collect()` / `torch.cuda.empty_cache()` ordering

**What**: across the codebase, `torch.cuda.empty_cache()` is called *before* `gc.collect()`. This is backwards — Python circular references must be broken by the garbage collector before CUDA can reclaim the underlying device memory.

**Files to fix** (confirmed by codebase search):
- `dalston/engine_sdk/model_manager.py` (~line 266, ~line 384)
- `dalston/engine_sdk/managers/nemo.py` (~line 232)
- `dalston/engine_sdk/managers/onnx.py` (~line 212)
- `engines/stt-unified/vllm-asr/runner.py` (~lines 237-238)
- `engines/stt-unified/vllm-asr/batch_engine.py` (~lines 168-169, 390-391)
- `engines/stt-unified/vllm-asr/rt_engine.py` (~lines 95-96, 220-221)

**Pattern** — change every occurrence from:

```python
torch.cuda.empty_cache()
gc.collect()
```

to:

```python
gc.collect()
torch.cuda.empty_cache()
```

### 1.3 Add explicit cleanup at pipeline stage boundaries

**What**: ensure `gc.collect()` → `torch.cuda.empty_cache()` runs between every stage handover, particularly transcription → alignment and alignment → diarisation.

**Where**: `dalston/engine_sdk/model_manager.py` — in the model swap path (when one model is unloaded and another is about to be loaded). The `release()` and `evict()` methods should perform explicit cleanup.

### 1.4 Add audio chunking with configurable `max_duration`

**What**: add a `max_audio_duration_s` configuration parameter to the preparation stage. Audio longer than this threshold gets split into chunks with overlap before being dispatched to transcription engines.

**Where**:
- `engines/stt-prepare/audio-prepare/engine.py` — add chunking logic after format standardisation
- `engines/stt-prepare/audio-prepare/engine.yaml` — add `max_audio_duration_s` config (default: 300s / 5 minutes)

**Chunking strategy**:
- Split at silence boundaries (using ffmpeg silencedetect) when possible
- Fall back to fixed-duration splits with configurable overlap (default: 2s) when no silence found
- Each chunk becomes a separate task in the DAG; a downstream merge step reassembles

**Note**: this is a preparation-stage concern, not a per-engine concern. Individual engines already handle their own internal batching; this bounds the *input* to those engines.

### 1.5 Add warm-up inference before reporting engine healthy

**What**: extend the existing `DALSTON_MODEL_PRELOAD` mechanism to run an actual inference pass (not just load/release) before the engine accepts work.

**Where**:
- `dalston/engine_sdk/model_manager.py` — after `acquire()` in the preload path, run a warm-up inference
- `dalston/engine_sdk/runner.py` — update `/health` endpoint to check `warm_up_completed` flag, not just return static "healthy"

**Warm-up requirements**:
- Use **white noise** (not zeros) as synthetic input — some encoders follow different memory/compute paths with silence vs. textured audio
- Warm up at **max expected batch size** (configurable via `DALSTON_WARMUP_BATCH_SIZE`, default: 1)
- Record **peak memory** after warm-up as engine metadata
- Only report healthy after warm-up completes

**Implementation detail**: add a `warm_up()` method to the `Engine` base class that subclasses can override. Default implementation generates random audio at the configured duration and batch size, runs `transcribe()` / `process()`, and records peak memory via `torch.cuda.max_memory_allocated()`.

### Phase 1 verification

- [ ] All GPU engine containers have `PYTORCH_CUDA_ALLOC_CONF` set
- [ ] `gc.collect()` precedes `torch.cuda.empty_cache()` in every occurrence
- [ ] Model manager performs explicit cleanup on model swap
- [ ] Audio longer than `max_audio_duration_s` is chunked in the prepare stage
- [ ] Engines with `DALSTON_MODEL_PRELOAD` run warm-up inference before reporting healthy
- [ ] `make dev-gpu` starts cleanly; existing tests pass (`make test`)

---

## Phase 2: Calibration Infrastructure (Medium Effort)

**Goal**: replace guesswork with measured data. Build tooling that profiles model-GPU pairs and stores results for runtime use.

### 2.1 Build `dalston calibrate <model> <gpu>` CLI command

**What**: a CLI command that runs the binary-search batch size profiler from the guide and stores results.

**Where**:
- `cli/dalston_cli/commands/calibrate.py` — new command module
- `cli/dalston_cli/main.py` — register the command

**Behaviour**:
1. Load the specified model on the current GPU
2. Binary-search for max stable batch size at 75% memory margin
3. Run warm-up, then re-profile at 85% margin to get the post-warm-up batch size
4. Output results as YAML and optionally store in model registry

**Output format**:

```yaml
model: parakeet-tdt-0.6b
gpu: A10G (24GB)
compute_type: float16
calibration:
  pre_warmup:
    max_batch_size: 12
    safety_margin: 0.75
    peak_memory_gb: 17.1
  post_warmup:
    max_batch_size: 16
    safety_margin: 0.85
    peak_memory_gb: 19.4
  max_audio_duration_s: 30
  calibrated_at: "2026-03-14T10:30:00Z"
```

### 2.2 Store calibration data in model registry metadata

**What**: extend the model registry schema to include GPU calibration data per model.

**Where**:
- `dalston/db/models/` — add calibration fields to model registry ORM (or a related `model_calibration` table)
- `dalston/gateway/services/model_service.py` — expose calibration data via API
- `dalston/gateway/api/v1/models.py` — add calibration endpoints

**Schema** (new `model_calibrations` table):

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `model_id` | FK → models | Which model |
| `gpu_type` | VARCHAR | e.g. "A10G", "T4", "A100" |
| `gpu_memory_gb` | FLOAT | Total GPU memory |
| `compute_type` | VARCHAR | float16, int8, etc. |
| `max_batch_size` | INT | Calibrated safe batch size |
| `safety_margin` | FLOAT | Margin used during calibration |
| `peak_memory_gb` | FLOAT | Observed peak memory |
| `max_audio_duration_s` | FLOAT | Max chunk duration used |
| `calibrated_at` | TIMESTAMP | When calibration ran |

### 2.3 Surface calibration data in model library

**What**: show VRAM requirements and calibrated batch sizes alongside WER benchmarks on the model pages at dalston.ai/models and in the console.

**Where**:
- `web/src/` — model detail component
- Console model management pages (M42)

**Display**: "Parakeet TDT 0.6B: batch_size=16 on A10G (24 GB), 0.85 safety margin post-warm-up"

### 2.4 Engines auto-load calibration on startup

**What**: when an engine starts and loads a model, it checks for existing calibration data for the current model-GPU pair. If found, it uses the calibrated `max_batch_size` instead of a hardcoded default.

**Where**:
- `dalston/engine_sdk/model_manager.py` — query calibration data after model load
- `dalston/engine_sdk/runner.py` — pass calibrated parameters to engine

**Fallback**: if no calibration exists for the current GPU, use conservative defaults and log a warning suggesting `dalston calibrate`.

### Phase 2 verification

- [ ] `dalston calibrate parakeet-tdt-0.6b` completes and outputs YAML
- [ ] Calibration data stored in database and retrievable via API
- [ ] Console model pages show calibration data when available
- [ ] Engines use calibrated batch sizes when calibration data exists
- [ ] Missing calibration logs a warning, not an error

---

## Phase 3: Runtime Adaptation (Higher Effort)

**Goal**: close the loop — engines observe real-world memory behaviour, adapt batch sizes at runtime, and recover gracefully from OOM.

### 3.1 Adaptive batch sizing with `dcgm_fb_free` monitoring

**What**: implement the `AdaptiveBatcher` class from the guide. Engines monitor actual GPU memory usage per request and adjust batch sizes up or down.

**Where**:
- `dalston/engine_sdk/adaptive_batcher.py` — new module with `AdaptiveBatcher` class
- `dalston/engine_sdk/runner.py` — integrate with the processing loop

**Behaviour**:
- Start at calibrated batch size (from Phase 2) or conservative default
- After each inference, record `peak_memory_fraction = peak / total`
- Maintain sliding window of last 100 observations
- If average < 0.70: increment batch size by 1
- If average > 0.90: decrement batch size by 2 (aggressive backoff — OOM is expensive)
- Expose current batch size as a Prometheus gauge (`dalston_engine_batch_size`)

**Monitoring integration**:
- Add `dcgm_fb_free` to existing Grafana dashboards (M20)
- Alert at 85% usage (batch size reduction trigger)
- Alert at 95% usage (reject new requests)

### 3.2 OOM recovery with automatic retry and batch reduction

**What**: catch `torch.cuda.OutOfMemoryError` in the engine processing loop and implement graduated recovery.

**Where**:
- `dalston/engine_sdk/runner.py` — wrap `engine.process()` in OOM-aware try/except
- `dalston/engine_sdk/adaptive_batcher.py` — add `on_oom()` method

**Recovery sequence**:
1. Catch `torch.cuda.OutOfMemoryError`
2. `gc.collect()` → `torch.cuda.empty_cache()`
3. Halve batch size, retry the failed request
4. If still OOM, re-chunk the audio into smaller segments and retry
5. If still OOM after 3 attempts, fail the task with a clear error

**Logging**: every OOM event logs audio duration, batch size, peak memory, GPU type, and model ID. This data feeds back into calibration refinement.

### 3.3 Memory-aware request admission

**What**: before accepting a new task from the queue, estimate whether the engine has enough headroom to process it.

**Where**:
- `dalston/engine_sdk/runner.py` — add admission check before `engine.process()`

**Estimation formula**:

```python
estimated_peak = model_base_memory + (batch_size * per_sample_cost * duration_factor)
```

Where `per_sample_cost` and `duration_factor` are derived from calibration data (Phase 2) or from the adaptive batcher's observations.

If `estimated_peak > available_memory * safety_factor`: reduce batch size for this request, or delay consumption from the queue (nack/requeue with backoff).

### 3.4 Feed production memory profiles back to calibration

**What**: periodically aggregate runtime memory observations and update calibration data in the registry, closing the feedback loop.

**Where**:
- `dalston/engine_sdk/adaptive_batcher.py` — periodic calibration update (e.g. every 1000 requests)
- `dalston/gateway/services/model_service.py` — accept calibration updates from engines

**What gets updated**:
- Observed max safe batch size under production load
- P95 peak memory across real requests
- OOM frequency per model-GPU pair

### Phase 3 verification

- [ ] `AdaptiveBatcher` adjusts batch size based on observed memory pressure
- [ ] OOM caught and recovered from without engine restart
- [ ] Batch size reduction visible in Prometheus/Grafana
- [ ] `dcgm_fb_free` alerts fire at configured thresholds
- [ ] Admission control rejects or downsizes requests when memory is tight
- [ ] Production observations update calibration data in the registry
- [ ] End-to-end: submit a very long audio file → engine chunks, processes, recovers from any OOM, delivers result

---

## Dependencies

```
Phase 1 (standalone — no prerequisites beyond existing codebase)
    │
    ├── 1.1 PYTORCH_CUDA_ALLOC_CONF          — docker-compose changes only
    ├── 1.2 Fix gc/cache ordering             — engine SDK changes
    ├── 1.3 Stage boundary cleanup            — engine SDK changes
    ├── 1.4 Audio chunking                    — prepare engine + orchestrator DAG
    └── 1.5 Warm-up inference                 — engine SDK + runner changes
         │
Phase 2 (requires Phase 1.5 warm-up infrastructure)
    │
    ├── 2.1 dalston calibrate CLI             — needs warm-up + profiling harness
    ├── 2.2 Calibration DB schema             — DB migration
    ├── 2.3 Console/model library display     — needs 2.2
    └── 2.4 Auto-load calibration             — needs 2.2
         │
Phase 3 (requires Phase 2 calibration data)
    │
    ├── 3.1 Adaptive batch sizing             — needs 2.4 for initial values
    ├── 3.2 OOM recovery                      — standalone, but benefits from 3.1
    ├── 3.3 Admission control                 — needs 3.1 observations
    └── 3.4 Feedback loop                     — needs 2.2 schema + 3.1 observations
```

Within each phase, tasks are largely independent and can be parallelised. Phase 1 tasks 1.1, 1.2, and 1.3 can ship together as a single PR. Task 1.4 (chunking) and 1.5 (warm-up) are larger and warrant separate PRs.
