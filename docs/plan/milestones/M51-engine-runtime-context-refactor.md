# M51: Stateless Engine Contract and Artifact Materialization

| | |
|---|---|
| **Goal** | Remove hidden global coupling between engines by making engines stateless and URI-free, with artifact persistence/materialization handled by runtime infrastructure |
| **Duration** | 12-16 days |
| **Dependencies** | M43 (RT model loading), M48 (RT routing alignment), M49 (terminology unification) |
| **Deliverable** | Clean-break engine SDK contract (`process(input, ctx)`), runner-side artifact materializer, artifact-reference internal schemas, local runner, RT side-effect adapters |
| **Status** | Core Complete — engine migration sweep and DX cleanup carried forward to M52 |

## User Story

> *"As the operator/developer, I want engines to be pure compute units with explicit typed inputs, so engine behavior does not depend on storage URI conventions or shared global infra assumptions."*

## Execution Mode

This milestone is intentionally a **clean break**:

1. No backwards-compatibility shims.
2. No mixed old/new engine interface support.
3. One coordinated migration branch, merged only after full verification.

---

## Motivation

### Current structural issue

Even with context adapters, engines can still be globally coupled if they:

1. Construct or parse storage URIs.
2. Assume bucket/prefix conventions.
3. Pull/push artifacts directly in engine business logic.

That creates implicit dependencies between engines through storage layout rather than explicit contracts.

### Why this matters

1. Harder to reason about stage boundaries.
2. Harder to test engine logic in isolation.
3. Harder to change storage backend or path policy.
4. Increased risk of cross-stage breakage from path convention drift.

### Existing baseline to leverage

1. `TaskOutput.artifacts: dict[str, Path]` already exists in runner flow and is uploaded by the current runner.
2. This milestone formalizes and extends that pattern into a typed artifact manifest with orchestration-time dependency resolution.

---

## Architectural Decision

### Boundary model

1. **Engines are stateless compute units**:
   - Consume typed stage input + local file handles.
   - Produce typed stage output + produced local files.
   - No direct storage clients, no URI building/parsing.

2. **Orchestrator is control plane**:
   - Builds DAG and dependency graph.
   - Decides what artifacts a task needs.
   - Stores task metadata and artifact references.

3. **Engine runner is data plane**:
   - Materializes required artifacts (download/resolve to local paths).
   - Invokes engine.
   - Persists produced artifacts.
   - Publishes task lifecycle events.

### Important clarification

Persistence/reads are handled **outside engine logic**, but should remain in runner/materializer near execution, not inside orchestrator process, to avoid making orchestrator a data-transfer bottleneck.

---

## Target Outcomes

### Functional outcomes

1. Internal task inputs are URI-free for engine logic (artifact references + local materialization).
2. Engines never import storage I/O helpers in runtime logic.
3. Batch runner performs all artifact fetch/persist operations.
4. RT session/runtime side effects are adapter-driven and swappable.
5. External APIs/protocols remain unchanged.

### Engineering outcomes

1. Strong, explicit stage contracts (typed data + declared artifacts).
2. Easy local execution of any engine without Redis/S3.
3. Storage path policy centralized in one place.
4. Reduced accidental coupling between stages.
5. Stage-specific input/output models replace generic dict-style payloads.

### Success metrics

1. All batch engines compile and run under `process(input, ctx)` contract.
2. Zero direct URI construction in batch engine `process` paths.
3. Local runner executes at least `prepare`, `transcribe`, and `merge` engines without Redis/S3.
4. Existing task lifecycle event semantics remain unchanged.
5. RT WebSocket protocol behavior unchanged.
6. Core stages have dedicated payload contracts (`Prepare*`, `Transcribe*`, `Align*`, `Diarize*`, `PII*`, `Redact*`, `Merge*`).

---

## Non-Goals

1. Rewriting inference/model-manager internals.
2. Changing external REST/WebSocket schemas.
3. Migrating orchestrator into data-transfer responsibilities.
4. Supporting old engine signatures after cutover.

---

## Contract Design

### Batch engine contract (new)

```python
class Engine(Generic[I, O], ABC):
    @abstractmethod
    def process(
        self,
        input: EngineInput[I],
        ctx: BatchTaskContext,
    ) -> EngineOutput[O]: ...
```

