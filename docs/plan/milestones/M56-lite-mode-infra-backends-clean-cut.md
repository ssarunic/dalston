# M56: Lite Mode Infra Backends (DB/Queue/Storage, Clean-Cut)

| | |
|---|---|
| **Goal** | Introduce a first-class `lite` runtime mode by abstracting core infra backends (DB, queue, storage) so Dalston can run without Postgres/Redis/MinIO in local-first workflows |
| **Duration** | 7-10 days |
| **Dependencies** | M47 (service-layer SQL separation and injection points), M51/M52 (engine runtime contract + local runner), existing batch gateway/orchestrator flow |
| **Deliverable** | Mode-aware backend interfaces with `distributed` and `lite` implementations, SQLite support, in-memory queue, local filesystem storage, and one validated lite batch transcription path |
| **Status** | Planned |

Dependency clarification:

1. M47 is a prerequisite because it moves SQL out of handlers into services, creating clearer dependency boundaries where backend implementations can be swapped.
2. M47 does not itself provide DB backend abstraction; M56 introduces that abstraction.

## Intended Outcomes

### Functional outcomes

1. Dalston can boot in `lite` mode without external Postgres, Redis, or MinIO services.
2. `lite` mode provides one working batch path end-to-end for first output (`prepare -> transcribe -> merge`).
3. Existing distributed mode behavior remains unchanged by default.
4. Backend selection is explicit and deterministic via mode/config, not implicit runtime heuristics.

### Architecture outcomes

1. DB, queue, and storage each have one explicit interface and two concrete implementations:
   - `distributed`: current Postgres/Redis/S3 behavior
   - `lite`: SQLite/in-memory/localfs behavior
2. Gateway/orchestrator/business services depend on interfaces, not concrete providers.
3. Mode boundaries are explicit and enforceable at startup.

### Operational outcomes

1. A developer can run core batch flow locally without Docker for iterative work.
2. CI can run a lite-mode integration suite as a fast gate.
3. Distributed deployments keep current observability and reliability semantics.

### Clean-start outcomes

1. No compatibility branch that mixes old direct dependencies and new abstraction in the same execution path.
2. No hidden fallback from distributed backends to lite backends.
3. No attempt to emulate full distributed orchestration semantics in lite mode.

### Success criteria

1. `DALSTON_MODE=lite` starts gateway + scheduler path without external infra services.
2. A local audio file can produce transcript JSON in lite mode in automated integration tests.
3. `DALSTON_MODE=distributed` preserves current behavior and tests remain green.
4. Backend contracts and mode semantics are documented and tested.

---

## Strategy To Reach Outcomes

### Strategy 1: Vertical slice over pure abstraction

Do not build abstractions in isolation. For each backend domain (DB, queue, storage), ship:

1. Interface
2. Lite implementation
3. Distributed adapter
4. One end-to-end usage path

This avoids a large abstraction layer with no proven behavior.

### Strategy 2: Mode-first boot contract

Introduce one canonical runtime mode switch:

1. `DALSTON_MODE=distributed` (default)
2. `DALSTON_MODE=lite`

Mode determines backend binding at process startup and remains immutable for process lifetime.

### Strategy 3: Keep lite scope intentionally narrow

M56 focuses on first-run batch path only. Realtime/session router and high-throughput delivery semantics stay distributed-first unless explicitly scoped.

### Strategy 4: Preserve current distributed behavior

Treat distributed mode as compatibility baseline. Any abstraction change must be verified against existing distributed tests and expected runtime behavior.

### Strategy 5: Defer hard problems to explicit follow-on milestones

Do not overload M56 with:

1. full SQLite parity for all historical migrations,
2. full realtime parity,
3. packaging/single-binary distribution,
4. multi-runtime local isolation policy.

These are planned as follow-on milestones with separate acceptance gates.

---

## What We Will Not Do In M56

