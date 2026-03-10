# M64: Registry Unification (Incremental, Compat-Mode)

| | |
|---|---|
| **Goal** | Replace split batch/RT registry surfaces with one unified engine registry without routing regressions |
| **Duration** | 1-2 weeks |
| **Dependencies** | M63 |
| **Primary Deliverable** | Unified `EngineRegistry` with dual-read/dual-write migration and clean cutover |
| **Status** | Proposed |

## Outcomes

1. A single registry model represents batch-only, RT-only, and dual-interface engines.
2. Existing orchestrator and gateway flows remain behaviorally compatible during migration.
3. Rollout is reversible by configuration until final cutover.
4. Routing and capacity decisions remain accurate through cutover.

## Scope

In scope:

- Unified schema and API for engine records.
- Dual-write from producers and dual-read in consumers during transition.
- Compatibility metrics and parity checks before disabling legacy paths.
- Cleanup of legacy writes/reads only after one stable release window.

Out of scope:

- Session-router functional merge into orchestrator (covered by M66).
- Pipeline shape changes (covered by M68).

## Strategy

1. Define and test unified registry contracts first.
2. Introduce registry in shadow mode (writes only).
3. Switch consumers to read unified registry with fallback.
4. Remove fallback after parity window.

Each step is a small commit with explicit pass/fail gates.

## Tactics

### T1. Contract Safety Net

- Characterization tests for:
  - batch registration/heartbeat/deregistration
  - RT registration/heartbeat/state updates
  - capacity filtering and selection queries

Gate:

- `make test`
- `make lint`

### T2. Add Unified Registry Surface

- Introduce `EngineRecord`/`EngineRegistry` abstractions.
- No production readers in first commit.

Gate:

- New registry unit tests pass.
- Existing tests unchanged.

### T3. Producer Dual-Write

- Unified engine runners write both:
  - new unified keys
  - legacy keys
- Add counters for write parity and failures.

Gate:

- No loss in visible workers across old/new queries.
- Heartbeat freshness parity within expected skew.

### T4. Consumer Dual-Read

- Orchestrator/gateway/session allocation read from unified registry first.
- Keep legacy fallback with feature flag.

Gate:

- Routing parity tests pass.
- No increase in "no available workers" under equivalent load.

### T5. Cutover and Cleanup

- Disable legacy writes, keep read fallback temporarily.
- Remove read fallback after stable release window.

Gate:

- Zero parity alerts during observation window.
- Canary and full rollout meet routing SLIs.

## Incremental Step Plan

## Phase 0: Baseline

1. Add/update tests for `BatchEngineRegistry` and RT registry behavior.
2. Add parity comparison helper for record normalization.

## Phase 1: Unified Registry in Shadow Mode

1. Add `EngineRecord` schema and `EngineRegistry` implementation.
2. Add migration-safe key naming and TTL policy.
3. Enable dual-write in engine runners.

## Phase 2: Consumer Migration

1. Switch orchestrator engine discovery to unified-first reads.
2. Switch RT allocation paths to unified-first reads.
3. Keep fallback mode enabled.

## Phase 3: Cutover

1. Disable legacy writes.
2. Observe parity and routing metrics for one release window.
3. Remove fallback reads and legacy registry code.

## Testing Matrix

- Unit: registry read/write paths and record validation.
- Integration: registration, heartbeat timeout, query filters.
- Compatibility: dual-read parity tests on normalized record sets.
- Failure: Redis reconnect and partial write failures.

Recommended gate:

```bash
make test
make lint
```

Release gate:

```bash
pytest -m integration
```

## Rollback and Safety Controls

- `DALSTON_ENGINE_REGISTRY_MODE=legacy|dual|unified`
- `DALSTON_REGISTRY_UNIFIED_READ_ENABLED=true|false`
- Rollback: switch to `legacy` mode and keep dual writes active until issue is fixed.

## Success Criteria

- Unified registry serves all routing use cases.
- No worker visibility regression during or after cutover.
- Legacy registry paths removed only after validated parity window.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-2)
- `docs/plan/milestones/M63-engine-unification-incremental.md`
- `docs/reviews/2026-03-09-complexity-review.md`
