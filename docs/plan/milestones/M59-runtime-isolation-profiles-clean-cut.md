# M59: Runtime Isolation Profiles (In-Proc / Venv / Container, Clean-Cut)

| | |
|---|---|
| **Goal** | Support heterogeneous/incompatible runtime stacks locally and in distributed deployments via explicit execution profiles (`inproc`, `venv`, `container`) |
| **Duration** | 8-12 days |
| **Dependencies** | M58 (expanded lite capability model), M36/M40 (runtime model lifecycle), existing engine runner contracts |
| **Deliverable** | Runtime execution-profile contract, profile-aware dispatch layer, venv/container adapters, and observability/guardrails for profile-based execution |
| **Status** | Planned |

Dependency clarification:

1. M58 defines what lite can do; M59 defines how runtimes execute safely when dependencies conflict.
2. M36/M40 remain the model-control plane; M59 changes execution isolation, not model identity contracts.
3. M52 local runner is the baseline in-proc execution primitive; M59 should reuse/extract from it rather than duplicating dispatch logic.
4. M59 should start only after M56 lite orchestrator path (`dalston/orchestrator/lite_main.py`) is stable; `inproc`/`venv` execution plugs into lite path, not distributed queue path.

## Intended Outcomes

### Functional outcomes

1. Each runtime can declare its execution profile (`inproc`, `venv`, or `container`).
2. Scheduler/runner dispatches tasks through a profile-aware executor layer.
3. Incompatible stacks (for example, NeMo vs other PyTorch/ONNX stacks) can coexist without process-level conflicts.
4. Failure handling and task output contracts remain consistent across profiles.
5. Existing containerized long-running worker model remains supported under the new abstraction (no per-task one-shot container requirement in M59).
6. Profile routing spans two execution domains with one policy model:
   - lite path: `inproc`/`venv` execution in/under `lite_main.py`
   - distributed path: `container` execution via existing Redis stream + engine container workers

### Architecture outcomes

1. One control plane with multiple execution backends.
2. No runtime-specific branching spread across orchestration business logic.
3. Profile policy is declarative (catalog/config), not hardcoded by runtime name in multiple places.

### Operational outcomes

1. Operators can inspect active profile per runtime in status/health output.
2. Profile-specific failures are observable and actionable.
3. Runtime startup/preparation overhead is bounded and cacheable (venv reuse, image reuse).

### Clean-start outcomes

1. No implicit mixing of incompatible dependencies in one Python process for isolated runtimes.
2. No hidden fallback from isolated profile to in-process execution.
3. No duplicated pipeline logic per profile.

### Success criteria

1. At least one runtime per profile executes end-to-end through same task contract.
2. Profile routing is deterministic and test-covered.
3. Isolation prevents known dependency conflicts in representative scenarios.
4. Existing inproc runtimes continue to work unchanged where isolation is not required.

---

## Strategy To Reach Outcomes

### Strategy 1: Contract-first execution abstraction

Define one `RuntimeExecutor` interface and route all execution through it before adding profile-specific implementations.

### Strategy 1.1: Reuse existing local execution core

Lift/reuse logic from `dalston/engine_sdk/local_runner.py` for `InProcExecutor` where possible, instead of re-implementing engine invocation/materialization paths.

### Strategy 2: Policy-driven profile assignment

Assign profiles in catalog/config and enforce at startup. Avoid runtime-name conditionals scattered through orchestration code.

### Strategy 3: Incremental profile rollout

Start with high-conflict runtimes and expand profile adoption in slices with clear rollback boundaries.

### Strategy 4: Keep artifact and task contracts stable

Execution profile affects isolation boundary only, not task payload/output schema.

### Strategy 4.1: Keep dispatch chain explicit

M59 keeps existing queueing contracts and clarifies where profile dispatch happens:

1. Lite mode: orchestrator/lite runner selects `inproc` or `venv` executor for stage execution.
2. Distributed mode: scheduler enqueues to Redis as today; `container` profile maps to existing long-running engine worker behavior.
3. No per-task container spawn flow is introduced in M59.

### Strategy 5: Explicit observability per profile

Expose profile and executor metadata in health/status/metrics to simplify debugging and operations.

---

## What We Will Not Do In M59

1. Do not redesign model registry semantics.
2. Do not introduce profile-specific API contracts.
3. Do not auto-install arbitrary dependencies at request time.
4. Do not require container-only execution for all runtimes.
5. Do not merge packaging/distribution goals (M60) into this milestone.

---

## Tactical Plan

### Entry Gate: Preconditions Before M59 Implementation

1. Confirm M56 lite path is stable (`dalston/orchestrator/lite_main.py` operational for scoped flows).
2. Verify M52 cleanup is complete for inherited runner debt:
   - check and resolve remaining stage-specific branch in `dalston/engine_sdk/runner.py` (notably merge-specific special casing) before profile work starts.

### Phase 0: Freeze Runtime Profile Contract

1. Define profile taxonomy and required metadata.
2. Freeze metadata schema and defaults:
   - `engines/**/engine.yaml`: add `execution_profile: inproc|venv|container`
   - default if omitted: `container` (backward compatibility)
3. Freeze type ownership for profile state:
   - `CatalogEntry.execution_profile` as routing source of truth
   - do not require `EngineCapabilities.execution_profile` for lite path
   - expose active profile via catalog-backed status surfaces for lite/distributed
4. Define dispatch integration contract:
   - scheduler/queue path remains unchanged
   - lite execution delegates to selected `inproc`/`venv` executor
   - distributed container profile remains current Redis-stream worker path
