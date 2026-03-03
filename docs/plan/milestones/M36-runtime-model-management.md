# M36: Runtime Model Management

|                  |                                                                    |
| ---------------- | ------------------------------------------------------------------ |
| **Goal**         | Engines can load any compatible model variant at runtime           |
| **Duration**     | 4 phases                                                           |
| **Dependencies** | M31 (Capability-Driven Routing), M32 (Engine Variant Structure)    |
| **Deliverable**  | Model swapping in engines, two-catalog architecture, simplified Docker setup |
| **Status**       | **COMPLETE**                                                       |

## Overview

Transformed Dalston from a system where **each model variant is a separate Docker image** to one where **a small number of engine runtimes can load any compatible model on demand**.

This is inspired by Ollama: a single `engine-nemo` runtime image can serve any Parakeet model variant. Model weights live in S3, downloaded on first use, cached locally for reuse.

### Benefits Achieved

- **Fewer images to build and maintain**: 5 consolidated runtime images instead of 20+ variant-specific images
- **Faster iteration**: Adding a new model means adding a YAML file, not a new Docker image
- **Smaller disk footprint**: One runtime image + shared S3 model storage
- **Dynamic model support**: `dalston model pull` and HuggingFace auto-routing

---

## Implementation Summary

### Phase 1: Engine-Side Model Swapping (COMPLETE)

Engines can load any compatible model variant at runtime via `config["runtime_model_id"]`.

**Key Components**:

1. **`ModelManager` base class** (`dalston/engine_sdk/model_manager.py`)
   - Thread-safe reference counting (`LoadedModel.ref_count`)
   - LRU eviction when at `max_loaded` capacity
   - TTL-based eviction (default: 3600s idle timeout)
   - Background eviction thread checking every 60s
   - Preloading via `DALSTON_MODEL_PRELOAD` environment variable

2. **Concrete Managers**:
   - `FasterWhisperModelManager` - CTranslate2/faster-whisper models
   - `HFTransformersModelManager` - HuggingFace Transformers ASR pipelines

3. **GPU Memory Cleanup**:

   ```python
   del model
   torch.cuda.synchronize()
   torch.cuda.empty_cache()
   gc.collect()
   ```

---

### Phase 2: Orchestrator Runtime Routing (COMPLETE)

Orchestrator routes jobs by runtime + model variant.

**Key Changes**:

1. **Engine capabilities include `runtime` field**
   - Heartbeat reports `loaded_model` for UI display
   - Registry tracks which model each engine instance has loaded

2. **Model catalog** (`models/*.yaml`)
   - 18 model definitions with namespaced HF-style IDs
   - Maps public ID → runtime + runtime_model_id

3. **Engine selection** (`dalston/orchestrator/engine_selector.py`)
   - Resolves model ID to runtime via catalog or DB
   - Validates runtime is running and healthy
   - Auto-selects best downloaded model when `model=auto`

---

### Phase 3: Simplify Docker Images (COMPLETE)

Collapsed variant-specific Docker images into runtime-based ones.

**Before**: 20+ images (one per model variant)

```
stt-batch-transcribe-faster-whisper-base
stt-batch-transcribe-faster-whisper-small
stt-batch-transcribe-faster-whisper-medium
stt-batch-transcribe-faster-whisper-large-v3
stt-batch-transcribe-faster-whisper-large-v3-turbo
stt-batch-transcribe-parakeet-ctc-0.6b
stt-batch-transcribe-parakeet-ctc-1.1b
stt-batch-transcribe-parakeet-tdt-0.6b-v3
stt-batch-transcribe-parakeet-tdt-1.1b
...
```

**After**: 5 runtime images

```
stt-batch-transcribe-faster-whisper    # All Whisper variants
stt-batch-transcribe-nemo              # All Parakeet NeMo variants
stt-batch-transcribe-parakeet-onnx     # All Parakeet ONNX variants
stt-batch-transcribe-hf-asr            # Any HuggingFace ASR model
stt-batch-transcribe-vllm-asr          # Voxtral, Qwen2-Audio
```

---

### Phase 4: Model Catalog and CLI (COMPLETE)

Structured model metadata with API and CLI management.

**Model YAML Schema** (`models/*.yaml`):

