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

### Current State

```yaml
# Current docker-compose.yml - 7+ separate volumes
volumes:
  model-cache:       # faster-whisper
  pyannote-models:   # diarization
  nemo-models:       # parakeet
  voxtral-models:    # voxtral
  align-models:      # phoneme alignment
  gliner-models:     # PII detection
  realtime-models:   # real-time engines
```

Each engine has its own cache directory env vars (`HF_HOME`, `NEMO_CACHE`, `TORCH_HOME`, etc.)

### Target State

```yaml
# Single shared volume
volumes:
  model-cache:  # All models

x-model-volumes: &model-volumes
  - model-cache:/models:rw

# All engine services use:
services:
  stt-batch-transcribe-faster-whisper:
    volumes: *model-volumes
  stt-batch-diarize-pyannote:
    volumes: *model-volumes
  # ... etc
```

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

**Create `dalston/engine_sdk/model_paths.py`:**

```python
"""Standardized model paths for all engines."""
import os
from pathlib import Path

MODEL_BASE = Path(os.environ.get("DALSTON_MODEL_DIR", "/models"))

# Per-framework subdirectories
HF_CACHE = MODEL_BASE / "huggingface"
CTRANSLATE2_CACHE = MODEL_BASE / "ctranslate2"
NEMO_CACHE = MODEL_BASE / "nemo"
TORCH_CACHE = MODEL_BASE / "torch"

def get_hf_model_path(model_id: str) -> Path:
    """Get expected path for a HuggingFace model."""
    safe_id = model_id.replace("/", "--")
    return HF_CACHE / "hub" / f"models--{safe_id}"

def is_model_cached(model_id: str, framework: str = "huggingface") -> bool:
    """Check if model is already downloaded."""
    if framework == "huggingface":
        path = get_hf_model_path(model_id)
        return path.exists() and any(path.iterdir())
    return False

def ensure_cache_dirs() -> None:
    """Create cache directories if they don't exist."""
    for cache_dir in [HF_CACHE, CTRANSLATE2_CACHE, NEMO_CACHE, TORCH_CACHE]:
        cache_dir.mkdir(parents=True, exist_ok=True)
```

**Update all engine Dockerfiles:**

```dockerfile
# Standardized environment variables
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
```

### Files to Modify

| File | Change |
|------|--------|
| `docker-compose.yml` | Remove 6 volumes, unify to `model-cache` |
| `dalston/engine_sdk/model_paths.py` | Create - centralized path utilities |
| `engines/stt-transcribe/faster-whisper/Dockerfile` | Update env vars |
| `engines/stt-transcribe/parakeet/Dockerfile` | Update env vars |
| `engines/stt-diarize/pyannote-4.0/Dockerfile` | Update env vars |
| `engines/stt-align/phoneme-align/Dockerfile` | Update env vars |
| `engines/stt-detect/pii-presidio/Dockerfile` | Update env vars |
| `engines/stt-transcribe/voxtral/Dockerfile` | Update env vars |

### Migration Path

For existing deployments:

```bash
# 1. Stop services
make stop

# 2. Copy models to unified volume (optional - models will re-download)
docker run --rm \
  -v pyannote-models:/old \
  -v model-cache:/new \
  alpine sh -c "mkdir -p /new/huggingface && cp -r /old/* /new/huggingface/"

# 3. Remove old volumes
docker volume rm pyannote-models nemo-models voxtral-models align-models gliner-models realtime-models

# 4. Start with new config
make dev
```

---

## 39.2: TTL-Based Model Manager

### Current State

Models stay loaded indefinitely after first use:

```python
# Current pattern in engines
def _ensure_model_loaded(self, model_id: str):
    if model_id == self._loaded_model_id:
        return
    # Unload previous, load new
    self._model = load_model(model_id)
    self._loaded_model_id = model_id
```

Problems:

- Only one model loaded at a time
- No eviction of idle models
- Manual swapping on every different request

### Target State

```python
# New pattern with ModelManager
class WhisperEngine(Engine):
    def __init__(self):
        self._manager = FasterWhisperModelManager(
            ttl_seconds=3600,    # Evict after 1 hour idle
            max_loaded=2,        # Max 2 models in GPU memory
        )

    def process(self, input: TaskInput) -> TaskOutput:
        model_id = input.config.get("runtime_model_id")
        model = self._manager.acquire(model_id)
        try:
            result = model.transcribe(input.audio_path)
            return TaskOutput(data=result)
        finally:
            self._manager.release(model_id)
```

### ModelManager Base Class

**Create `dalston/engine_sdk/model_manager.py`:**

