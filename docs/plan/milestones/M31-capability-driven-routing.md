# M31: Capability-Driven Engine Routing

|                  |                                                                                         |
| ---------------- | --------------------------------------------------------------------------------------- |
| **Goal**         | Orchestrator selects engines by capability, not hardcoded names; DAG shape adapts automatically |
| **Duration**     | 6 days                                                                                  |
| **Dependencies** | M30 (Engine Metadata Evolution)                                                         |
| **Deliverable**  | Engine selector module, capability-driven DAG builder, actionable error messages, auto-failover |
| **Status**       | Done                                                                                    |

## User Stories

> *"As an operator, I want to add a new transcription engine by deploying a container with engine.yaml, without modifying orchestrator code."*

> *"As an API client, I want immediate, actionable feedback when my job can't be processed, not a generic failure after queuing."*

> *"As a platform owner, I want jobs to automatically re-route to alternative engines when the originally selected one goes down."*

---

## Context

M30 established engine.yaml as the single source of truth and exposed capabilities via registry heartbeats and the catalog. But the orchestrator still doesn't *use* this metadata for routing decisions — engine selection was hardcoded via `DEFAULT_ENGINES` and `NATIVE_WORD_TIMESTAMP_ENGINES` dicts in `dag.py`.

**Problems this creates:**

1. **New engines require code changes** — Adding a transcriber means editing `DEFAULT_ENGINES` and possibly `NATIVE_WORD_TIMESTAMP_ENGINES`
2. **Silent misrouting** — Croatian audio routed to English-only Parakeet produces garbage
3. **Suboptimal pipelines** — Engine with native word timestamps still gets alignment stage if not in the hardcoded set
4. **Opaque failures** — "Engine unavailable" with no guidance on what's running or what to start
5. **No failover** — If selected engine dies, task fails permanently

This milestone makes the orchestrator capability-driven:

- Engine selection queries the registry by stage + requirements
- DAG shape determined by selected engine's capability flags
- Clear errors explain why no engine matched and what alternatives exist
- Automatic re-routing when engines disappear

### Architecture After M31

Job submission extracts requirements from parameters, then the engine selector queries the registry (what's running) and catalog (what could run) to filter and rank candidates per stage. The DAG builder uses selected engine capabilities to determine pipeline shape (e.g., skip alignment if transcriber has native word timestamps, skip diarize if transcriber includes diarization).

---

## Outcomes

### O1: Zero-Code Engine Additions

**Before:** Adding a new engine requires modifying `DEFAULT_ENGINES` and `NATIVE_WORD_TIMESTAMP_ENGINES` in dag.py.

**After:** Deploy engine with engine.yaml → it participates in routing automatically based on declared capabilities.

**Verification:** Deploy a new engine container with engine.yaml and submit a job — the new engine is selected automatically if it's the best match.

### O2: Correct Language Routing

**Before:** Croatian audio may be routed to English-only Parakeet, producing garbage output.

**After:** Jobs route to engines that support the requested language, or fail fast with clear error.

**Verification:** Submit Croatian audio when only Parakeet (English-only) is running — returns 422 with actionable error listing running engines and catalog alternatives.

### O3: Automatic Pipeline Optimization

**Before:** DAG always includes alignment stage unless engine is in hardcoded `NATIVE_WORD_TIMESTAMP_ENGINES` set.

**After:** DAG shape adapts based on selected engine's capabilities:

- `supports_word_timestamps: true` → skip alignment stage
- `includes_diarization: true` → skip diarize stage

**Verification:** With Parakeet selected, DAG skips alignment (prepare -> transcribe -> merge). With faster-whisper, DAG includes alignment stage.

### O4: Actionable Error Messages

**Before:** "Engine unavailable" or generic 500 errors.

**After:** Errors explain what was required, what's running, why each failed, and what alternatives exist.

**Verification:** Error responses include structured JSON with `error`, `stage`, `requirements`, `running_engines` (with per-engine mismatch reasons), and `catalog_alternatives`.

### O5: Automatic Failover

**Before:** If selected engine goes down between submission and execution, task fails permanently.

**After:** System re-selects alternative engine and re-routes task (up to retry budget).

**Verification:** Kill an engine mid-processing; task automatically re-routes to an alternative compatible engine. Logs show `engine_reselected` event.

### O6: Selection Observability

**Before:** No visibility into why a particular engine was chosen.

**After:** Structured logs document every selection decision with `engine_selected` and `dag_shape_decided` events.

---

## Strategy

### Phase 1: Foundation (Days 1-2)

Extend the data model and add prerequisite methods before building the selector.

1. Add missing field to `EngineCapabilities` (`includes_diarization`)
2. Update catalog loader to extract new fields
3. Add `find_engines()` method to catalog for requirement-based queries
4. Resolve faster-whisper word timestamps discrepancy

### Phase 2: Engine Selector (Days 2-3)

Build the core selection logic as an isolated, testable module.

1. Create `engine_selector.py` with `select_engine()` function
2. Implement requirements matching (hard filters)
3. Implement ranking logic (soft preferences)
4. Build `NoCapableEngineError` with structured context

### Phase 3: DAG Builder Integration (Days 3-4)

Replace hardcoded engine selection with capability-driven selection.

1. Update `build_task_dag()` signature to accept registry and catalog
2. Replace `DEFAULT_ENGINES` with selector calls
3. Replace `NATIVE_WORD_TIMESTAMP_ENGINES` with capability checks
4. Update all callers of `build_task_dag()`

### Phase 4: Error Handling & Failover (Days 5-6)

Make the system robust and observable.

1. Wire selector into job submission for fail-fast validation
2. Implement retry-with-reselection flow
3. Add structured logging for selection decisions
4. Add strangle-fig fallback during migration

---

## Tactical Plan

### 31.1: Extend EngineCapabilities Model

Added `includes_diarization: bool` field to `EngineCapabilities` for DAG shape decisions.

*Implementation: see `dalston/engine_sdk/types.py`*

**Tests:** `tests/unit/test_engine_capabilities.py`

---

### 31.2: Update Catalog Loader

Extended the catalog loader to extract `includes_diarization` from engine.yaml into capabilities.

*Implementation: see `dalston/orchestrator/catalog.py`*

---

### 31.3: Add find_engines() to Catalog

Added `find_engines()` and `_matches_requirements()` methods to `EngineCatalog` for requirement-based queries (language filtering, etc.), used by error messages to suggest catalog alternatives.

*Implementation: see `dalston/orchestrator/catalog.py`*

**Tests:** `tests/unit/test_catalog_find_engines.py`

---

### 31.4: Clarify supports_word_timestamps Semantics

**Resolved:** The `supports_word_timestamps` field means "produces **accurate** word-level timestamps that don't require correction downstream."

- **Parakeet**: `true` — Native RNNT alignment produces accurate timestamps
- **faster-whisper**: `false` — Whisper's word timestamps are notoriously inaccurate; the alignment stage (WhisperX's wav2vec2 forced alignment) is required to fix them