Where:

1. `EngineInput` includes:
   - Task/job/stage metadata.
   - Typed previous stage outputs.
   - `materialized_artifacts`: local filesystem references prepared by runner.
   - Stage config.

2. `EngineOutput` includes:
   - Typed output data payload.
   - `produced_artifacts`: local files to persist (no URI fields).
   - Optional artifact descriptors (media type, role, channel, etc.).

3. `BatchTaskContext` exposes:
   - logging/tracing context
   - runtime metadata access
   - deterministic runtime helpers (no storage side effects)

4. Typing note:
   - Keep subtype contracts explicit (`Engine[PrepareInputPayload, PrepareOutputPayload]` etc.) and enforce with mypy + `@override` in concrete engines.

### Stage-specific payload contracts (required)

Use stage contracts, not runtime/vendor contracts.

1. Required core models:
   - `PrepareInputPayload` / `PrepareOutputPayload`
   - `TranscribeInputPayload` / `TranscribeOutputPayload`
   - `AlignInputPayload` / `AlignOutputPayload`
   - `DiarizeInputPayload` / `DiarizeOutputPayload`
   - `PIIDetectInputPayload` / `PIIDetectOutputPayload`
   - `AudioRedactInputPayload` / `AudioRedactOutputPayload`
   - `MergeInputPayload` / `MergeOutputPayload`
2. Per-runtime extensions are allowed only for strictly runtime-specific fields and must embed within the stage payload boundary.
3. `EngineInput/EngineOutput` remain common envelopes; stage payloads carry domain semantics.

### Artifact model (new internal shape)

Introduce artifact references (IDs) as internal dependency links between tasks:

1. `artifact_id` (stable within job)
2. `kind` (audio, transcript, redacted_audio, task_output, etc.)
3. `storage_locator` (owned by runtime infra, opaque to engine)
4. `checksum`, `size`, `media_type`

Engines see local paths only after materialization.

---

## Phase 0 Contract Decisions (Hard Gate)

No Phase 1+ code starts until these decisions are written and approved in-repo.

### 1) URI-free media shapes (clean break)

1. Replace `AudioMedia.uri` with `AudioMedia.artifact_id`.
2. Keep media metadata (`format`, `duration`, `sample_rate`, `channels`, `bit_depth`) unchanged.
3. `PrepareOutputPayload.channel_files[]` carries `AudioMedia` with `artifact_id`, never URI.
4. Rename URI fields in stage payloads to artifact reference fields:
   - `AudioRedactOutput.redacted_audio_uri` -> `redacted_audio_artifact_id`
   - `PIIMetadata.redacted_audio_uri` -> `redacted_audio_artifact_id`
5. No compatibility aliases are added.

### 2) Artifact publication model (engine side)

1. Primary pattern is return-only publication via `EngineOutput.produced_artifacts`.
2. `BatchTaskContext` does not perform publish/persist side effects.
3. Optional helper methods may exist, but must only construct descriptors and remain side-effect-free.

Reference shape:

```python
class ProducedArtifact(BaseModel):
    logical_name: str
    local_path: Path
    kind: str
    channel: int | None = None
    role: str | None = None
    media_type: str | None = None
```

### 3) DAG artifact-typed dependency API

Define explicit input-slot bindings so orchestration does not depend on naming conventions:

```python
class ArtifactSelector(BaseModel):
    producer_stage: str
    kind: str
    channel: int | None = None
    role: str | None = None
    required: bool = True

class InputBinding(BaseModel):
    slot: str
    selector: ArtifactSelector
```

1. DAG nodes declare `input_bindings: list[InputBinding]`.
2. Scheduler resolves bindings to concrete `artifact_id`s before queueing.
3. Runner materializes artifacts from resolved IDs only.

### 4) Redis artifact reference schema

Define storage shape before implementation:

1. Job-scoped artifact index:
   - key: `dalston:job:{job_id}:artifacts`
   - value: `artifact_id -> artifact metadata JSON` (storage locator, kind, channel, role, producer task/stage, checksum, size, media_type)
2. Task metadata extensions on `dalston:task:{task_id}`:
   - `input_bindings_json`
   - `resolved_artifact_ids_json`
   - `produced_artifact_ids_json`
3. Reconciliation/replay paths read the same schema.