```python
"""TTL-based model manager with reference counting and LRU eviction."""
from __future__ import annotations
import gc
import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar
import structlog

T = TypeVar("T")  # Model type

logger = structlog.get_logger()


@dataclass
class LoadedModel(Generic[T]):
    """Wrapper tracking a loaded model's lifecycle."""
    model_id: str
    model: T
    loaded_at: float
    last_used_at: float
    ref_count: int = 0
    size_bytes: int | None = None

    def touch(self) -> None:
        self.last_used_at = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used_at


class ModelManager(ABC, Generic[T]):
    """
    Base class for TTL-based model management.

    Features:
    - Reference counting for safe eviction during requests
    - LRU eviction when at max_loaded capacity
    - Background TTL eviction thread
    - Thread-safe operations
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_loaded = max_loaded
        self._models: dict[str, LoadedModel[T]] = {}
        self._lock = threading.RLock()
        self._eviction_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

        # Start background eviction thread
        self._start_eviction_thread()

        # Preload default model if specified
        if preload:
            self.acquire(preload)
            self.release(preload)

    def acquire(self, model_id: str) -> T:
        """
        Acquire a model for use. Loads if not already loaded.

        Increments reference count to prevent eviction during use.
        Caller MUST call release() when done.
        """
        with self._lock:
            if model_id not in self._models:
                self._maybe_evict_for_capacity()
                model = self._load_model(model_id)
                self._models[model_id] = LoadedModel(
                    model_id=model_id,
                    model=model,
                    loaded_at=time.time(),
                    last_used_at=time.time(),
                )
                logger.info("model_loaded", model_id=model_id)

            entry = self._models[model_id]
            entry.ref_count += 1
            entry.touch()
            return entry.model

    def release(self, model_id: str) -> None:
        """Release a model reference. May trigger TTL eviction later."""
        with self._lock:
            if model_id in self._models:
                entry = self._models[model_id]
                entry.ref_count = max(0, entry.ref_count - 1)

    def _maybe_evict_for_capacity(self) -> None:
        """Evict LRU model if at max capacity. Called under lock."""
        if len(self._models) < self.max_loaded:
            return

        # Find LRU model with ref_count=0
        candidates = [
            (mid, m) for mid, m in self._models.items()
            if m.ref_count == 0
        ]
        if not candidates:
            raise RuntimeError(
                f"Cannot load model: {self.max_loaded} models in use, none idle"
            )

        lru_id, _ = min(candidates, key=lambda x: x[1].last_used_at)
        self._evict(lru_id)

    def _evict(self, model_id: str) -> None:
        """Evict a specific model. Called under lock."""
        entry = self._models.pop(model_id, None)
        if entry:
            logger.info(
                "model_evicted",
                model_id=model_id,
                idle_seconds=entry.idle_seconds,
            )
            self._unload_model(entry.model)
            del entry
            gc.collect()
            self._cleanup_gpu_memory()

    def _start_eviction_thread(self) -> None:
        """Start background thread for TTL-based eviction."""
        def eviction_loop():
            while not self._shutdown.wait(timeout=60):  # Check every minute
                self._evict_expired()

        self._eviction_thread = threading.Thread(
            target=eviction_loop, daemon=True, name="model-eviction"
        )
        self._eviction_thread.start()

    def _evict_expired(self) -> None:
        """Evict models that have exceeded TTL."""
        with self._lock:
            now = time.time()
            expired = [
                mid for mid, m in self._models.items()
                if m.ref_count == 0 and (now - m.last_used_at) > self.ttl_seconds
            ]
            for model_id in expired:
                self._evict(model_id)

    def shutdown(self) -> None:
        """Shutdown manager and unload all models."""
        self._shutdown.set()
        with self._lock:
            for model_id in list(self._models.keys()):
                self._evict(model_id)

    def get_stats(self) -> dict:
        """Return current manager statistics."""
        with self._lock:
            return {
                "loaded_models": list(self._models.keys()),
                "model_count": len(self._models),
                "max_loaded": self.max_loaded,
                "ttl_seconds": self.ttl_seconds,
                "models": {
                    mid: {
                        "ref_count": m.ref_count,
                        "idle_seconds": m.idle_seconds,
                        "loaded_at": m.loaded_at,
                    }
                    for mid, m in self._models.items()
                },
            }

    @abstractmethod
    def _load_model(self, model_id: str) -> T:
        """Load a model. Implemented by subclasses."""
        raise NotImplementedError

    @abstractmethod
    def _unload_model(self, model: T) -> None:
        """Unload a model. Implemented by subclasses."""
        raise NotImplementedError

    def _cleanup_gpu_memory(self) -> None:
        """Optional GPU memory cleanup. Override if needed."""
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except ImportError:
            pass
```

### Faster Whisper Manager

**Create `dalston/engine_sdk/managers/faster_whisper.py`:**

