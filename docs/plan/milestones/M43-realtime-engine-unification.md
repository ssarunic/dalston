# M43: Real-Time Engine Unification

| | |
|---|---|
| **Goal** | RT engines use the same registry + dynamic model loading architecture as batch engines |
| **Duration** | 3-5 days |
| **Dependencies** | M36 (Runtime Model Management), M40 (Model Registry) |
| **Deliverable** | Dynamic model loading for RT engines, consolidated Docker images (one per runtime) |
| **Status** | In Progress (Phase 2/2b Complete) |

> Note (2026-03-11): Base `docker-compose.yml` no longer declares
> `stt-rt-faster-whisper`. Default deployments use `stt-unified-faster-whisper*`.
> Any split-service names in this milestone are historical context.

## User Story

> *"As a platform operator, I can deploy a single RT container per runtime that serves any model variant, with models downloaded on-demand from S3."*

---

## Problem

Real-time engines are baked at compile time. Each model variant requires a separate Docker image:

```
stt-rt-transcribe-faster-whisper-large-v3
stt-rt-transcribe-faster-whisper-distil-large-v3
stt-rt-transcribe-parakeet-rnnt-0.6b
stt-rt-transcribe-parakeet-rnnt-1.1b
...
```

This means:

- Adding a new model requires building and deploying a new image
- GPU nodes run multiple containers for different models (wasted VRAM overhead)
- No runtime model swapping - restart required to change models
- Model names are ad-hoc strings, not catalog-validated

---

## Solution

RT engines download and load models dynamically using the same `ModelManager` and `S3ModelStorage` that batch engines use. One RT container per runtime serves any model variant.

```text
Before: 8 RT images (2 runtimes x 4 models each)
After:  2 RT images (faster-whisper-rt, parakeet-rt)
```

---

## Phases

### Phase 1: Capabilities Declaration (Complete)

**Deliverables:**

- [x] Add `engine.yaml` to all RT engines with runtime and capabilities
- [x] Add `get_capabilities()` to `RealtimeEngine` base class
- [x] Include structured capabilities in worker registration/heartbeats
- [x] Update Dockerfiles to use main engine.yaml

**Files changed:**

- `engines/stt-rt/*/engine.yaml` - New capability declarations
- `dalston/realtime_sdk/base.py` - `get_capabilities()`, `_load_engine_yaml()`
- `dalston/realtime_sdk/registry.py` - `WorkerInfo.capabilities`, `WorkerInfo.runtime`

### Phase 2: Dynamic Model Loading (Complete)

**Deliverables:**

- [x] `AsyncModelManager` wrapper for RT engines
- [x] RT engines use `ModelManager.acquire()/release()` per session (faster-whisper)
- [x] Session Router routes by `loaded_models` in heartbeat (warm routing)
- [x] Consolidate per-model Docker images into per-runtime images (faster-whisper)

**Key changes:**

```python
# Before: Models hardcoded at startup
class WhisperStreamingEngine(RealtimeEngine):
    MODELS = {"faster-whisper-large-v3": "Systran/faster-whisper-large-v3"}
    def load_models(self):
        for name, hf_id in self.MODELS.items():
            self._models[name] = WhisperModel(hf_id, device="cuda")

# After: Models loaded on-demand via ModelManager
class WhisperStreamingEngine(RealtimeEngine):
    def load_models(self):
        sync_manager = FasterWhisperModelManager(
            device=self._device,
            compute_type=self._compute_type,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
        )
        self._model_manager = AsyncModelManager(sync_manager)

    def transcribe(self, audio, language, model_variant, vocabulary=None):
        model_id = self._normalize_model_id(model_variant)
        model = self._model_manager.manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio, language, vocabulary)
        finally:
            self._model_manager.manager.release(model_id)
```

**Files changed:**

- `dalston/realtime_sdk/model_manager.py` - New `AsyncModelManager` wrapper
- `dalston/realtime_sdk/base.py` - Added `_model_manager`, `get_loaded_models()`
- `dalston/realtime_sdk/registry.py` - Added `loaded_models` to heartbeat
- `engines/stt-rt/faster-whisper/engine.py` - Refactored to use ModelManager
- `engines/stt-rt/faster-whisper/Dockerfile` - Removed model pre-baking
- `docker-compose.yml` - Consolidated `stt-rt-faster-whisper` service

**Notes:**

- Parakeet and Voxtral engines retain single-model-per-instance approach (model selected via `DALSTON_MODEL_VARIANT` env var at startup)
- Full dynamic model loading for NeMo/Transformers runtimes deferred to future enhancement

### Phase 2b: Web Console & Registry Impact (Complete)

**Deliverables:**

- [x] Add `runtime` field to Session Router's `WorkerState` and `WorkerStatus`
- [x] Add `runtime` field to Console API's `RealtimeWorker` response model
- [x] Update frontend `WorkerStatus` type with `runtime` field
- [x] Update `RealtimeWorkerCard` UI to display runtime badge and "Loaded models" label

**Files changed:**

- `dalston/session_router/registry.py` - Added `runtime` field to `WorkerState`
- `dalston/session_router/router.py` - Added `runtime` field to `WorkerStatus`
- `dalston/gateway/api/console.py` - Added `runtime` field to `RealtimeWorker`
- `web/src/api/types.ts` - Added `runtime` field to `WorkerStatus` interface
- `web/src/pages/Engines.tsx` - Updated `RealtimeWorkerCard` to show runtime and clarify loaded models

**UI Changes:**

The Real-time Workers card in the Engines page now shows:

- Runtime badge (e.g., "faster-whisper") next to worker ID
- "Loaded models" label above the model badges to clarify these are dynamically loaded

### Phase 3: Session Router Integration (Pending)

**Deliverables:**

- [ ] Session Router prefers workers with model already loaded (warm)
- [ ] Gateway validates model/language against capabilities before proxying
- [ ] Cold-start latency logging and metrics

---

## API Changes

### Worker Heartbeat (Phase 1)

New fields in worker registration:

```json
{
  "worker_id": "rt-fw-1",
  "engine_id": "stt-rt-faster-whisper",
  "runtime": "faster-whisper",
  "capabilities": { "languages": null, "streaming": true, ... },
  "loaded_models": ["Systran/faster-whisper-large-v3-turbo"],
  "active_sessions": 2
}
```

### Session Allocation (Phase 3)

Response includes warm/cold status:

```json
{
  "worker_id": "rt-fw-1",
  "worker_url": "ws://rt-fw-1:8001",
  "model_status": "warm"
}
```

---

## Success Criteria

- [x] RT engines serve any model variant without image rebuild (faster-whisper)
- [ ] Session Router routes to warm workers when available (Phase 3)
- [ ] Cold-start latency < 60s for largest model (Phase 3)
- [x] Number of RT Docker images reduced from N*M to N (runtimes only)

---

## References

- [M36: Runtime Model Management](M36-runtime-model-management.md) - Batch `ModelManager` implementation
- [M40: Model Registry](M40-model-registry.md) - Database model tracking
- [Engine Architecture Analysis](../reports/engines-architecture-analysis.md) - Research leading to this milestone
