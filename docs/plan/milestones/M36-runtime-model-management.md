# M36: Runtime Model Management

|                  |                                                                    |
| ---------------- | ------------------------------------------------------------------ |
| **Goal**         | Engines can load any compatible model variant at runtime           |
| **Duration**     | 4 phases (implementation in progress)                              |
| **Dependencies** | M31 (Capability-Driven Routing), M32 (Engine Variant Structure)    |
| **Deliverable**  | Model swapping in engines, two-catalog architecture, simplified Docker setup |
| **Status**       | Phase 1 Complete                                                   |

## Overview

Transform Dalston from a system where **each model variant is a separate Docker image** to one where **a small number of engine runtimes can load any compatible model on demand**.

This is inspired by Ollama: a single `engine-nemo` runtime image can serve any Parakeet model variant. Model weights live on a shared Docker volume, downloaded on first use, cached for reuse.

### Why This Matters

- **Fewer images to build and maintain.** 2 consolidated runtime images instead of 8+ variant-specific images for transcription alone.
- **Faster iteration.** Adding a new variant means adding a YAML metadata file, not a new Docker image.
- **Smaller disk footprint.** One runtime image + shared model weights volume, instead of N images each containing the runtime + one model.
- **Foundation for `dalston pull`.** Future CLI command to pre-download models.

---

## Phases

### Phase 1: Engine-Side Model Swapping (COMPLETE)

**Goal:** Engines can load any compatible model variant at runtime, selected by `config["runtime_model_id"]` in the task payload.

**Changes:**

1. **`dalston/engine_sdk/base.py`**
   - Added thread-safe `_set_runtime_state()` and `get_runtime_state()` methods
   - Engines report loaded model and status for heartbeat reporting

2. **`engines/stt-transcribe/parakeet/engine.py`**
   - Added `SUPPORTED_MODELS` set with all valid NeMo model IDs
   - Implemented `_ensure_model_loaded()` with GPU memory cleanup on model swap
   - Reads `runtime_model_id` from task config, falls back to `DALSTON_DEFAULT_MODEL_ID`
   - Engine ID now comes from `DALSTON_ENGINE_ID` env var (runtime ID, not variant ID)
   - Updated `get_capabilities()` to return runtime ID and all supported models

3. **`engines/stt-transcribe/faster-whisper/engine.py`**
   - Added `SUPPORTED_MODELS` set with all valid Whisper model IDs
   - Implemented `_ensure_model_loaded()` for model swapping
   - Removed GPU-only restriction for `large-v3-turbo` (CTranslate2 supports CPU with int8)
   - Uses `download_root` parameter for runtime-specific model cache directory
   - Engine ID from `DALSTON_ENGINE_ID` env var

**Verification:**

```bash
# Tests pass
pytest tests/unit/test_engine*.py tests/unit/test_dag.py tests/integration/test_capability_driven_dag.py -v
# 135 tests passed

# Syntax check
python -m py_compile dalston/engine_sdk/base.py engines/stt-transcribe/parakeet/engine.py engines/stt-transcribe/faster-whisper/engine.py

# Linting
ruff check dalston/engine_sdk/base.py engines/stt-transcribe/parakeet/engine.py engines/stt-transcribe/faster-whisper/engine.py
# All checks passed
```

---

### Phase 2: Orchestrator Runtime Routing (PLANNED)

**Goal:** Orchestrator routes jobs by runtime + model variant. Legacy sync DAG path deleted.

**Key Changes:**

- Add `runtime` field to `EngineCapabilities` (required for all engines)
- Heartbeat includes `loaded_model` from engine's runtime state
- `MODEL_REGISTRY` in dag.py maps public model IDs to runtime + runtime_model_id
- `build_task_dag()` sets `config["runtime_model_id"]` from registry
- Delete legacy `build_task_dag()` and `NATIVE_WORD_TIMESTAMP_ENGINES`
- Update 9 utility engine.yaml files with `runtime:` field

---

### Phase 4: Model Catalog and CLI (PLANNED - runs before Phase 3)

**Goal:** Structured model metadata. New `/v1/models` and `/v1/engines` APIs.

**Key Changes:**

- Create `models/` directory with YAML metadata for each model variant
- Two-catalog architecture: runtime catalog + model catalog
- `dalston models` CLI shows model catalog (installed status)
- `dalston engines` CLI shows running engine status
- `dalston models pull <model>` pre-downloads model weights

---

### Phase 3: Simplify Docker Images (PLANNED - runs after Phase 4)

**Goal:** Collapse variant-specific Docker images into runtime-based ones.

**Key Changes:**

- Create runtime-level `engine.yaml` files (one per runtime)
- Delete variant YAML files (`engines/*/variants/*.yaml`)
- Dockerfiles no longer download models at build time
- `docker-compose.yml` collapses to 2 transcription services
- Model cache uses runtime-specific subdirectories (`/models/nemo/`, `/models/faster-whisper/`)

---

## Target Flow (After All Phases)

```
API request (model="parakeet-tdt-1.1b")
  → Gateway creates job
  → Orchestrator builds DAG:
      - Looks up "parakeet-tdt-1.1b" in model catalog
        → runtime="nemo", runtime_model_id="nvidia/parakeet-tdt-1.1b"
      - Sets task.engine_id = "nemo"
      - Sets task.config["runtime_model_id"] = "nvidia/parakeet-tdt-1.1b"
  → Scheduler finds container registered as runtime="nemo"
  → Container receives task, reads config["runtime_model_id"]
    → If model already loaded: transcribe immediately
    → If different model: unload, load requested (~5-15s swap)
    → If model not on disk: download, then load
  → Transcribe and return result
```

---

## Key Design Decisions

1. **Two IDs per model**: Public Dalston ID (e.g., `faster-whisper-large-v3-turbo`) vs runtime-native ID (e.g., `large-v3-turbo` for WhisperModel).

2. **GPU memory cleanup on swap**: `del model → torch.cuda.synchronize() → empty_cache() → gc.collect()`

3. **Runtime-specific cache directories**: `/models/nemo/` and `/models/faster-whisper/` prevent cross-contamination.

4. **Default model is multilingual + CPU-capable**: `faster-whisper-large-v3-turbo` for "just works" experience.

---

## Files Changed (Phase 1)

| File | Change |
|------|--------|
| `dalston/engine_sdk/base.py` | Added `_set_runtime_state()` and `get_runtime_state()` |
| `engines/stt-transcribe/parakeet/engine.py` | Model swapping, runtime ID, GPU memory cleanup |
| `engines/stt-transcribe/faster-whisper/engine.py` | Model swapping, CPU support for large-v3-turbo |
