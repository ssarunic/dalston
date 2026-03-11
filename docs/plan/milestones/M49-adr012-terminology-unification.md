# M49: ADR-012 Terminology Unification

## Goal

Unify batch and RT terminology per ADR-012. **No backwards compatibility** - break everything, fail fast, fix immediately.

## Terminology Changes

| Old (Batch) | Old (RT) | New |
|-------------|----------|-----|
| `engine_id` | `engine` (legacy) | **`engine_id`** |
| `instance_id` | `worker_id` | **`instance`** |

## Implementation Status

### ✅ Completed

#### 1. Database Schema

- [x] Migration: Columns renamed
- [x] Models: `TaskModel.engine_id`, `RealtimeSessionModel.instance`

#### 2. Batch SDK (`dalston/engine_sdk/`)

- [x] `registry.py`: `RUNTIME_SET_KEY`, `INSTANCE_KEY_PREFIX`, `RUNTIME_INSTANCES_PREFIX`
- [x] `BatchEngineInfo.engine_id`, `BatchEngineInfo.instance`
- [x] `base.py`: `DALSTON_ENGINE_ID`, `DALSTON_INSTANCE` env vars

#### 3. RT SDK & Session Router

- [x] `session_router/registry.py`: `INSTANCE_SET_KEY`, `INSTANCE_KEY_PREFIX`, `WorkerState.instance`
- [x] `session_router/allocator.py`: `WorkerAllocation.engine_id`, `SessionState.instance`
- [x] `realtime_sdk/`: `DALSTON_INSTANCE` env var

#### 4. Gateway APIs

- [x] `models/responses.py`: `StageResponse.engine_id`, `TaskResponse.engine_id`
- [x] `api/v1/realtime_status.py`: `/workers/{instance}` endpoint
- [x] `api/console.py`: Imports registry constants (single source of truth)

#### 5. Orchestrator

- [x] `registry.py`: `BatchEngineState.engine_id`, `BatchEngineState.instance`
- [x] `catalog.py`: `CatalogEntry.engine_id`
- [x] `reconciler.py`: Uses orchestrator registry constants

#### 6. Common Modules

- [x] `pipeline_types.py`: All stage outputs use `engine_id`
- [x] `events.py`: `publish_engine_needed(engine_id=...)`
- [x] `streams.py`, `streams_sync.py`: Import `INSTANCE_KEY_PREFIX`

#### 7. Web Console

- [x] `api/types.ts`: `Task.engine_id`, `WorkerStatus.instance`, `BatchEngine.engine_id`
- [x] React components updated

#### 8. Docker & Config

- [x] Environment variables: `DALSTON_ENGINE_ID`, `DALSTON_INSTANCE`

#### 9. Engine Implementations

- [x] All engine.py files use `self._engine_id` and `DALSTON_ENGINE_ID` env var
- [x] Health checks return `engine_id` field
- [x] Capabilities use `engine_id` field

#### 10. CLI Tools

- [x] `scaffold_engine.py`: Uses `engine_id` terminology
- [x] `validate_engine.py`: Uses `engine_id` in ValidationResult
- [x] `generate_catalog.py`: Outputs `engine_id` field

#### 11. Tests

- [x] All test files updated with correct field names
- [x] Test helper functions use `engine_id` and `instance` parameters
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
dalston:batch:engine_ids                    # Set of logical engine_id names
dalston:batch:instance:{instance}         # Hash with instance state
dalston:batch:engine_id:instances:{engine_id} # Set of instances per engine_id

# Realtime workers
dalston:realtime:instances                # Set of instance IDs
dalston:realtime:instance:{instance}      # Hash with worker state
```

## Notes

- **NO aliases, NO deprecation period**
- Clean break from legacy terminology
- All tests passing
