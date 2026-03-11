# M63: Engine Unification (Incremental, Test-Gated)

| | |
|---|---|
| **Goal** | One shared engine instance per engine_id/model that serves both batch and realtime interfaces |
| **Duration** | 2-3 weeks (incremental rollout) |
| **Dependencies** | M36, M40, M43, M44 |
| **Primary Deliverable** | Unified engine_id core + dual interfaces with QoS/admission control |
| **Status** | In Progress (phase 1 complete, phase 2 complete) |

## Outcomes

1. Batch and realtime for the same engine_id/model use one loaded model and one engine_id core.
2. External API contracts stay stable (no output format break for existing clients).
3. Realtime latency is protected under batch load (`rt_reservation`, `batch_max_inflight`).
4. Migration is reversible at every step (compat mode, flags, canary rollout).

## Scope

In scope:

- Runtime-by-engine_id unification of existing engines.
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
2. Extract shared engine_id core with no callers.
3. Migrate batch adapter to core, verify parity.
4. Migrate realtime adapter to core, verify parity.
5. Introduce unified process runner with admission control.
6. Roll out engine_id-by-engine_id with canary + rollback flag.

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

- Introduce engine_id-specific core module (for example, `TranscribeCore`) with:
  - model loading
  - inference execution
  - minimal engine_id-neutral result shape
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

- Single engine_id process exposes:
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

## Phase 1: Faster-Whisper Unification (pilot engine_id)

1. Add `TranscribeCore` module (unused).
2. Switch batch faster-whisper engine to core.
3. Switch RT faster-whisper engine to core.
4. Introduce unified engine_id process with both adapters.
5. Add admission controller + limits.
6. Canary deploy one engine_id pool and compare against legacy.

## Phase 2: Remaining Runtimes (repeatable template)

For each engine_id (`nemo`, `nemo-onnx`, `hf-asr`, `vllm-asr`):

1. Add engine_id core module (unused first).
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

- Unit: engine_id core + adapter contracts.
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

- `DALSTON_UNIFIED_ENGINE_ENABLED` (engine_id-level opt-in).
- `DALSTON_ENGINE_REGISTRY_MODE=dual|legacy|unified`.
- Keep legacy engine_id path deployable until post-canary signoff.
- Rollback rule: if parity or latency SLA fails, flip flag to legacy path and halt rollout.

## Success Criteria

- No API contract regression across batch and RT endpoints.
- Realtime p95 latency under mixed load within agreed SLA.
- Duplicate engine_id/model logic reduced without behavior regressions.
- Unified engine instances visible and routable with stable heartbeats.
- All changes delivered in small, test-gated commits.

## Implementation Status

### Phase 0: Baseline (complete)

- Batch and RT contract test suites for faster-whisper refreshed.
- Parity fixtures and snapshot comparator helpers in place.

### Phase 1: Faster-Whisper Unification (complete)

- **TranscribeCore** (`dalston/engine_sdk/cores/faster_whisper_inference.py`): shared
  engine_id core with `FasterWhisperModelManager`, engine_id-neutral result types,
  `transcribe()` accepting both file paths and numpy arrays.
- **Batch delegation**: `WhisperEngine` (`engines/stt-transcribe/faster-whisper/engine.py`)
  delegates to `TranscribeCore` via optional `core=` injection.
- **RT delegation**: `WhisperStreamingEngine` (`engines/stt-rt/faster-whisper/engine.py`)
  delegates to `TranscribeCore` via optional `core=` injection.
- **Unified runner**: `UnifiedFasterWhisperRunner`
  (`engines/stt-unified/faster-whisper/runner.py`) — single process with one
  `TranscribeCore`, batch adapter in background thread, RT adapter in async loop.
  Gated by `DALSTON_UNIFIED_ENGINE_ENABLED=true`.
- **Admission control**: `AdmissionController`
  (`dalston/engine_sdk/admission.py`) with `DALSTON_RT_RESERVATION`,
  `DALSTON_BATCH_MAX_INFLIGHT`, `DALSTON_TOTAL_CAPACITY`. Thread-safe, wired
  into the unified faster-whisper runner.

### Phase 2: Remaining Runtimes (complete)

- **ParakeetCore** (`dalston/engine_sdk/cores/parakeet_core.py`): shared core
  with `NeMoModelManager`. Batch (`ParakeetEngine`) and RT
  (`ParakeetStreamingEngine`) both delegate via `core=` injection.
- **ParakeetOnnxCore** (`dalston/engine_sdk/cores/parakeet_onnx_core.py`):
  shared core with `NeMoOnnxModelManager`. Batch (`ParakeetOnnxEngine`) and RT
  (`ParakeetOnnxStreamingEngine`) both delegate via `core=` injection.
- **Unified runners implemented**: `UnifiedParakeetRunner`
  (`engines/stt-unified/parakeet/runner.py`) and `UnifiedParakeetOnnxRunner`
  (`engines/stt-unified/parakeet-onnx/runner.py`) — same structure as the
  faster-whisper runner (one core, batch thread + RT async loop, shared
  admission controller).
- **Dockerfiles created**: `engines/stt-unified/parakeet/Dockerfile` and
  `engines/stt-unified/parakeet-onnx/Dockerfile` with GPU/CPU build-arg variants.
- **Promoted to docker-compose**: 8 legacy split services (`stt-batch-transcribe-nemo*`,
  `stt-rt-nemo*`) replaced by 4 unified services (`stt-unified-nemo`,
  `stt-unified-nemo-cpu`, `stt-unified-nemo-onnx-cpu`, `stt-unified-nemo-onnx`).
- **hf-asr, vllm-asr, voxtral**: batch-only engine_ids with no RT counterpart to
  unify — no core extraction needed.

### Phase 3: Registry Cutover (complete — tracked by M64)

- Unified engine registry (M64) handles dual-write, consumer migration, and
  interface-aware routing. Phase 3 cutover (legacy removal) tracked by M69.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-1 sequence and dependencies)
- `docs/reviews/2026-03-09-complexity-review.md` (complexity findings and ordering rationale)
- `docs/plan/milestones/M64-registry-unification-incremental.md`