This matches the current DAG builder behavior: faster-whisper is NOT in `NATIVE_WORD_TIMESTAMP_ENGINES`, so alignment is always added. The engine.yaml was incorrectly claiming `word_timestamps: true`.

**Note:** We don't need a dedicated WhisperX transcription engine. WhisperX functionality is achieved by combining faster-whisper + alignment stage + diarization stage — which is exactly what the capability-driven DAG builder does automatically.

*Implementation: see `engines/transcribe/faster-whisper/engine.yaml` (set `word_timestamps: false`)*

---

### 31.5: Create Engine Selector Module

Created `engine_selector.py` with the core selection logic: `EngineSelectionResult` dataclass, `NoCapableEngineError` exception with structured context, and `select_engine()` function that validates explicit user choices, filters candidates by hard requirements (language, streaming), and ranks multiple matches by native word timestamps, native diarization, speed (RTF), and specificity.

*Implementation: see `dalston/orchestrator/engine_selector.py`*

**Tests:** `tests/unit/test_engine_selector.py`

---

### 31.6: Implement NoCapableEngineError Messages

`NoCapableEngineError` provides detailed, actionable error formatting: `_build_message()` produces a human-readable message listing running engines with per-engine mismatch reasons and catalog alternatives with start commands. `to_dict()` produces structured JSON for API responses.

*Implementation: see `dalston/orchestrator/engine_selector.py`*

---

### 31.7: Extract Job Requirements

Implemented `extract_requirements()` to convert user-facing job parameters (language, language_code, streaming) into internal selector requirements dict.

*Implementation: see `dalston/orchestrator/engine_selector.py`*

---

### 31.8: Create select_all_engines() Function