```yaml
schema_version: "1.1"
id: nvidia/parakeet-tdt-1.1b
runtime: nemo
runtime_model_id: "nvidia/parakeet-tdt-1.1b"
name: NVIDIA Parakeet TDT 1.1B
source: nvidia/parakeet-tdt-1.1b
size_gb: 4.2
stage: transcribe
languages: [en]
capabilities:
  word_timestamps: true
  punctuation: false
  capitalization: false
hardware:
  min_vram_gb: 6
  supports_cpu: true
  min_ram_gb: 12
performance:
  rtf_gpu: 0.0006
```

**API Endpoints**:

- `GET /v1/models` - List catalog
- `GET /v1/models/{model_id}` - Get catalog entry
- `GET /v1/engines` - List engines with `loaded_model` and `available_models`

---

## Final Architecture

```
API request (model="nvidia/parakeet-tdt-1.1b")
  → Gateway creates job
  → Orchestrator builds DAG:
      - Looks up model in catalog/registry
        → runtime="nemo", runtime_model_id="nvidia/parakeet-tdt-1.1b"
      - Sets task.engine_id = "nemo"
      - Sets task.config["runtime_model_id"] = "nvidia/parakeet-tdt-1.1b"
  → Scheduler finds container registered as runtime="nemo"
  → Container receives task, reads config["runtime_model_id"]
    → If model already loaded: transcribe immediately
    → If different model: unload, load requested (~5-15s swap)
    → If model not on disk: download from S3, then load
  → Transcribe and return result
```

---

## Key Design Decisions

1. **Namespaced model IDs**: Use HuggingFace-style `org/model` format for unambiguous identification

2. **S3 as canonical storage**: Models stored in S3, engines cache locally. The `.complete` marker ensures atomic availability.

3. **TTL + LRU eviction**: Models evicted after 1 hour idle OR when memory pressure requires it

4. **Runtime-specific cache directories**: `/models/s3-cache/{runtime}/` prevents cross-contamination

5. **Default model is multilingual + CPU-capable**: `Systran/faster-whisper-large-v3-turbo` for "just works" experience

---

## Files Changed

### Engine SDK

| File | Change |
|------|--------|
| `dalston/engine_sdk/base.py` | `_set_runtime_state()`, `get_runtime_state()` |
| `dalston/engine_sdk/model_manager.py` | TTL/LRU model manager base class |
| `dalston/engine_sdk/model_storage.py` | S3ModelStorage for download/caching |
| `dalston/engine_sdk/managers/` | FasterWhisperModelManager, HFTransformersModelManager |
| `dalston/engine_sdk/types.py` | `runtime` field in EngineCapabilities |
| `dalston/engine_sdk/registry.py` | `loaded_model` in heartbeat |
| `dalston/engine_sdk/runner.py` | Pass runtime state to heartbeat |

### Orchestrator

| File | Change |
|------|--------|
| `dalston/orchestrator/catalog.py` | EngineCatalog with model lookup |
| `dalston/orchestrator/engine_selector.py` | Runtime-aware engine selection |
| `dalston/orchestrator/registry.py` | `loaded_model` in BatchEngineState |
| `dalston/orchestrator/generated_catalog.json` | Generated from models/*.yaml |

### Gateway API

| File | Change |
|------|--------|
| `dalston/gateway/api/v1/models.py` | Catalog endpoints |
| `dalston/gateway/api/v1/engines.py` | `loaded_model`, `available_models` fields |

### Model Definitions

| File | Description |
|------|-------------|
| `models/faster-whisper-*.yaml` | 6 Whisper model definitions |
| `models/parakeet-*.yaml` | 7 Parakeet model definitions |
| `models/parakeet-onnx-*.yaml` | 5 Parakeet ONNX definitions |
| `models/vllm-asr-*.yaml` | 3 Audio LLM definitions |

### Engine Implementations

| File | Change |
|------|--------|
| `engines/stt-transcribe/faster-whisper/engine.py` | Model swapping, SUPPORTED_MODELS |
| `engines/stt-transcribe/parakeet/engine.py` | Model swapping for NeMo |
| `engines/stt-transcribe/*/engine.yaml` | Runtime field added |

---

## Verification

```bash
# All tests pass
pytest tests/unit/test_engine*.py tests/unit/test_dag.py tests/integration/test_capability_driven_dag.py -v
# 135 tests passed

# Lint
ruff check dalston/engine_sdk/*.py dalston/orchestrator/*.py
# All checks passed

# Catalog generation
python scripts/generate_catalog.py
# Generated 18 models, 5 runtimes
```

---

## Related Milestones

- **M31**: Capability-Driven Routing (prerequisite)
- **M32**: Engine Variant Structure (prerequisite)
- **M40**: Model Registry & Aliases (builds on this)