### 5) SessionStorage ABC (realtime)

Define RT side-effect boundary now, before refactor:

```python
class SessionStorage(ABC):
    async def start(self, session_id: str, config: SessionConfig) -> None: ...
    async def append_audio(self, chunk: bytes) -> None: ...
    async def save_transcript(self, transcript_data: dict[str, Any]) -> None: ...
    async def finalize(self) -> SessionStorageResult: ...
    async def abort(self) -> None: ...
```

1. `SessionStorageResult` returns artifact references, not raw URIs.
2. Session handler keeps protocol behavior unchanged and delegates persistence lifecycle.

---

## Implementation Plan

### Phase 0: Spec and invariants (1-1.5 days)

**Actions**

1. Freeze `AudioMedia`, stage payload, and artifact reference shapes (URI-free).
2. Freeze `EngineInput`, `EngineOutput`, `BatchTaskContext`, and `SessionStorage` interfaces.
3. Freeze DAG input-binding API and Redis artifact metadata schema.
4. Define invariants:
   - engines are URI-free
   - engines are storage-client-free
   - runner owns artifact transport
5. Define CI enforcement implementation (AST + grep checks, exact blocked patterns).
6. Publish migration notes for engine authors.

**Deliverables**

- Contract types and docstrings committed.
- Approved schema/examples for artifact binding resolution and Redis metadata.
- This milestone as source of truth.

**Go/No-Go Gate**

1. If any of the five Phase 0 contract decisions are unresolved, do not start implementation phases.

---

### Phase 1: Runner materializer and contract cutover (2.5-3.5 days)

**Actions**

1. Add new modules:
   - `dalston/engine_sdk/context.py`
   - `dalston/engine_sdk/materializer.py`
   - `dalston/common/artifacts.py` (or equivalent internal models module)
   - `dalston/engine_sdk/contracts.py` (stage payload contracts)
2. Change `Engine` base to new required `process(input, ctx)` signature.
3. Refactor `EngineRunner`:
   - resolve artifact refs
   - materialize local files
   - invoke engine
   - persist produced files via artifact store
   - write canonical task output envelope
4. Reuse existing `TaskOutput.artifacts` upload path as migration bridge, then replace with typed artifact manifest.
5. Remove compatibility paths for old signature.

**Files expected**

- `dalston/engine_sdk/base.py`
- `dalston/engine_sdk/runner.py`
- `dalston/engine_sdk/context.py` (new)
- `dalston/engine_sdk/materializer.py` (new)
- `dalston/common/artifacts.py` (new)
- `dalston/engine_sdk/contracts.py` (new)

**Acceptance criteria**

1. Old engine signature no longer exists.
2. Runner passes existing task lifecycle tests.
3. Artifact materialization/persistence works via adapters only.
4. Task lifecycle events keep existing semantics/order.

---

### Phase 2: Orchestrator schema and scheduling alignment (4-6 days)

**Actions**

1. Update internal task input schema from URI-centric to artifact-reference-centric.
2. Add job artifact index writes/reads in Redis metadata flow.
3. Update scheduler task input writer to emit:
   - `input_bindings`
   - resolved `artifact_id` references per input slot
4. Update DAG dependency resolution logic to select artifacts by `kind` + optional `channel` + optional `role` (not by stage-key naming conventions).
5. Compile/validate stage-specific payload models before queueing tasks.
6. Update reconciliation/replay code paths to use the same artifact reference schema.
7. Keep external API responses stable.

**Files expected**

- `dalston/common/pipeline_types.py`
- `dalston/orchestrator/scheduler.py`
- `dalston/orchestrator/dag.py`
- `dalston/orchestrator/handlers.py`
- `dalston/orchestrator/reconciler.py` (as needed for replay/recovery alignment)

**Acceptance criteria**

1. Orchestrator no longer passes direct artifact URIs as engine input contract.
2. Stage dependencies resolve via artifact references.
3. Stage payload validation is enforced before dispatch.
4. Orchestrator routing tests prove per-channel and role-specific artifact binding works.
5. E2E pipeline still completes with same user-visible outputs.

---

### Phase 3: Batch engine migration sweep (2-3 days)

**Actions**

