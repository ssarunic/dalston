# M49: ADR-012 Terminology Unification

## Goal

Unify batch and RT terminology per ADR-012. **No backwards compatibility** - break everything, fail fast, fix immediately.

## Terminology Changes

| Old (Batch) | Old (RT) | New |
|-------------|----------|-----|
| `engine_id` | `engine` (legacy) | **`runtime`** |
| `instance_id` | `worker_id` | **`instance`** |

## Implementation Status

### ✅ Completed

#### 1. Database Schema

- [x] Migration: Columns renamed
- [x] Models: `TaskModel.runtime`, `RealtimeSessionModel.instance`

#### 2. Batch SDK (`dalston/engine_sdk/`)

- [x] `registry.py`: `RUNTIME_SET_KEY`, `INSTANCE_KEY_PREFIX`, `RUNTIME_INSTANCES_PREFIX`
- [x] `BatchEngineInfo.runtime`, `BatchEngineInfo.instance`
- [x] `base.py`: `DALSTON_RUNTIME`, `DALSTON_INSTANCE` env vars

#### 3. RT SDK & Session Router

- [x] `session_router/registry.py`: `INSTANCE_SET_KEY`, `INSTANCE_KEY_PREFIX`, `WorkerState.instance`
- [x] `session_router/allocator.py`: `WorkerAllocation.runtime`, `SessionState.instance`
- [x] `realtime_sdk/`: `DALSTON_INSTANCE` env var

#### 4. Gateway APIs

- [x] `models/responses.py`: `StageResponse.runtime`, `TaskResponse.runtime`
- [x] `api/v1/realtime_status.py`: `/workers/{instance}` endpoint
- [x] `api/console.py`: Imports registry constants (single source of truth)

#### 5. Orchestrator

- [x] `registry.py`: `BatchEngineState.runtime`, `BatchEngineState.instance`
- [x] `catalog.py`: `CatalogEntry.runtime`
- [x] `reconciler.py`: Uses orchestrator registry constants

#### 6. Common Modules

- [x] `pipeline_types.py`: All stage outputs use `runtime`
- [x] `events.py`: `publish_engine_needed(runtime=...)`
- [x] `streams.py`, `streams_sync.py`: Import `INSTANCE_KEY_PREFIX`

#### 7. Web Console

- [x] `api/types.ts`: `Task.runtime`, `WorkerStatus.instance`, `BatchEngine.runtime`
- [x] React components updated

#### 8. Docker & Config

- [x] Environment variables: `DALSTON_RUNTIME`, `DALSTON_INSTANCE`

#### 9. Engine Implementations

- [x] All engine.py files use `self._runtime` and `DALSTON_RUNTIME` env var
- [x] Health checks return `runtime` field
- [x] Capabilities use `runtime` field

#### 10. CLI Tools

- [x] `scaffold_engine.py`: Uses `runtime` terminology
- [x] `validate_engine.py`: Uses `runtime` in ValidationResult
- [x] `generate_catalog.py`: Outputs `runtime` field

#### 11. Tests

- [x] All test files updated with correct field names
- [x] Test helper functions use `runtime` and `instance` parameters
- [x] Redis key patterns in assertions updated

## Verification

```bash
# Core imports work
python -c "from dalston.gateway.main import app; from dalston.orchestrator.main import main"

# All tests pass
make test  # 1723 passed, 3 skipped

# Lint passes
make lint
```

## Redis Key Patterns (New)

```
# Batch engines
dalston:batch:runtimes                    # Set of logical runtime names
dalston:batch:instance:{instance}         # Hash with instance state
dalston:batch:runtime:instances:{runtime} # Set of instances per runtime

# Realtime workers
dalston:realtime:instances                # Set of instance IDs
dalston:realtime:instance:{instance}      # Hash with worker state
```

## Notes

- **NO aliases, NO deprecation period**
- Clean break from legacy terminology
- All tests passing
