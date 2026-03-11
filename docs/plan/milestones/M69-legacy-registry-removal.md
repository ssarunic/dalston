# M69: Legacy Registry Removal

| | |
|---|---|
| **Goal** | Remove dual-write/dual-read scaffolding and legacy batch/RT registry code after validated unified registry parity |
| **Duration** | 2-3 days |
| **Dependencies** | M64 (completed), production parity window on `unified` mode |
| **Primary Deliverable** | Single registry code path; deleted legacy modules, bridge converters, and migration config |
| **Status** | Complete |

## Context

M64 introduced the unified engine registry behind feature flags with a
dual-write/dual-read migration path. Once all environments run with
`DALSTON_ENGINE_REGISTRY_MODE=unified` and
`DALSTON_REGISTRY_UNIFIED_READ_ENABLED=true` for a stable release window,
the legacy code becomes dead weight. This milestone removes it.

## Pre-conditions (must be true before starting)

1. All engines and workers run with `DALSTON_ENGINE_REGISTRY_MODE=unified`.
2. All consumers run with `DALSTON_REGISTRY_UNIFIED_READ_ENABLED=true`.
3. No legacy Redis keys (`dalston:batch:*`, `dalston:realtime:*`) are being
   written or read in production for at least one release cycle.
4. Monitoring confirms zero parity alerts during the observation window.

## Outcomes

1. Legacy registry modules deleted — no split batch/RT registration code.
2. All consumers use `EngineRecord` directly — no bridge converter functions.
3. Migration feature flags removed from config.
4. Legacy Redis key patterns documented for cleanup but not actively used.
5. Test suite uses unified registry exclusively.

## Scope

In scope:

- Delete legacy registry client modules.
- Delete legacy server-side reader classes.
- Migrate all consumers from `BatchEngineState`/`WorkerState` to `EngineRecord`.
- Remove dual-write logic from engine runners.
- Remove migration config settings.
- Update and consolidate test suites.

Out of scope:

- Session-router service removal (covered by M66).
- Redis key cleanup scripts for leftover legacy data (ops concern, not code).
- Changes to unified registry behavior or schema.

## Files to Delete

| File | Description |
|------|-------------|
| `dalston/engine_sdk/registry.py` | Sync batch engine client registry |
| `dalston/realtime_sdk/registry.py` | Async RT worker client registry |
| `tests/unit/test_batch_registry.py` | Legacy batch registry tests |

## Files to Simplify

### Writers (remove dual-write, keep unified only)

| File | Changes |
|------|---------|
| `dalston/engine_sdk/runner.py` | Remove `BatchEngineRegistry` import, legacy registration, legacy heartbeat, legacy deregister. Promote `UnifiedRegistryWriter` to sole path. Remove `registry_mode` conditional. |
| `dalston/realtime_sdk/base.py` | Remove `WorkerRegistry` import, legacy registration, legacy heartbeat, legacy deregister. Promote `UnifiedEngineRegistry` to sole path. Remove `registry_mode` conditional. |
| `dalston/realtime_sdk/__init__.py` | Remove lazy exports for `WorkerRegistry`, `WorkerInfo`, `WorkerPresenceRegistry`. |

### Readers (remove legacy fallback, expose unified directly)

| File | Changes |
|------|---------|
| `dalston/orchestrator/registry.py` | Remove `BatchEngineState` class, legacy Redis read logic, `_engine_record_to_state` bridge. Expose `UnifiedEngineRegistry` or thin wrapper returning `EngineRecord`. |
| `dalston/session_router/registry.py` | Remove `WorkerState` class, legacy Redis read logic, `_engine_record_to_worker_state` bridge. Expose `UnifiedEngineRegistry` or thin wrapper returning `EngineRecord`. |

### Consumers (migrate from legacy types to EngineRecord)

| File | Type change |
|------|-------------|
| `dalston/orchestrator/engine_selector.py` | `BatchEngineState` → `EngineRecord` |
| `dalston/orchestrator/main.py` | `BatchEngineRegistry` → `UnifiedEngineRegistry` |
| `dalston/orchestrator/distributed_main.py` | `BatchEngineRegistry` → `UnifiedEngineRegistry` |
| `dalston/orchestrator/handlers.py` | Registry type passed from main |
| `dalston/orchestrator/scheduler.py` | Registry type passed from main |
| `dalston/gateway/api/v1/engines.py` | `BatchEngineRegistry` → `UnifiedEngineRegistry`, response from `EngineRecord` |
| `dalston/session_router/router.py` | `WorkerRegistry`/`WorkerState` → `UnifiedEngineRegistry`/`EngineRecord` |
| `dalston/session_router/allocator.py` | `WorkerState` → `EngineRecord` |
| `dalston/session_router/health.py` | `WorkerRegistry` → `UnifiedEngineRegistry` |

### Config cleanup

