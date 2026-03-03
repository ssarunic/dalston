# M50: Real-Time Engine Unification

**Status**: Draft
**Goal**: Real-time engines use the same registry + dynamic model loading architecture as batch engines

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

## Solution

Real-time engines download and load models dynamically using the same `ModelManager` and `S3ModelStorage` that batch engines use. One RT container per runtime serves any model variant.

```
Before: 8 RT images (2 runtimes × 4 models each)
After:  2 RT images (faster-whisper-rt, parakeet-rt)
```

---

## Deliverables

### 1. RT engines declare capabilities via `engine.yaml`

Add `engine.yaml` to each RT engine directory:

```yaml
# engines/stt-rt/faster-whisper/engine.yaml
engine_id: stt-rt-faster-whisper
stage: transcribe
runtime: faster-whisper
mode: realtime

capabilities:
  supports_streaming: true
  max_concurrency: 4
  languages: ["en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "zh", "ja", "ko"]

hardware:
  gpu_required: true
  min_vram_gb: 4
```

`RealtimeEngine` base class gains `get_capabilities()` that parses this file, mirroring `Engine.get_capabilities()`.

### 2. RT worker heartbeats include structured capabilities

Current heartbeat (flat strings):

```json
{
  "worker_id": "rt-fw-1",
  "models_loaded": ["faster-whisper-large-v3"],
  "languages_supported": ["en", "es", "fr"],
  "active_sessions": 2,
  "capacity": 4
}
```

New heartbeat (structured):

```json
{
  "worker_id": "rt-fw-1",
  "engine_id": "stt-rt-faster-whisper",
  "runtime": "faster-whisper",
  "capabilities": { ... },
  "loaded_models": ["Systran/faster-whisper-large-v3-turbo"],
  "active_sessions": 2,
  "capacity": 4
}
```

### 3. `AsyncModelManager` for RT engines

Batch `ModelManager` is synchronous (threading). RT engines need an async interface:

```python
class AsyncModelManager:
    """Async wrapper around ModelManager for use in RT engines."""

    def __init__(self, sync_manager: ModelManager):
        self._manager = sync_manager

    async def acquire(self, model_id: str) -> LoadedModel:
        """Load model (downloading if needed) and increment ref count."""
        return await asyncio.to_thread(self._manager.acquire, model_id)

    async def release(self, model_id: str) -> None:
        """Decrement ref count. Model evicted after TTL if refs hit zero."""
        await asyncio.to_thread(self._manager.release, model_id)
```

Location: `dalston/realtime_sdk/model_manager.py`

### 4. RT engines use `ModelManager` for dynamic loading

Before:

```python
class FasterWhisperRealtimeEngine(RealtimeEngine):
    MODELS = {
        "faster-whisper-large-v3": "Systran/faster-whisper-large-v3",
    }

    def load_models(self):
        for name, hf_id in self.MODELS.items():
            self._models[name] = WhisperModel(hf_id, device="cuda")
```

After:

```python
class FasterWhisperRealtimeEngine(RealtimeEngine):
    async def setup(self):
        sync_manager = FasterWhisperModelManager.from_env()
        self._manager = AsyncModelManager(sync_manager)

        # Optional: preload a default model
        if preload := os.environ.get("DALSTON_MODEL_PRELOAD"):
            await self._manager.acquire(preload)

    async def transcribe(self, audio, model_id, language, ...):
        model = await self._manager.acquire(model_id)
        try:
            return model.transcribe(audio, language=language, ...)
        finally:
            await self._manager.release(model_id)
```

### 5. Session Router routes by loaded model

`SessionAllocator` gains model-aware routing:

```python
async def allocate(self, model_id: str, ...) -> Worker:
    workers = await self._registry.get_workers(runtime=self._resolve_runtime(model_id))

    # Prefer workers with model already loaded (warm)
    warm = [w for w in workers if model_id in w.loaded_models and w.has_capacity]
    if warm:
        return self._least_loaded(warm)

    # Fall back to any worker with capacity (cold - will download model)
    available = [w for w in workers if w.has_capacity]
    if available:
        return self._least_loaded(available)

    raise NoCapacityError(f"No workers available for model {model_id}")
```

### 6. Consolidated RT Docker images

Before: One image per model variant

```
engines/stt-rt/faster-whisper-large-v3/Dockerfile
engines/stt-rt/faster-whisper-distil-large-v3/Dockerfile
engines/stt-rt/parakeet-rnnt-0.6b/Dockerfile
engines/stt-rt/parakeet-rnnt-1.1b/Dockerfile
```

After: One image per runtime

```
engines/stt-rt/faster-whisper/Dockerfile
engines/stt-rt/parakeet/Dockerfile
```

Model loaded at runtime based on session request.

---

## API Changes

### Gateway WebSocket endpoint

Model specified in connection query params (unchanged):

```
WS /v1/audio/transcriptions/stream?model=Systran/faster-whisper-large-v3-turbo
```

Model names are now catalog model IDs. Old ad-hoc names no longer work.

### Session Router internal API

`POST /internal/sessions/allocate` request gains `model_id`:

```json
{
  "model_id": "Systran/faster-whisper-large-v3-turbo",
  "language": "en"
}
```

Response includes cold/warm status:

```json
{
  "worker_id": "rt-fw-1",
  "worker_url": "ws://rt-fw-1:8001",
  "model_status": "warm"  // or "cold" - client may see initial latency
}
```

---

## File Changes

| File | Change |
|------|--------|
| `engines/stt-rt/faster-whisper/engine.yaml` | New - capabilities declaration |
| `engines/stt-rt/parakeet/engine.yaml` | New - capabilities declaration |
| `dalston/realtime_sdk/model_manager.py` | New - `AsyncModelManager` |
| `dalston/realtime_sdk/engine.py` | Add `get_capabilities()`, update base class |
| `dalston/realtime_sdk/registry.py` | Heartbeat includes capabilities + loaded_models |
| `dalston/session_router/allocator.py` | Model-aware worker selection |
| `dalston/session_router/registry.py` | Parse structured worker heartbeats |
| `engines/stt-rt/faster-whisper/engine.py` | Use `AsyncModelManager` |
| `engines/stt-rt/parakeet/engine.py` | Use `AsyncModelManager` |
| `docker-compose.yml` | Consolidate RT services |

---

## Testing

1. **Unit**: `AsyncModelManager` acquire/release with mocked sync manager
2. **Unit**: `SessionAllocator` warm vs cold routing logic
3. **Integration**: RT engine loads model on first request, serves subsequent requests from cache
4. **Integration**: Session Router prefers warm workers, falls back to cold
5. **E2E**: WebSocket connection with catalog model ID, successful transcription

---

## Rollout

1. Deploy new RT images alongside existing per-model images
2. Route 10% of traffic to new images, monitor latency
3. If cold-start latency is acceptable, increase to 100%
4. Remove per-model RT images

---

## Success Criteria

- [ ] RT engines serve any model variant without image rebuild
- [ ] Session Router routes to warm workers when available
- [ ] Cold-start latency < 60s for largest model (first request only)
- [ ] Number of RT Docker images reduced from N×M to N (where N=runtimes, M=models)
