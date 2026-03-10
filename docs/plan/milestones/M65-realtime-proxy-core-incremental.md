# M65: Gateway Realtime Proxy Core (Incremental Extraction)

| | |
|---|---|
| **Goal** | Extract a shared realtime proxy core while preserving Dalston/OpenAI/ElevenLabs WS behavior |
| **Duration** | 1 week |
| **Dependencies** | M63, M64 (recommended) |
| **Primary Deliverable** | `RealtimeProxy` service used by all WS adapters with protocol-parity tests |
| **Status** | Proposed |

## Outcomes

1. WS handler duplication is removed from gateway adapter layers.
2. All protocol variants keep existing message contracts and semantics.
3. Session lifecycle, keepalive, and error handling remain stable.
4. Future protocol additions become adapter-only work.

## Scope

In scope:

- Extract allocation/forwarding/session lifecycle to one proxy core.
- Keep protocol translation in thin adapter handlers.
- Add protocol contract test suite for all three WS interfaces.

Out of scope:

- Session-router orchestration behavior migration (covered by M66).
- Changes to upstream engine/runtime behavior.

## Strategy

1. Freeze behavior with adapter-level contract tests.
2. Extract proxy core with no protocol-level API changes.
3. Migrate one protocol handler at a time.
4. Compare message traces before removing old shared code.

## Tactics

### T1. Protocol Contract Safety Net

- Add tests for:
  - Dalston native WS
  - OpenAI compatible WS
  - ElevenLabs compatible WS
- Assert transcript events, error codes, and connection close behavior.

Gate:

- `make test`
- `make lint`

### T2. Proxy Core Extraction

- Create `dalston/gateway/services/realtime_proxy.py`.
- Move shared allocation/forwarding/release logic.
- Keep handlers untouched in first commit.

Gate:

- New proxy unit tests pass.
- Existing WS tests unchanged.

### T3. Handler-by-Handler Migration

1. Migrate Dalston native handler.
2. Migrate OpenAI compat handler.
3. Migrate ElevenLabs compat handler.

One commit per handler; no multi-handler batch changes.

Gate:

- Contract tests pass after each handler migration.
- End-to-end realtime smoke remains green.

### T4. Cleanup

- Remove obsolete shared helper paths after parity confirmation.
- Keep translation-only logic in adapters.

Gate:

- No coverage regressions in WS tests.
- No protocol trace diffs beyond expected non-functional fields.

## Incremental Step Plan

## Phase 0: Baseline

1. Capture current WS trace fixtures per protocol.
2. Add regression tests for close/error semantics and keepalive.

## Phase 1: Core

1. Add `RealtimeProxy` with allocation/stream/release APIs.
2. Add unit tests with mocked upstream worker channels.

## Phase 2: Adapter Migration

1. Dalston handler -> proxy.
2. OpenAI handler -> proxy.
3. ElevenLabs handler -> proxy.

## Phase 3: Stabilize and Remove Duplication

1. Remove superseded shared paths.
2. Keep adapter boundaries strict (translation only).

## Testing Matrix

- Unit: `RealtimeProxy` lifecycle and error handling.
- Contract: per-protocol WS message/close semantics.
- Integration: allocate -> stream -> finalize -> release flow.
- Resilience: disconnect/reconnect and worker timeout paths.

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

- `DALSTON_REALTIME_PROXY_CORE_ENABLED=true|false`
- Rollback: disable proxy-core flag and route handlers to legacy logic.

## Success Criteria

- Handler LOC and duplication reduced without protocol regressions.
- Native and compat WS clients behave identically to pre-refactor.
- Operational metrics (session failures/timeouts) do not regress.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-3)
- `docs/plan/milestones/M63-engine-unification-incremental.md`
- `docs/reviews/2026-03-09-complexity-review.md`