| File | Changes |
|------|---------|
| `dalston/config.py` | Remove `engine_registry_mode` and `registry_unified_read_enabled` fields |

### Test updates

| File | Changes |
|------|---------|
| `tests/unit/test_engine_selector.py` | Construct `EngineRecord` instead of `BatchEngineState` |
| `tests/unit/test_session_router.py` | Use `EngineRecord` instead of `WorkerState` |
| `tests/integration/test_capability_driven_dag.py` | Update registry mocks |
| `tests/integration/test_engines_api.py` | Update registry mocks |
| `tests/integration/test_runtime_profile_container.py` | Update registry mocks |

## Tactics

### T1. Delete Legacy Modules

- Delete `engine_sdk/registry.py`, `realtime_sdk/registry.py`, `test_batch_registry.py`.
- Remove lazy exports from `realtime_sdk/__init__.py`.
- Fix all resulting import errors.

Gate: `make test` passes (with expected test failures from deleted test file).

### T2. Simplify Writers

- In `engine_sdk/runner.py`: remove `BatchEngineRegistry` usage, make `UnifiedRegistryWriter` the only registration path. Remove `registry_mode` checks.
- In `realtime_sdk/base.py`: remove `WorkerRegistry` usage, make `UnifiedEngineRegistry` the only registration path. Remove `registry_mode` checks.

Gate: `make test && make lint`

### T3. Simplify Readers

- In `orchestrator/registry.py`: remove `BatchEngineState`, legacy Redis reads, bridge converter. The module should export a thin interface over `UnifiedEngineRegistry` or re-export it directly.
- In `session_router/registry.py`: same treatment for `WorkerState` and legacy reads.

Gate: `make test && make lint`

### T4. Migrate Consumers

- Update `engine_selector.py`, `main.py`, `distributed_main.py`, `handlers.py`, `scheduler.py`, `engines.py`, `router.py`, `allocator.py`, `health.py` to use `EngineRecord` and `UnifiedEngineRegistry`.
- Key field mappings:
  - `BatchEngineState.current_task` → drop (not in `EngineRecord`, derive from status if needed)
  - `WorkerState.active_sessions` → `EngineRecord.active_realtime`
  - `WorkerState.endpoint` → `EngineRecord.endpoint`
  - `BatchEngineState.stream_name` → `EngineRecord.stream_name`

Gate: `make test && make lint`

### T5. Remove Config and Update Tests

- Remove `engine_registry_mode` and `registry_unified_read_enabled` from `config.py`.
- Update all test files that construct legacy types or mock legacy registries.
- Verify no references to `DALSTON_ENGINE_REGISTRY_MODE` or `DALSTON_REGISTRY_UNIFIED_READ_ENABLED` remain outside docs.

Gate: `make test && make lint` — zero failures, zero new warnings.

## Testing Matrix

- Unit: all existing unified registry tests remain green.
- Integration: engine discovery, capability routing, session allocation.
- Negative: verify legacy Redis keys are never read or written.

Recommended gate:

```bash
make test
make lint
```

Release gate:

```bash
pytest -m integration
pytest -m e2e
```

## Rollback

There is no engine_id rollback for this milestone — it is a code deletion.
If issues are found post-deploy, revert the git commit and redeploy the
previous version. The unified registry data in Redis remains valid for both
old and new code.

**Do not start this milestone until the pre-conditions are satisfied.**

## Implementation Status

### Complete

All tactics (T1–T5) executed:

- `dalston/engine_sdk/registry.py` and `dalston/realtime_sdk/registry.py` deleted.
- `dalston/realtime_sdk/__init__.py` lazy exports for `WorkerRegistry`, `WorkerInfo`,
  `WorkerPresenceRegistry` removed.
- `engine_sdk/runner.py` and `realtime_sdk/base.py` use `UnifiedRegistryWriter`
  as sole registration path; dual-write and `registry_mode` conditionals removed.
- `orchestrator/registry.py` is a thin re-export of `UnifiedEngineRegistry`;
  `BatchEngineState` class and legacy Redis reads deleted.
- All consumers (`engine_selector.py`, `main.py`, `distributed_main.py`,
  `handlers.py`, `scheduler.py`, `engines.py`) use `EngineRecord` directly.
- `engine_registry_mode` and `registry_unified_read_enabled` removed from
  `dalston/config.py`.

## Success Criteria

- Zero references to `BatchEngineRegistry` (client), `WorkerRegistry` (client),
  `BatchEngineState`, or `WorkerState` in production code.
- Zero references to legacy Redis key patterns in production code.
- Migration config settings removed.
- Test count does not decrease (legacy tests replaced, not just deleted).
- `make test && make lint` green.

## References

- `docs/plan/milestones/M64-registry-unification-incremental.md` (Phase 3 precursor)
- `docs/plan/milestones/M66-session-router-consolidation-incremental.md` (depends on this for clean registry)
- `docs/plans/pipeline-simplification-plan.md`
