# M43: Real-Time Engine Unification

| | |
|---|---|
| **Goal** | RT engines use the same registry + dynamic model loading architecture as batch engines |
| **Duration** | 3-5 days |
| **Dependencies** | M36 (Runtime Model Management), M40 (Model Registry) |
| **Deliverable** | Dynamic model loading for RT engines, consolidated Docker images (one per runtime) |
| **Status** | In Progress (Phase 1 Complete) |

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

### Phase 2: Dynamic Model Loading (Pending)

**Deliverables:**

- [ ] `AsyncModelManager` wrapper for RT engines
- [ ] RT engines use `ModelManager.acquire()/release()` per session
- [ ] Session Router routes by `loaded_models` in heartbeat (warm routing)
- [ ] Consolidate per-model Docker images into per-runtime images

**Key changes:**

```python
# Before: Models hardcoded at startup
class FasterWhisperRealtimeEngine(RealtimeEngine):
    MODELS = {"faster-whisper-large-v3": "Systran/faster-whisper-large-v3"}
    def load_models(self):
        for name, hf_id in self.MODELS.items():
            self._models[name] = WhisperModel(hf_id, device="cuda")

# After: Models loaded on-demand
class FasterWhisperRealtimeEngine(RealtimeEngine):
    async def setup(self):
        self._manager = AsyncModelManager(FasterWhisperModelManager.from_env())

    async def transcribe(self, audio, model_id, ...):
        model = await self._manager.acquire(model_id)
        try:
            return model.transcribe(audio, ...)
        finally:
            await self._manager.release(model_id)
```

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

- [ ] RT engines serve any model variant without image rebuild
- [ ] Session Router routes to warm workers when available
- [ ] Cold-start latency < 60s for largest model
- [ ] Number of RT Docker images reduced from N*M to N (runtimes only)

---

## References

- [M36: Runtime Model Management](M36-runtime-model-management.md) - Batch `ModelManager` implementation
- [M40: Model Registry](M40-model-registry.md) - Database model tracking
- [Engine Architecture Analysis](../reports/engines-architecture-analysis.md) - Research leading to this milestone
