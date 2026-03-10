# M63: Engine Unification (Incremental, Test-Gated)

| | |
|---|---|
| **Goal** | One shared engine instance per runtime/model that serves both batch and realtime interfaces |
| **Duration** | 2-3 weeks (incremental rollout) |
| **Dependencies** | M36, M40, M43, M44 |
| **Primary Deliverable** | Unified runtime core + dual interfaces with QoS/admission control |
| **Status** | Proposed |

## Outcomes

1. Batch and realtime for the same runtime/model use one loaded model and one runtime core.
2. External API contracts stay stable (no output format break for existing clients).
3. Realtime latency is protected under batch load (`rt_reservation`, `batch_max_inflight`).
4. Migration is reversible at every step (compat mode, flags, canary rollout).

## Scope

In scope:

- Runtime-by-runtime unification of existing engines.
- Registry compatibility needed to represent dual-interface engines.
- QoS/admission controls for mixed batch+realtime load.
- Characterization and parity testing before each refactor step.

Out of scope for this milestone:

- DAG restructuring and merge elimination.
- per_channel redesign.
- Declarative `engine.yaml` pipeline wiring and new stages.
- Align removal (align remains capability-gated fallback).

## Strategy

1. Add test harnesses first and lock behavior.
2. Extract shared runtime core with no callers.
3. Migrate batch adapter to core, verify parity.
4. Migrate realtime adapter to core, verify parity.
5. Introduce unified process runner with admission control.
6. Roll out runtime-by-runtime with canary + rollback flag.

Each change is one logical commit. Stop on first regression.

## Tactics

### T1. Characterization Harness (before refactor)

- Batch contract tests: output shape, timestamps, language, segment determinism.
- RT contract tests: session lifecycle, chunk semantics, partial/final events.
- Snapshot/parity fixtures for representative audio and languages.
- Failure-path tests: model load failure, partial stream disconnect, retries.

Gate:

- `make test`
- `make lint`

### T2. Shared Runtime Core Extraction

- Introduce runtime-specific core module (for example, `TranscribeCore`) with:
  - model loading
  - inference execution
  - minimal runtime-neutral result shape
- No production call sites in first commit (dark launch by construction).

Gate:

- Existing tests pass unchanged.
- New core unit tests pass.

### T3. Batch Adapter Delegation

- Batch engine delegates inference/model lifecycle to shared core.
- Batch adapter keeps current I/O contract and output mapping.
- No RT changes in the same commit.

Gate:

- Batch contract tests remain identical.
- Export regression tests (`SRT`/`VTT`/`TXT`) pass.

### T4. Realtime Adapter Delegation

- RT engine delegates inference/model lifecycle to shared core.
- RT adapter keeps protocol behavior unchanged (Dalston/OpenAI/ElevenLabs).
- No registry schema changes in the same commit.

Gate:

- RT protocol contract tests pass.
- Session lifecycle tests pass.

### T5. Unified Process + Admission Control

- Single runtime process exposes:
  - queue adapter (batch)
  - websocket adapter (realtime)
- Add admission controller with:
  - `DALSTON_RT_RESERVATION`
  - `DALSTON_BATCH_MAX_INFLIGHT`
  - bounded inflight counters and rejection paths

Gate:

- Mixed-load integration test proves RT is not starved by batch.
- Backpressure behavior tested (`NACK`/retry for batch, bounded RT reject policy).

### T6. Registry Compatibility and Cutover

- Dual-write registry entries during migration.
- Consumers dual-read (new first, legacy fallback).
- Cut legacy writes only after parity telemetry is clean.

Gate:

- Registration/heartbeat parity dashboards show no missing workers.
- No routing regressions in canary.

## Incremental Step Plan

## Phase 0: Baseline and Test Safety Net

1. Add/refresh batch and RT contract suites for faster-whisper.
2. Add parity fixtures and snapshot comparator helper.
3. Add mixed-load benchmark test harness (offline/integration mark).

## Phase 1: Faster-Whisper Unification (pilot runtime)

1. Add `TranscribeCore` module (unused).
2. Switch batch faster-whisper engine to core.
3. Switch RT faster-whisper engine to core.
4. Introduce unified runtime process with both adapters.
5. Add admission controller + limits.
6. Canary deploy one runtime pool and compare against legacy.

## Phase 2: Remaining Runtimes (repeatable template)

For each runtime (`nemo`, `nemo-onnx`, `hf-asr`, `vllm-asr`):

1. Add runtime core module (unused first).
2. Migrate batch adapter.
3. Migrate RT adapter.
4. Enable unified process with admission controls.
5. Canary and promote.

## Phase 3: Registry Cutover and Cleanup

1. Switch orchestrator/session allocation reads to unified records.
2. Keep legacy compatibility for one release cycle.
3. Remove legacy writes after clean telemetry window.

## Testing Matrix

Required on every step:

- Unit: runtime core + adapter contracts.
- Integration: job execution, RT sessions, mixed-load admission behavior.
- Compatibility: output parity for transcript/export formats.
- Resilience: worker restart, heartbeat timeout, reconnect/retry paths.

Recommended command gate per commit:

```bash
make test
make lint
```

For release candidates:

```bash
pytest -m integration
pytest -m e2e
```

## Rollback and Safety Controls

- `DALSTON_UNIFIED_ENGINE_ENABLED` (runtime-level opt-in).
- `DALSTON_ENGINE_REGISTRY_MODE=dual|legacy|unified`.
- Keep legacy runtime path deployable until post-canary signoff.
- Rollback rule: if parity or latency SLA fails, flip flag to legacy path and halt rollout.

## Success Criteria

- No API contract regression across batch and RT endpoints.
- Realtime p95 latency under mixed load within agreed SLA.
- Duplicate runtime/model logic reduced without behavior regressions.
- Unified engine instances visible and routable with stable heartbeats.
- All changes delivered in small, test-gated commits.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-1 sequence and dependencies)
- `docs/reviews/2026-03-09-complexity-review.md` (complexity findings and ordering rationale)
