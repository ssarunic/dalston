# M39: Model Cache & TTL Management

| | |
|---|---|
| **Goal** | Unified model storage with intelligent cache management and TTL-based unloading |
| **Duration** | 2-3 days |
| **Dependencies** | M36 (Runtime Model Management) Phase 1-2 complete |
| **Deliverable** | Single shared model volume, TTL-based model eviction, multi-model GPU efficiency |
| **Status** | Planned |

## Overview

Two complementary improvements to Dalston's model management:

1. **Unified Model Cache**: Consolidate 7+ per-engine Docker volumes into a single shared volume using HuggingFace Hub cache structure
2. **TTL-Based Model Manager**: Enable engines to load multiple models and automatically evict idle ones, maximizing GPU utilization

### Why This Matters

- **Simpler operations**: One volume to backup/migrate instead of 7+
- **Smaller disk footprint**: Shared models across engines (e.g., wav2vec2 used by align and diarize)
- **Multi-model efficiency**: Single GPU can serve multiple models by swapping based on demand
- **Foundation for `dalston model pull`**: M40's CLI needs predictable cache paths

---

## 39.1: Unified Model Cache

### Current vs Target State

Currently 7+ separate Docker volumes (one per engine) with per-engine env vars (`HF_HOME`, `NEMO_CACHE`, `TORCH_HOME`, etc.). Target: a single `model-cache` volume mounted at `/models` via a YAML anchor (`x-model-volumes`), shared by all engine services.

### Directory Structure

```
/models/
├── huggingface/           # HuggingFace Hub cache
│   └── hub/
│       ├── models--Systran--faster-whisper-large-v3/
│       ├── models--pyannote--speaker-diarization-3.1/
│       └── models--nvidia--parakeet-tdt-1.1b/
├── ctranslate2/           # CTranslate2 converted models
│   └── faster-whisper/
│       ├── large-v3-turbo/
│       └── large-v3/
├── nemo/                  # NeMo checkpoints (if not HF)
└── torch/                 # PyTorch hub cache
```

### Implementation

**Create `dalston/engine_sdk/model_paths.py`** — centralised path resolution for all frameworks. Reads `DALSTON_MODEL_DIR` (default `/models`) and exposes per-framework subdirectory constants (`HF_CACHE`, `CTRANSLATE2_CACHE`, `NEMO_CACHE`, `TORCH_CACHE`).

Key functions:

```python
def get_hf_model_path(model_id: str) -> Path: ...
def is_model_cached(model_id: str, framework: str = "huggingface") -> bool: ...
def ensure_cache_dirs() -> None: ...
```

**Update all engine Dockerfiles** to set standardised env vars (`DALSTON_MODEL_DIR=/models`, `HF_HUB_CACHE`, `HF_HOME`, `TORCH_HOME`, `NEMO_CACHE`, `WHISPER_MODELS_DIR`).

### Migration Path

For existing deployments: stop services, optionally copy old per-engine volumes into unified `model-cache` volume (or let models re-download), remove old volumes, then `make dev`.

---

## 39.2: TTL-Based Model Manager

### Current vs Target State

Currently engines load one model at a time and hold it indefinitely — no eviction, no multi-model support. Target: engines use a `ModelManager` that holds up to N models simultaneously, evicts idle ones via TTL, and uses LRU eviction at capacity. Callers wrap usage in `acquire`/`release` pairs.

### ModelManager Base Class

**Create `dalston/engine_sdk/model_manager.py`**

`LoadedModel[T]` dataclass wraps a loaded model with `model_id`, `model: T`, timestamps (`loaded_at`, `last_used_at`), `ref_count`, and `size_bytes`.

`ModelManager(ABC, Generic[T])` — thread-safe base class providing reference-counted model acquisition with LRU eviction at capacity and background TTL-based eviction. All state protected by `threading.RLock`; a daemon thread checks for expired models every 60 seconds. GPU memory is cleaned via `torch.cuda.empty_cache()` after eviction.

```python
class ModelManager(ABC, Generic[T]):
    def __init__(self, ttl_seconds: int = 3600, max_loaded: int = 2, preload: str | None = None): ...
    def acquire(self, model_id: str) -> T: ...       # Load-or-reuse, increment ref_count
    def release(self, model_id: str) -> None: ...     # Decrement ref_count
    def shutdown(self) -> None: ...                    # Unload all models
    def get_stats(self) -> dict: ...                   # Current load/idle info

    # Subclass hooks
    @abstractmethod
    def _load_model(self, model_id: str) -> T: ...
    @abstractmethod
    def _unload_model(self, model: T) -> None: ...
    def _cleanup_gpu_memory(self) -> None: ...         # Optional override
```

**Design decisions:**

- **Reference counting** prevents eviction of in-use models — callers must pair `acquire`/`release`.
- **LRU eviction at capacity**: when `max_loaded` is reached and a new model is requested, the least-recently-used model with `ref_count == 0` is evicted. If all models are in use, a `RuntimeError` is raised.
- **Background TTL thread**: daemon thread avoids holding idle models in GPU memory indefinitely. Runs every 60s, evicts models idle longer than `ttl_seconds`.

### Faster Whisper Manager

**Create `dalston/engine_sdk/managers/faster_whisper.py`** — `FasterWhisperModelManager(ModelManager[WhisperModel])`. Accepts `device` and `compute_type` constructor args. `_load_model` instantiates `WhisperModel` with `download_root` pointing to the unified cache. `_unload_model` simply deletes the reference (base class handles `gc.collect` and GPU cleanup). Additional managers (`nemo.py`, `hf_transformers.py`) follow the same pattern.

### Environment Variables

```bash
# New configuration options
DALSTON_MODEL_TTL_SECONDS=3600      # Default: 1 hour
DALSTON_MAX_LOADED_MODELS=2         # Default: 2 models
DALSTON_MODEL_PRELOAD=large-v3-turbo  # Optional: preload on startup
```

### Engine Integration

Engines create a framework-specific `ModelManager` subclass in `__init__` (reading TTL/max-loaded from env vars), then wrap model usage in `acquire`/`release` within `process`.

---

## Verification

### 39.1 Checklist

- [ ] `make build-cpu` succeeds with unified volume config
- [ ] All engines mount `/models` and model downloads land in correct subdirectories (`huggingface/`, `ctranslate2/`, etc.)
- [ ] End-to-end transcription works after volume consolidation
- [ ] Old per-engine volumes can be removed without breaking anything

### 39.2 Checklist

- [ ] Unit tests pass for `ModelManager` base class (`test_model_manager.py`)
- [ ] LRU eviction fires when `max_loaded` is reached
- [ ] TTL eviction removes idle models after configured timeout
- [ ] Models with `ref_count > 0` are never evicted
- [ ] `get_stats()` returns accurate load/idle information

---

## Checkpoint

- [ ] **39.1**: Single `model-cache` volume in docker-compose.yml
- [ ] **39.1**: All engines use standardized env vars
- [ ] **39.1**: `model_paths.py` utility module created
- [ ] **39.2**: `ModelManager` base class implemented
- [ ] **39.2**: `FasterWhisperModelManager` working
- [ ] **39.2**: TTL eviction verified
- [ ] **39.2**: Max loaded limit verified
- [ ] **39.2**: Engines updated to use ModelManager