```python
"""Faster Whisper model manager."""
import os
from faster_whisper import WhisperModel
from dalston.engine_sdk.model_manager import ModelManager
from dalston.engine_sdk.model_paths import CTRANSLATE2_CACHE


class FasterWhisperModelManager(ModelManager[WhisperModel]):
    """Model manager for CTranslate2/faster-whisper models."""

    def __init__(
        self,
        device: str = "cuda",
        compute_type: str = "float16",
        **kwargs,
    ):
        self.device = device
        self.compute_type = compute_type
        self.cache_dir = str(CTRANSLATE2_CACHE / "faster-whisper")
        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> WhisperModel:
        return WhisperModel(
            model_id,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.cache_dir,
        )

    def _unload_model(self, model: WhisperModel) -> None:
        del model
```

### Environment Variables

```bash
# New configuration options
DALSTON_MODEL_TTL_SECONDS=3600      # Default: 1 hour
DALSTON_MAX_LOADED_MODELS=2         # Default: 2 models
DALSTON_MODEL_PRELOAD=large-v3-turbo  # Optional: preload on startup
```

### Engine Integration Example

**Update `engines/stt-transcribe/faster-whisper/engine.py`:**

```python
class WhisperEngine(Engine):
    def __init__(self):
        super().__init__()
        device, compute_type = self._detect_device()

        self._manager = FasterWhisperModelManager(
            device=device,
            compute_type=compute_type,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", 3600)),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", 2)),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

    def process(self, input: TaskInput) -> TaskOutput:
        model_id = input.config.get(
            "runtime_model_id",
            os.environ.get("DALSTON_DEFAULT_MODEL_ID", "large-v3-turbo")
        )

        model = self._manager.acquire(model_id)
        try:
            segments, info = model.transcribe(
                str(input.audio_path),
                language=input.config.get("language"),
                beam_size=input.config.get("beam_size", 5),
            )
            return TaskOutput(data=self._build_output(segments, info))
        finally:
            self._manager.release(model_id)
```

---

## Files Summary

### New Files

| File | Description |
|------|-------------|
| `dalston/engine_sdk/model_paths.py` | Centralized model path utilities |
| `dalston/engine_sdk/model_manager.py` | Base ModelManager class |
| `dalston/engine_sdk/managers/__init__.py` | Managers package |
| `dalston/engine_sdk/managers/faster_whisper.py` | Faster Whisper manager |
| `dalston/engine_sdk/managers/nemo.py` | NeMo manager |
| `dalston/engine_sdk/managers/hf_transformers.py` | HuggingFace Transformers manager |

### Modified Files

| File | Change |
|------|--------|
| `docker-compose.yml` | Consolidate volumes |
| `engines/stt-transcribe/faster-whisper/Dockerfile` | Update env vars |
| `engines/stt-transcribe/faster-whisper/engine.py` | Use ModelManager |
| `engines/stt-transcribe/parakeet/Dockerfile` | Update env vars |
| `engines/stt-transcribe/parakeet/engine.py` | Use ModelManager |
| `engines/stt-diarize/pyannote-4.0/Dockerfile` | Update env vars |
| `engines/stt-align/phoneme-align/Dockerfile` | Update env vars |
| `engines/stt-detect/pii-presidio/Dockerfile` | Update env vars |

---

## Verification

### 39.1 Tests

```bash
# Build all engines with unified paths
make build-cpu

# Start minimal stack
make dev-minimal

# Verify models download to correct location
docker compose exec stt-batch-transcribe-faster-whisper ls -la /models/
# Should show: huggingface/, ctranslate2/, torch/

# Verify HuggingFace cache structure
docker compose exec stt-batch-transcribe-faster-whisper ls /models/huggingface/hub/
# Should show: models--Systran--faster-whisper-large-v3-turbo/

# Run transcription to trigger model download
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.wav" \
  -F "model_id=faster-whisper-large-v3-turbo"
```

### 39.2 Tests

```bash
# Unit tests for ModelManager
pytest tests/unit/engine_sdk/test_model_manager.py -v

# Test eviction behavior
DALSTON_MAX_LOADED_MODELS=1 DALSTON_MODEL_TTL_SECONDS=10 \
  python -c "
from dalston.engine_sdk.managers.faster_whisper import FasterWhisperModelManager
import time

mgr = FasterWhisperModelManager(device='cpu', compute_type='int8', max_loaded=1)

# Load first model
m1 = mgr.acquire('tiny')
mgr.release('tiny')
print(mgr.get_stats())

# Load second model - should evict first
m2 = mgr.acquire('base')
mgr.release('base')
print(mgr.get_stats())  # Should only have 'base'

# Wait for TTL and trigger eviction
time.sleep(15)
print(mgr.get_stats())  # Should be empty after TTL
"
```

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

**Next**: M40 (Model Registry & Aliases)