Implemented `select_all_engines()` for all-or-nothing pipeline selection: always selects transcribe/prepare/merge, conditionally adds alignment (via `_should_add_alignment()` — needed when job wants word timestamps and transcriber lacks native support) and diarization (via `_should_add_diarization()` — needed when speaker_detection is requested and transcriber doesn't include it).

*Implementation: see `dalston/orchestrator/engine_selector.py`*

**Tests:** `tests/unit/test_select_all_engines.py`

---

### 31.9: Refactor build_task_dag() Signature

Changed `build_task_dag()` from sync to async and added `registry` and `catalog` parameters. **Breaking change:** all callers updated.

*Implementation: see `dalston/orchestrator/dag.py`*

---

### 31.10: Find and Update All Callers

Updated all callers of `build_task_dag` including the scheduler and tests.

*Implementation: see `dalston/orchestrator/scheduler.py`, `tests/unit/test_dag.py`, and integration tests*

---

### 31.11: Replace DEFAULT_ENGINES with Selector

Removed hardcoded `DEFAULT_ENGINES` and `NATIVE_WORD_TIMESTAMP_ENGINES` dicts. The async DAG builder now calls `select_all_engines()` and derives DAG shape from the returned selections (skip alignment if not in selections, skip diarization if not in selections).

*Implementation: see `dalston/orchestrator/dag.py`*

---

### 31.12: Update _build_per_channel_dag()

Updated per-channel DAG builder to accept `selections` dict instead of an engines dict, deriving skip-alignment/skip-diarization from capabilities rather than engine names.

*Implementation: see `dalston/orchestrator/dag.py`*

---

### 31.13: Wire Into Job Submission

Wired selector into job submission for fail-fast validation. `NoCapableEngineError` is caught and mapped to a 422 response with structured error context.

*Implementation: see `dalston/orchestrator/scheduler.py` and `dalston/gateway/api/v1/transcriptions.py`*

---

### 31.14: Implement Retry-with-Reselection

> **DEFERRED**: This task is deferred to a future milestone. The core capability-driven selection at job submission time covers the primary use case. Retry-with-reselection adds complexity for an edge case (engine dying mid-job) that can be handled by simple job resubmission for now.

---

### 31.15: Add Structured Logging

Added structured logging for selection decisions: `engine_selected` events in the selector (with stage, selected engine, reason, candidate counts) and `dag_shape_decided` events in the DAG builder (with transcriber, alignment/diarization inclusion, stage list).

*Implementation: see `dalston/orchestrator/engine_selector.py` and `dalston/orchestrator/dag.py`*

---

### 31.16: Add Strangle-Fig Fallback

Kept `_LEGACY_DEFAULT_ENGINES` as a fallback during migration. If the selector raises an unexpected exception, the DAG builder logs a warning and falls back to hardcoded defaults. To be removed after one milestone cycle in production.

*Implementation: see `dalston/orchestrator/dag.py`*

---

### 31.17: Update Unit Tests

Comprehensive tests for selector logic covering: requirement matching (language filtering, null-languages-means-all), ranking (prefers native word timestamps, native diarization, faster RTF), and end-to-end selection (single capable engine, no-capable-engine error with structured context).

*Implementation: see `tests/unit/test_engine_selector.py`*

---

### 31.18: Update Integration Tests

End-to-end tests for capability-driven DAG building: DAG skips alignment with native timestamps, DAG includes alignment without native timestamps, new engine works without code changes.

*Implementation: see `tests/integration/test_dag_capability_driven.py`*

---

### 31.19: Documentation

Updated architecture docs to reflect capability-driven routing.

*Implementation: see `docs/specs/batch/ORCHESTRATOR.md` and `docs/specs/batch/ENGINES.md`*

---

## What NOT to Do

- **Don't implement late binding** — Select at submission, not execution. Retry-with-reselection handles failures.
- **Don't add GPU/CPU preference to ranking** — We don't run both variants simultaneously yet.
- **Don't change pipeline stages** — This is routing within existing stages, not new stages.
- **Don't touch realtime path** — Batch only. Realtime uses different registry (WorkerRegistry).
- **Don't delete DEFAULT_ENGINES immediately** — Keep as fallback for one milestone cycle.
- **Don't over-engineer ranking** — 3 criteria are enough for 2-3 engines per stage.

---

## Verification

- [ ] Unit tests pass (`pytest tests/unit/test_engine_selector.py -v`)
- [ ] Integration tests pass (`pytest tests/integration/test_dag_capability_driven.py -v`)
- [ ] New engine deploys and routes correctly without code changes
- [ ] Language mismatch returns 422 with actionable error (running engines, catalog alternatives)
- [ ] DAG shape adapts: Parakeet skips alignment, faster-whisper includes it
- [ ] Structured logs show `engine_selected` and `dag_shape_decided` events

---

## Checkpoint

- [x] `includes_diarization` added to EngineCapabilities
- [x] Catalog loader extracts new fields
- [x] `find_engines()` method added to catalog
- [x] faster-whisper word timestamps discrepancy resolved
- [x] `engine_selector.py` created with `select_engine()`
- [x] `NoCapableEngineError` with structured messages
- [x] `extract_requirements()` converts job config
- [x] `select_pipeline_engines()` handles full pipeline
- [x] `build_task_dag_async()` added with capability-driven selection
- [x] Job handler wired to use `build_task_dag_async()`
- [x] DAG shape adapts based on engine capabilities
- [x] `_build_per_channel_dag_from_selections()` uses capabilities
- [x] Job submission wired with fail-fast validation
- [ ] ~~Retry-with-reselection implemented~~ (DEFERRED)
- [x] Structured logging added
- [x] Strangle-fig fallback in place (sync `build_task_dag()` preserved)
- [x] Unit tests for selector
- [x] Integration tests for DAG building
- [x] Documentation updated

---

## Enables Next

- **Auto-scaling**: Catalog + selector = "boot this image when no running engine matches"
- **Load-aware routing**: Add queue depth to ranking when `max_concurrent_jobs` is known
- **Cost optimization**: Route by GPU/CPU preference when both available
- **Multi-region routing**: Extend selector to consider engine location