1. Mechanical migration of all batch engines to new signature.
2. Migrate engines from generic payload access to stage-specific payload models.
3. Semantic migration of side-effect-heavy engines:
   - `engines/stt-prepare/audio-prepare/engine.py`
   - `engines/stt-redact/audio-redactor/engine.py`
   - `engines/stt-merge/final-merger/engine.py`
4. Remove URI construction and direct SDK I/O calls from engine runtime logic.
5. Add regression tests for produced artifact declarations and downstream compatibility.
6. Update payloads/consumers impacted by prepare output shape migration (`channel_files[].artifact_id`).

**Acceptance criteria**

1. No batch engine builds/parses storage URIs in `process`.
2. No batch engine imports storage transport helpers in runtime path.
3. Engines consume/produce stage payload models (no raw dict contracts in runtime path).
4. Existing stage outputs remain compatible for downstream consumers.
5. Prepare-driven per-channel fan-out remains correct in orchestrator dispatch.

---

### Phase 4: Local runner first-class support (1-1.5 days)

**Actions**

1. Add `dalston/engine_sdk/local_runner.py`.
2. Implement local artifact store/materializer adapters (filesystem-backed).
3. Support run command:
   - engine class/module
   - local input config
   - local media/artifact directory
   - output path
4. Add docs and examples.

**Acceptance criteria**

1. Local runner works with no Redis/S3.
2. Produces canonical output envelope.
3. Can run at least one side-effect-heavy migrated engine.

---

### Phase 5: Realtime side-effect adapter alignment (2-2.5 days)

**Actions**

1. Add `dalston/realtime_sdk/context.py`.
2. Implement `SessionStorage` ABC + concrete S3 adapter.
3. Refactor `SessionHandler` storage path to `SessionStorage` abstraction.
4. Refactor realtime worker registry interactions behind `WorkerPresenceRegistry`.
5. Keep `transcribe(audio, ...)` signature and protocol untouched.
6. Add failure-mode tests: storage unavailable, partial finalize failure, cleanup behavior.

**Files expected**

- `dalston/realtime_sdk/context.py` (new)
- `dalston/realtime_sdk/session.py`
- `dalston/realtime_sdk/base.py`
- `dalston/realtime_sdk/registry.py`

**Acceptance criteria**

1. RT protocol behavior unchanged.
2. Session storage and presence registry are swappable adapters.
3. Direct side-effect concrete coupling reduced/removed in runtime layer.
4. Session handler no longer creates S3 client directly.

---

### Phase 6: Hardening and enforcement (1-1.5 days)

**Actions**

1. Add CI checks with explicit implementations:
   - AST check: no `dalston.engine_sdk.io`, `boto3`, or `redis` imports under `engines/**` runtime modules
   - grep check: no `s3://` string literals under `engines/**`
   - AST check: no calls to URI helpers (`build_*_uri`, `parse_s3_uri`) under engine runtime modules
   - check for old batch engine signature and raw dict payload access in migrated stages
2. Add contract tests:
   - adapter parity tests (prod/local)
   - artifact manifest golden tests
   - orchestrator artifact routing golden tests (channel/role selectors)
3. Publish migration guide and engine authoring guide.

**Acceptance criteria**

1. CI blocks regressions to implicit coupling patterns.
2. Docs enable adding new engines with stateless contract from day one.

---

## Phase Gates (Execution Control)

1. Gate A (after Phase 0): contract/schema/interface freeze approved in-repo.
2. Gate B (after Phase 2): orchestrator resolves artifact-typed bindings correctly in unit + golden tests.
3. Gate C (after Phase 3): migrated engines have zero runtime storage imports/URI logic and fan-out/fan-in behavior verified.
4. Gate D (after Phase 5): realtime protocol unchanged and `SessionStorage` adapter passes failure-path tests.
5. Gate E (after Phase 6): CI checks active and blocking on forbidden patterns.

---

## Detailed Work Breakdown (Specific Actions)

