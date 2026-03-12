# M48: Real-time Routing Registry Alignment

| | |
|---|---|
| **Goal** | Ensure RT session routing only considers workers whose engine_id has downloaded models in the registry |
| **Duration** | 0.5 days |
| **Dependencies** | M46 (Model Registry as Source of Truth) |
| **Deliverable** | Session router filters workers by valid engine_ids from registry |
| **Status** | Planned |

## Problem Statement

When no specific model is requested (via "Any available" in console OR ElevenLabs/OpenAI compatible endpoints):

- The session router prefers workers with `models_loaded`, regardless of whether those models are in the registry
- This routes sessions to workers like `onnx-cpu` (with preloaded models) even when no onnx models are downloaded

**This affects API compatibility**: ElevenLabs/OpenAI endpoints use generic model names (e.g., `scribe_v1`) that map to `routing_model = None`. Clients have no control over which worker handles their request, and may get routed to workers with models that aren't properly registered.

**Root cause**: Routing uses Redis worker state (`models_loaded`), but the source of truth for available models is the registry database.

**Example scenario:**

1. User has downloaded `Systran/faster-whisper-base` (engine_id: `faster-whisper`)
2. Worker `onnx-cpu` has `parakeet-onnx-ctc-0.6b` preloaded
3. User selects "Any available" or calls ElevenLabs endpoint
4. Session routes to `onnx-cpu` because it has models loaded
5. User has no visibility into why this worker was selected

## Solution

When `model=None` (no specific model requested), filter workers by engine_ids that have at least one downloaded model in the registry.

```
Before:
┌────────────┐   model=None   ┌────────────────┐
│  Gateway   ├───────────────▶│ Session Router │──▶ ANY worker with models_loaded
└────────────┘                └────────────────┘

After:
┌────────────┐   model=None   ┌──────────┐   valid_engine_ids   ┌────────────────┐
│  Gateway   ├───────────────▶│ Registry ├───────────────────▶│ Session Router │
└────────────┘                └──────────┘                    └────────────────┘
                                   │                                   │
                                   │ {faster-whisper, nemo}            │
                                   └───────────────────────────────────┘
                                        Only workers with matching engine_id
```

---

## Implementation

### Phase 1: Add valid_engine_ids parameter to WorkerRegistry

**File:** `dalston/session_router/registry.py`

Add `valid_engine_ids: set[str] | None = None` parameter to `get_available_workers()`:

```python
async def get_available_workers(
    self,
    model: str | None,
    language: str,
    engine_id: str | None = None,
    valid_engine_ids: set[str] | None = None,  # NEW
) -> list[WorkerState]:
```

Add filtering logic:

```python
# When model=None and valid_engine_ids specified, filter by engine_id
if model is None and engine_id is None and valid_engine_ids is not None:
    if worker.engine_id not in valid_engine_ids:
        continue
```

### Phase 2: Propagate through SessionAllocator

**File:** `dalston/session_router/allocator.py`

Add parameter to `acquire_worker()` and pass to registry:

```python
async def acquire_worker(
    self,
    language: str,
    model: str | None,
    client_ip: str,
    engine_id: str | None = None,
    valid_engine_ids: set[str] | None = None,  # NEW
) -> WorkerAllocation | None:
    # ...
    available = await self._registry.get_available_workers(
        model, language, engine_id, valid_engine_ids  # Pass new param
    )
```

### Phase 3: Propagate through SessionRouter

**File:** `dalston/session_router/router.py`

Add parameter to `acquire_worker()` and pass to allocator:

```python
async def acquire_worker(
    self,
    language: str,
    model: str | None,
    client_ip: str,
    engine_id: str | None = None,
    valid_engine_ids: set[str] | None = None,  # NEW
) -> WorkerAllocation | None:
    # ...
    return await self._allocator.acquire_worker(
        language=language,
        model=model,
        client_ip=client_ip,
        engine_id=engine_id,
        valid_engine_ids=valid_engine_ids,  # Pass new param
    )
```

### Phase 4: Query registry in Gateway

**File:** `dalston/gateway/api/v1/realtime.py`

When `routing_model is None`, query registry for downloaded models and extract engine_ids:

```python
# Model parameter: use engine ID directly or None for any available worker
routing_model = model if model else None
model_engine_id = None
valid_engine_ids: set[str] | None = None  # NEW

if routing_model:
    # Look up model's engine_id for routing
    # ... existing code ...
else:
    # When "Any available" selected, get valid engine_ids from registry
    try:
        async for db in _get_db():
            model_service = ModelRegistryService()
            downloaded_models = await model_service.list_models(
                db, stage="transcribe", status="ready"
            )
            valid_engine_ids = {m.engine_id for m in downloaded_models if m.engine_id}
            break
    except Exception as e:
        logger.warning("registry_lookup_failed", error=str(e))

# Acquire worker from Session Router
allocation = await session_router.acquire_worker(
    language=language,
    model=routing_model,
    client_ip=client_ip,
    engine_id=model_engine_id,
    valid_engine_ids=valid_engine_ids,  # NEW
)
```

Apply same change to ElevenLabs endpoint handler.

---

## Files Changed

| File | Action |
|------|--------|
| `dalston/session_router/registry.py` | Add `valid_engine_ids` param, add filter logic |
| `dalston/session_router/allocator.py` | Add `valid_engine_ids` param, pass through |
| `dalston/session_router/router.py` | Add `valid_engine_ids` param, pass through |
| `dalston/gateway/api/v1/realtime.py` | Query registry, pass `valid_engine_ids` to router |

---

## Verification

1. **Setup test state:**
   - Have `onnx-cpu` worker with preloaded model
   - Have `faster-whisper-cpu` worker without preloaded model
   - Ensure only `faster-whisper` models are downloaded in registry (not `onnx`)

2. **Test "Any available" routing:**

   ```bash
   # Start session with "Any available"
   # Check which worker was allocated
   docker compose logs --tail=20 gateway | grep session_allocated
   ```

   - Session should route to `faster-whisper-cpu` (engine_id has downloaded models)
   - NOT to `onnx-cpu` (engine_id has no downloaded models in registry)

3. **Test ElevenLabs endpoint:**

   ```bash
   # Connect via ElevenLabs WebSocket endpoint
   # Verify routing follows same rules
   ```

4. **Run existing tests:**

   ```bash
   make lint
   make test
   ```

---

## Design Notes

### Why filter by engine_id, not by models_loaded?

The registry tracks which models are **downloaded and available for use**. A worker may have a model preloaded on startup that:

- Isn't in the registry (admin preload, test model)
- Isn't meant for production use
- Users can't select in the UI

Filtering by registry engine_ids ensures routing consistency with the UI and API model selection.

### Performance consideration

This adds one DB query per "Any available" session. The query is simple (filter by stage/status, extract engine_ids) and should be fast. If needed, we could cache valid engine_ids with a short TTL.