1. Do not replace Docker/distributed mode as the production target.
2. Do not port every existing Alembic migration to SQLite.
3. Do not implement full distributed reliability semantics (e.g., Redis Streams claim/replay parity) inside lite queue.
4. Do not add auto-start daemon/ghost server behavior yet.
5. Do not solve all runtime dependency isolation concerns (PyTorch/NeMo/ONNX/vLLM) in this milestone.

---

## Tactical Plan

### Phase 0: Freeze Runtime Mode Contract

1. Add explicit mode config:
   - `DALSTON_MODE=distributed|lite`
   - lite defaults for DB/queue/storage paths
2. Define backend interface contracts:
   - `JobStore` / `SettingsStore` surface for gateway/orchestrator needs
   - `TaskQueue` surface for enqueue/consume/ack/metadata
   - `ArtifactStore` surface for upload/download/delete/list
3. Define non-goals and behavior deltas in docs.

Expected files:

- `dalston/config.py`
- `docs/specs/ARCHITECTURE.md`
- `docs/specs/batch/ORCHESTRATOR.md`

### Phase 1: DB Abstraction with SQLite Lite Backend

1. Introduce DB access layer interfaces for the hot path entities used in lite flow.
2. Explicitly freeze and document the lite-path model subset used by `prepare -> transcribe -> merge` before schema work starts.
3. Run a type compatibility audit for that subset and define per-type mitigation:
   - `PG_UUID` / `gen_random_uuid()` -> app-generated UUID strings
   - `JSONB` -> JSON/TEXT representation
   - `ARRAY(...)` -> JSON list or normalized child table
   - `INET` -> validated text
4. Refactor `dalston/db/session.py` to mode-aware lazy initialization (no module-import engine creation side effects).
5. Add SQLite connection support (`sqlite+aiosqlite`) and a lite schema bootstrap path (clean baseline schema), separate from full Postgres migration replay.
6. Keep current SQLAlchemy/Postgres path as distributed adapter.

Expected files:

- `dalston/db/session.py`
- `dalston/db/models.py` (mode-safe types or adapter mapping)
- `dalston/gateway/services/*` and `dalston/orchestrator/*` store call sites
- `tests/integration/test_lite_db_bootstrap.py` (new)
- `docs/reports/M56-lite-model-compat-audit.md` (new)

### Phase 2: Queue Abstraction with In-Memory Lite Backend

1. Introduce queue contract used by scheduler/runner.
2. Implement `RedisStreamsQueue` adapter (distributed).
3. Implement `InMemoryQueue` adapter (lite) using `asyncio.Queue` + explicit ack/visibility semantics suitable for single-process execution.
4. Bind scheduler path to queue interface and validate enqueue/consume lifecycle in lite mode.

Expected files:

- `dalston/common/streams.py` / `dalston/common/streams_sync.py` (adapter split)
- `dalston/orchestrator/scheduler.py`
- `dalston/engine_sdk/runner.py` (if queue API touched for local path)
- `tests/unit/test_lite_queue_contract.py` (new)
- `tests/integration/test_lite_scheduler_path.py` (new)

### Phase 3: Storage Abstraction with Local Filesystem Lite Backend

1. Define artifact storage interface used by ingestion/results paths.
2. Evaluate reuse/adaptation of `LocalFilesystemArtifactStore` from `dalston/engine_sdk/materializer.py` before creating a new implementation.
3. Keep S3 adapter for distributed mode.
4. Add localfs adapter for lite mode under `~/.dalston/artifacts` (or configurable path).
5. Ensure gateway reads/writes transcript and artifact payloads through interface only.

Expected files:

- `dalston/gateway/services/storage.py`
- `dalston/common/s3.py` (adapter extraction boundary)
- `tests/unit/test_lite_storage_provider.py` (new)
- `tests/integration/test_lite_artifact_roundtrip.py` (new)

### Phase 4: Lite End-to-End Batch Slice

1. Freeze lite orchestrator design at phase start:
   - either a dedicated lite entrypoint (`orchestrator_lite`) with in-memory queue poller, or
   - a clearly separated mode branch with equivalent behavior for the scoped path.