1. Freeze URI-free `AudioMedia` and stage payload shapes (`artifact_id` fields).
2. Freeze `EngineInput`, `EngineOutput`, `BatchTaskContext`, and `SessionStorage` interfaces.
3. Define and freeze `ArtifactSelector` + `InputBinding` schema.
4. Define and freeze Redis artifact index and task metadata fields.
5. Introduce artifact reference models and typed manifest schema.
6. Introduce stage-specific payload contracts for all core stages.
7. Replace batch engine base contract with `process(input, ctx)`.
8. Implement runner materializer abstraction and production adapter.
9. Refactor runner lifecycle around materialize -> process -> persist flow.
10. Rework runner output persistence from `dict[str, Path]` bridge to typed artifact manifest.
11. Update orchestrator task input writing to artifact bindings + resolved artifact IDs.
12. Update DAG dependency handoff to typed selectors (`kind` + `channel` + `role`).
13. Update reconciliation/replay code to consume the same artifact schema.
14. Migrate all batch engines to new signature + stage payload contracts.
15. Refactor prepare engine to emit artifact declarations (no URI writes).
16. Refactor audio redactor and merger to consume artifact references.
17. Update downstream consumers for prepare output cascade (dispatch + merge + gateway response assembly paths).
18. Add regression tests for artifact declarations, routing, and merged outputs.
19. Implement local filesystem artifact store + local runner CLI.
20. Add local runner tests for prepare/transcribe/merge.
21. Add realtime context interfaces and `SessionStorage` adapter implementations.
22. Refactor RT session storage and presence handling behind adapters.
23. Add CI static checks (AST + grep) for forbidden runtime patterns.
24. Remove dead code, publish migration docs, and lock gates in CI.

---

## Risk Assessment and Mitigation

| Risk | Severity | Mitigation |
|------|----------|------------|
| Prepare output shape cascade (`channel_files[].uri` -> `artifact_id`) breaks routing/merge/gateway | Critical | Dedicated cascade test suite across orchestrator routing, merge input assembly, and gateway response paths |
| Internal schema migration breaks orchestrator-engine handoff | Critical | Contract tests around scheduler writer, Redis artifact schema, and runner parser |
| DAG artifact selector resolution mismatch (`kind`/`channel`/`role`) | High | Golden tests for selector resolution matrix + strict schema validation before dispatch |
| Broad compile/test breakage from clean cutover | High | Mechanical migration sweep first, strict CI, no partial merges |
| Event semantics drift | High | Golden/snapshot tests for task.started/completed/failed payloads/order |
| Side-effect-heavy engine regressions | High | Focused regression tests for prepare/redact/merge and per-channel fan-out paths |
| CI misses lazy imports or URI helper calls | Medium | AST-based checks on import/call nodes plus grep for URI literals in `engines/**` |
| RT lifecycle regression | Medium | Keep protocol fixed, isolate adapter refactor to side-effect plumbing, add failure-path tests |

---

## Rollout and Rollback

### Rollout

1. Execute on a dedicated clean-break branch.
2. Order of merge readiness:
   - contract + materializer
   - orchestrator schema alignment
   - engine migration
   - local runner
   - RT adapter alignment
3. Merge only when full automated + manual gates pass.

### Rollback

1. Full revert of milestone PR(s) if critical regressions appear.
2. No mixed-mode fallback after merge.

---

## Verification Plan

### Automated

1. `pytest` full suite.
2. Focus suites:
   - runner lifecycle/event tests
   - scheduler + DAG artifact binding resolution tests
   - scheduler input writer tests
   - orchestrator replay/reconciler artifact schema tests
   - artifact materializer tests
   - migrated engine tests
   - gateway response assembly tests from artifact references
   - RT session/registry tests
   - CI static-rule tests (forbidden imports/URI helpers/literals)

### Manual

1. Run one batch E2E job with migrated engines.
2. Validate prepare -> transcribe/align fan-out on multi-channel audio.
3. Run local runner on prepare, transcribe, and merge flows.
4. Run one RT session and verify protocol + persistence behavior.
5. Confirm no engine `process` path contains URI building or storage imports.

---

## Exit Criteria

1. Engines are stateless and URI-free in runtime logic.
2. Artifact transport/persistence is owned by runner materializer.
3. Orchestrator schedules via artifact references, not implicit URI coupling.
4. URI fields are removed from internal stage payloads where replaced by artifact IDs.
5. Local runner provides no-infra inner-loop workflow.
6. RT side effects are adapter-driven and testable.
7. Stage-specific payload contracts are enforced for core stages.
8. CI gates block forbidden coupling patterns.
9. All tests pass with no contract regressions.
