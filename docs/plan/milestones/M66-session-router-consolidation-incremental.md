# M66: Session Router Consolidation (Behavioral Parity First)

| | |
|---|---|
| **Goal** | Move realtime session coordination into orchestrator without losing existing lifecycle/recovery guarantees |
| **Duration** | 1-2 weeks |
| **Dependencies** | M64, M65 |
| **Primary Deliverable** | `SessionCoordinator` in orchestrator with parity-validated acquire/release/TTL/orphan/offline behavior |
| **Status** | Complete |

## Outcomes

1. Session allocation and recovery logic is centralized in orchestrator.
2. All existing session-router behaviors are preserved:

- atomic reservation + rollback
- release cleanup
- TTL extension
- orphan reconciliation
- offline detection + fanout

3. Capacity accounting and routing remain stable under concurrency.
4. Legacy session-router code is removed only after parity window.

## Scope

In scope:

- Build orchestrator `SessionCoordinator` with full behavior parity.
- Preserve Redis key semantics or migrate with explicit compatibility.
- Integration tests for lifecycle and recovery edge cases.

Out of scope:

- WS protocol adapter changes beyond coordinator call site (covered by M65).
- Registry model redesign (covered by M64).

## Strategy

1. Establish integration safety net against current session-router behavior.
2. Implement coordinator with parity-by-test.
3. Run old and new coordination paths in parallel observation mode.
4. Remove legacy router only after clean parity window.

## Tactics

### T1. Integration Safety Net

- Lifecycle tests:
  - acquire -> keepalive -> release
  - capacity saturation and rejection
  - concurrent allocate race with rollback
  - orphan cleanup after TTL expiry
  - offline worker detection + pub/sub notifications

Gate:

- `make test`
- `make lint`

### T2. Orchestrator Coordinator

- Add `dalston/orchestrator/session_coordinator.py`.
- Port allocation and cleanup behavior with strict parity.
- Integrate with unified registry reads.

Gate:

- Session integration suite passes against coordinator path.
- No leaked sessions/capacity in stress test.

### T3. Gateway Cutover

- Point gateway proxy allocation/release calls to coordinator.
- Keep legacy router monitor active during observation window.

Gate:

- Session success/failure rates parity in canary.
- No missed offline events.

### T4. Remove Legacy Router

- Remove `dalston/session_router/` only after parity window.
- Remove docker service and stale configuration paths.

Gate:

- No regressions after removal in canary + full rollout.

## Incremental Step Plan

## Phase 0: Baseline

1. Add or refresh session lifecycle integration tests.
2. Add parity metrics for session counts and leaked capacity.

## Phase 1: Coordinator Implementation

1. Add coordinator module with acquire/release/extend_ttl.
2. Add background health monitor and orphan reconciliation.
3. Wire unified registry queries.

## Phase 2: Parallel Operation

1. Gateway uses coordinator.
2. Legacy router monitor remains active for comparison.
3. Alert on parity drift before deletion.

## Phase 3: Cleanup

1. Remove session-router package and service wiring.
2. Remove dead code and obsolete settings.

## Testing Matrix

- Unit: allocator semantics and TTL behavior.
- Integration: lifecycle, races, orphan cleanup, offline fanout.
- Resilience: Redis reconnect and process restart scenarios.
- Load: concurrent session churn and release correctness.

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

## Rollback and Safety Controls

- `DALSTON_SESSION_COORDINATOR_ENABLED=true|false`
- `DALSTON_SESSION_PARITY_MONITOR_ENABLED=true|false`
- Rollback: disable coordinator flag and route back to legacy router.

## Success Criteria

- No session leaks, capacity leaks, or missing offline events.
- Realtime allocation latency and failure rate non-regressed.
- Legacy session-router removed after verified parity.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-4)
- `docs/plan/milestones/M64-registry-unification-incremental.md`
- `docs/plan/milestones/M65-realtime-proxy-core-incremental.md`
- `docs/reviews/2026-03-09-complexity-review.md`