2. Implement lite orchestration loop for the scoped batch path without Redis Streams consumer groups/DLQ/reconciler coupling.
3. Wire lite backend providers in gateway/orchestrator startup.
4. Validate one canonical lite DAG path:
   - ingest local audio
   - run prepare/transcribe/merge
   - return transcript JSON
5. Add guardrails: clear startup error if unsupported features are requested in lite mode.

Expected files:

- `dalston/gateway/main.py`
- `dalston/orchestrator/main.py`
- `dalston/orchestrator/lite_main.py` (new, if dedicated entrypoint is chosen)
- `dalston/orchestrator/dag.py` (if lite-stage constraints need explicit gating)
- `tests/integration/test_lite_transcribe_e2e.py` (new)

### Phase 5: Docs, Migration Guidance, and Exit Gate

1. Document mode semantics and capability matrix (`lite` vs `distributed`).
2. Add operator/developer migration path:
   - start in lite
   - move to distributed with same API/CLI contract
3. Update plan index and implementation notes.

Expected files:

- `docs/guides/self-hosted-deployment-tutorial.md`
- `docs/README.md`
- `docs/plan/README.md`
- `docs/reports/M56-lite-mode-foundation.md` (new)

---

## Testing Plan

### Automated tests

1. Unit tests for each backend contract:
   - DB store CRUD/query behavior (lite + distributed adapters)
   - queue enqueue/consume/ack semantics
   - artifact store upload/download/delete semantics
2. Integration tests for lite mode:
   - gateway startup without Postgres/Redis/MinIO
   - scheduler + queue + storage interaction
   - batch transcription happy path to JSON transcript
3. Regression tests for distributed mode:
   - existing integration tests unaffected
   - webhook delivery and orchestration behavior unchanged
4. Mode selection tests:
   - invalid mode -> explicit startup failure
   - required config missing per mode -> explicit startup failure
5. Initialization safety tests:
   - importing DB session module does not trigger distributed connection attempts in lite mode
   - mode-specific session factory initialization occurs only after settings resolution

Suggested command sets:

```bash
pytest tests/unit/test_lite_queue_contract.py \
       tests/unit/test_lite_storage_provider.py \
       tests/integration/test_lite_db_bootstrap.py \
       tests/integration/test_lite_scheduler_path.py \
       tests/integration/test_lite_artifact_roundtrip.py \
       tests/integration/test_lite_transcribe_e2e.py -q
```

```bash
pytest -q
```

### Manual verification

1. Run lite mode without Docker and confirm first transcription output path.
2. Run distributed mode with Docker and confirm no behavior regression.
3. Verify artifacts/transcripts are materialized in localfs for lite and S3 for distributed.

---

## Follow-On Milestones (Out Of Scope For M56)

### M57: Ghost Server + Zero-Config CLI Bootstrap

Goal: `dalston transcribe <file>` auto-checks server, starts local lite server if absent, pulls default model if missing, and returns output with progress UX.

### M58: Lite Pipeline Expansion and Capability Parity

Goal: Expand lite-mode beyond minimal batch path (additional stages/flags), define explicit capability matrix, and add predictable fallback/error behavior for unsupported features.

### M59: Runtime Isolation Profiles (In-Proc / Venv / Container)

Goal: Introduce per-runtime execution profiles to handle conflicting dependency stacks locally while preserving a unified control plane and CLI UX.

### M60: One-Line Distribution and Packaging

Goal: Deliver `pip install`/single-command install UX, optional background service setup, and cross-platform bootstrap validation.

---

## Exit Criteria

1. Lite mode runs without external infra dependencies for the scoped batch path.
2. Backend abstractions are active in production codepaths, not only test doubles.
3. Distributed mode remains behaviorally stable.
4. Mode behavior and migration path are documented.
5. Follow-on milestones are explicitly tracked for remaining scope.