5. Define model/artifact resolution contract for non-container profiles:
   - `runtime_model_id` remains the identity contract (M36/M40 unchanged)
   - profile-specific caches/materialization paths are execution details, not API changes
6. Freeze venv invocation mechanics:
   - subprocess-based execution in target venv Python
   - explicit serialization contract for `EngineInput`/`EngineOutput` (JSON envelope + artifact references)
   - no importlib `sys.path` injection isolation strategy
7. Define selection precedence and validation rules.
8. Define profile-specific error taxonomy and fallback policy (explicitly no silent fallback).

Expected files:

- `docs/specs/ARCHITECTURE.md` (update)
- `docs/specs/batch/ENGINES.md` (update)
- `docs/specs/batch/ORCHESTRATOR.md` (update)
- `scripts/generate_catalog.py`

### Phase 1: Executor Abstraction Layer

1. Refactor (not greenfield): extract LocalRunner execution core behind `RuntimeExecutor` interface.
2. Add `InProcExecutor` by reusing/extracting shared logic from `local_runner.py`.
3. Add profile field plumbing through catalog parsing/schema with default-compat handling.
4. Keep external LocalRunner CLI behavior unchanged while internals move behind shared executor core.

Expected files:

- `dalston/engine_sdk/local_runner.py`
- `dalston/orchestrator/catalog.py`
- `scripts/generate_catalog.py`
- `dalston/orchestrator/scheduler.py`
- `dalston/orchestrator/lite_main.py`
- `tests/unit/test_runtime_executor_contract.py` (new)

### Phase 2: Venv Profile Implementation

1. Add `VenvExecutor` with environment lifecycle management.
2. Add deterministic dependency-lock mapping per runtime profile.
3. Add venv cache and health checks.
4. Integrate venv profile into lite execution path via executor selection in `lite_main.py` (no distributed scheduler bypass/branch).
5. Ensure profile-specific model cache/materialization path handling follows Phase 0 contract.
6. Implement subprocess invocation + serialization protocol frozen in Phase 0.

Expected files:

- `dalston/engine_sdk/executors/venv_executor.py` (new)
- `dalston/engine_sdk/executors/env_manager.py` (new)
- `dalston/orchestrator/lite_main.py`
- `tests/unit/test_venv_executor.py` (new)
- `tests/integration/test_runtime_profile_venv.py` (new)

### Phase 3: Container Profile Implementation

1. Treat `container` profile as existing distributed dispatch path:
   - orchestrator scheduler writes to Redis streams as today
   - long-running engine containers process tasks via `EngineRunner`
2. Do not add new orchestrator-side container scheduling logic.
3. Explicitly keep per-task one-shot container launching out of scope for M59.
4. Add profile-aware validation/observability for this path and cover with regression tests.

Expected files:

- `dalston/orchestrator/scheduler.py`
- `dalston/engine_sdk/runner.py`
- `dalston/orchestrator/registry.py` (if profile state exposure required)
- `tests/integration/test_runtime_profile_container.py` (new)

### Phase 4: Profile Policy Integration and Migration

1. Populate engine catalog/runtime metadata with `execution_profile` for migrated runtimes (using Phase 1 schema/plumbing).
2. Add validation at startup and runtime selection points.
3. Migrate selected runtimes to isolated profiles in controlled order.
4. Required validation migration target: `nemo-msdd` on `venv` profile as canonical dependency-conflict runtime.
5. Keep backward compatibility for runtimes missing `execution_profile` by applying default `container` during migration window.

Expected files:

- `dalston/orchestrator/catalog.py`
- `engines/**/engine.yaml` (selected runtime entries)
- `tests/integration/test_runtime_profile_routing.py` (new)

### Phase 5: Observability and Docs

1. Add profile labels to status/health/metrics.
2. Add runbooks for profile-specific failures and remediation.
3. Update roadmap/reporting.

Expected files:

- `dalston/metrics.py`
- `dalston/gateway/api/v1/engines.py` (or equivalent status endpoints)
- `docs/specs/OBSERVABILITY.md`
- `docs/reports/M59-runtime-isolation-profiles.md` (new)

---

## Testing Plan

### Automated tests

1. Unit tests:
   - executor contract compliance
   - catalog/type parsing for `execution_profile` and default fallback
   - profile policy validation
   - no-fallback enforcement
2. Integration tests:
   - inproc/venv/container profile happy paths
   - representative dependency conflict scenarios
   - profile-specific failure handling
   - lite executor dispatch selection (`inproc` vs `venv`)
   - distributed container path regression under `execution_profile=container`
3. Regression tests:
   - existing inproc runtimes unchanged
   - existing container runtimes unchanged when `execution_profile` is omitted
   - output contracts stable across profiles

Suggested command sets:

```bash
pytest tests/unit/test_runtime_executor_contract.py \
       tests/unit/test_venv_executor.py \
       tests/integration/test_runtime_profile_venv.py \
       tests/integration/test_runtime_profile_container.py \
       tests/integration/test_runtime_profile_routing.py -q
```

```bash
pytest -q
```

### Manual verification

1. Run one runtime per profile and verify consistent task outputs.
2. Simulate a dependency conflict and verify isolated profile prevents system-wide breakage.
3. Inspect status/metrics and confirm profile visibility.

---

## Exit Criteria

1. Profile-aware execution is active in production codepaths.
2. At least one runtime per profile is validated end-to-end.
3. No silent fallback between profiles.
4. Observability and runbooks cover profile-based execution operations.
