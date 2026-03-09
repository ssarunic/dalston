# M59: Runtime Isolation Profiles — Implementation Report

| | |
|---|---|
| **Milestone** | M59 |
| **Status** | Completed |
| **Date** | 2026-03-08 |
| **Dependencies** | M52 (local runner), M58 (lite profiles), M36/M40 (runtime model lifecycle) |

## Summary

M59 introduces an explicit runtime isolation contract across Dalston:
`inproc`, `venv`, and `container`. Routing is now policy-driven from
`CatalogEntry.execution_profile`, with no silent fallback between execution
backends and no per-task container spawning.

## What Was Delivered

### Phase 0 — Contract Freeze

- Added `execution_profile` to the engine schema and generated catalog.
- Defaulted omitted profiles to `container` for backward compatibility.
- Updated architecture and batch-engine/orchestrator docs to make the routing
  contract explicit.
- Cleared the M59 entry gate by removing the last merge-stage special casing
  from the runner/materializer path.

### Phase 1 — Executor Abstraction

- Added `RuntimeExecutor` and `ExecutionRequest`.
- Extracted `InProcExecutor` from the `LocalRunner` execution core instead of
  re-implementing local execution.
- Kept `LocalRunner` CLI behavior unchanged.
- Wired lite execution dispatch to select executors from
  `CatalogEntry.execution_profile`.

### Phase 2 — Venv Execution

- Added `VenvExecutor` using subprocess invocation only.
- Added `VenvEnvironmentManager` for interpreter discovery, caching, and health
  checks.
- Kept the serialized task contract stable across `inproc` and `venv`.
- Added no-fallback enforcement for missing/broken isolated executors.

### Phase 3 — Container Validation

- Kept `container` as the existing Redis-stream + long-running worker path.
- Added distributed-path validation so non-container runtimes cannot be queued
  or booted as engine workers.
- Propagated `execution_profile` through Redis registry/task metadata for
  observability.

### Phase 4 — Policy Migration

- Kept distributed utility runtimes such as `audio-prepare` and `final-merger`
  on `container`, while migrating `nemo-msdd` to `venv` as the canonical
  isolated runtime.
- Marked `nemo-msdd` as `venv` as the canonical conflict-isolation runtime.
- Regenerated the catalog and validated all engine metadata.

### Phase 5 — Observability and Docs

- Added `execution_profile` to `/v1/engines`.
- Added `execution_profile` metric labels on orchestrator scheduling/completion
  metrics and engine processing/storage timing metrics.
- Added profile-specific runbook guidance to
  `docs/specs/OBSERVABILITY.md`.
- Added this report.

## Tests and Validation

New or expanded test coverage:

- `tests/unit/test_runtime_executor_contract.py`
- `tests/unit/test_venv_executor.py`
- `tests/unit/test_metrics.py`
- `tests/integration/test_runtime_profile_venv.py`
- `tests/integration/test_runtime_profile_container.py`
- `tests/integration/test_runtime_profile_routing.py`
- `tests/integration/test_engines_api.py`

Validated during implementation:

- `python -m pytest tests/unit/test_runtime_executor_contract.py tests/unit/test_venv_executor.py tests/integration/test_runtime_profile_venv.py tests/integration/test_runtime_profile_container.py tests/integration/test_runtime_profile_routing.py -q`
- `python -m dalston.tools.validate_engine --all --quiet`
- phase-by-phase `make lint`
- phase-by-phase `make test`

Final verification is recorded in the implementation session for:

- full unit/integration/e2e execution
- full regression test suite
- sample-audio runtime checks across at least one runtime per profile

## Design Decisions

**Catalog is authoritative**: execution policy lives in the generated catalog,
not in scattered runtime-name checks.

**Refactor over rewrite**: `InProcExecutor` was extracted from `LocalRunner`;
local execution behavior stayed stable.

**No silent fallback**: if `venv` or `inproc` is declared and that executor is
missing or fails, the request raises immediately.

**Container path unchanged**: M59 does not add one-shot container launches.
The existing long-running worker model remains the only `container` execution
path in this milestone.
