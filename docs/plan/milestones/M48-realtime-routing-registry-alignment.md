# M48: Real-time Routing Registry Alignment

| | |
|---|---|
| **Goal** | Ensure RT session routing only considers workers whose runtime has downloaded models in the registry |
| **Duration** | 0.5 days |
| **Dependencies** | M46 (Model Registry as Source of Truth) |
| **Deliverable** | Session router filters workers by valid runtimes from registry |
| **Status** | Planned |

## Problem Statement

When no specific model is requested (via "Any available" in console OR ElevenLabs/OpenAI compatible endpoints):

- The session router prefers workers with `models_loaded`, regardless of whether those models are in the registry
- This routes sessions to workers like `nemo-onnx-cpu` (with preloaded models) even when no nemo-onnx models are downloaded

**This affects API compatibility**: ElevenLabs/OpenAI endpoints use generic model names (e.g., `scribe_v1`) that map to `routing_model = None`. Clients have no control over which worker handles their request, and may get routed to workers with models that aren't properly registered.

**Root cause**: Routing uses Redis worker state (`models_loaded`), but the source of truth for available models is the registry database.

**Example scenario:**

1. User has downloaded `Systran/faster-whisper-base` (runtime: `faster-whisper`)
2. Worker `nemo-onnx-cpu` has `parakeet-onnx-ctc-0.6b` preloaded
3. User selects "Any available" or calls ElevenLabs endpoint
4. Session routes to `nemo-onnx-cpu` because it has models loaded
5. User has no visibility into why this worker was selected

## Solution

When `model=None` (no specific model requested), filter workers by runtimes that have at least one downloaded model in the registry.

```
Before:
┌────────────┐   model=None   ┌────────────────┐
│  Gateway   ├───────────────▶│ Session Router │──▶ ANY worker with models_loaded
└────────────┘                └────────────────┘

After:
┌────────────┐   model=None   ┌──────────┐   valid_runtimes   ┌────────────────┐
│  Gateway   ├───────────────▶│ Registry ├───────────────────▶│ Session Router │
└────────────┘                └──────────┘                    └────────────────┘
                                   │                                   │
                                   │ {faster-whisper, nemo}            │
                                   └───────────────────────────────────┘
                                        Only workers with matching runtime
```

---

## Implementation

### Phase 1: Add valid_runtimes parameter to WorkerRegistry

**File:** `dalston/session_router/registry.py`

Add `valid_runtimes: set[str] | None = None` parameter to `get_available_workers()`:

```python
async def get_available_workers(
    self,
    model: str | None,
    language: str,
    runtime: str | None = None,
    valid_runtimes: set[str] | None = None,  # NEW
) -> list[WorkerState]:
```

Add filtering logic:

```python
# When model=None and valid_runtimes specified, filter by runtime
if model is None and runtime is None and valid_runtimes is not None:
    if worker.runtime not in valid_runtimes:
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
    runtime: str | None = None,
    valid_runtimes: set[str] | None = None,  # NEW
) -> WorkerAllocation | None:
    # ...
    available = await self._registry.get_available_workers(
        model, language, runtime, valid_runtimes  # Pass new param
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
    runtime: str | None = None,
    valid_runtimes: set[str] | None = None,  # NEW
) -> WorkerAllocation | None:
    # ...
    return await self._allocator.acquire_worker(
        language=language,
        model=model,
        client_ip=client_ip,
        runtime=runtime,
        valid_runtimes=valid_runtimes,  # Pass new param
    )
```

### Phase 4: Query registry in Gateway

**File:** `dalston/gateway/api/v1/realtime.py`

When `routing_model is None`, query registry for downloaded models and extract runtimes:

```python
# Model parameter: use engine ID directly or None for any available worker
routing_model = model if model else None
model_runtime = None
valid_runtimes: set[str] | None = None  # NEW

if routing_model:
    # Look up model's runtime for routing
    # ... existing code ...
else:
    # When "Any available" selected, get valid runtimes from registry
    try:
        async for db in _get_db():
            model_service = ModelRegistryService()
            downloaded_models = await model_service.list_models(
                db, stage="transcribe", status="ready"
            )
            valid_runtimes = {m.runtime for m in downloaded_models if m.runtime}
            break
    except Exception as e:
        logger.warning("registry_lookup_failed", error=str(e))

# Acquire worker from Session Router
allocation = await session_router.acquire_worker(
    language=language,
    model=routing_model,
    client_ip=client_ip,
    runtime=model_runtime,
    valid_runtimes=valid_runtimes,  # NEW
)
```

Apply same change to ElevenLabs endpoint handler.

---

## Files Changed

| File | Action |
|------|--------|
| `dalston/session_router/registry.py` | Add `valid_runtimes` param, add filter logic |
| `dalston/session_router/allocator.py` | Add `valid_runtimes` param, pass through |
| `dalston/session_router/router.py` | Add `valid_runtimes` param, pass through |
| `dalston/gateway/api/v1/realtime.py` | Query registry, pass `valid_runtimes` to router |

---

## Verification

1. **Setup test state:**
   - Have `nemo-onnx-cpu` worker with preloaded model
   - Have `faster-whisper-cpu` worker without preloaded model
   - Ensure only `faster-whisper` models are downloaded in registry (not `nemo-onnx`)

2. **Test "Any available" routing:**

   ```bash
   # Start session with "Any available"
   # Check which worker was allocated
   docker compose logs --tail=20 gateway | grep session_allocated
   ```

   - Session should route to `faster-whisper-cpu` (runtime has downloaded models)
   - NOT to `nemo-onnx-cpu` (runtime has no downloaded models in registry)

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

### Why filter by runtime, not by models_loaded?

The registry tracks which models are **downloaded and available for use**. A worker may have a model preloaded on startup that:

- Isn't in the registry (admin preload, test model)
- Isn't meant for production use
- Users can't select in the UI

Filtering by registry runtimes ensures routing consistency with the UI and API model selection.

### Performance consideration

This adds one DB query per "Any available" session. The query is simple (filter by stage/status, extract runtimes) and should be fast. If needed, we could cache valid runtimes with a short TTL.
