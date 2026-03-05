# M55: Non-Transcribe Runtime Model Management (Diarize/Align/PII, Clean-Cut)

| | |
|---|---|
| **Goal** | Make diarization, alignment, and PII stages fully model-pluggable with runtime model selection and registry-backed model lifecycle, matching the transcribe architecture |
| **Duration** | 8-12 days |
| **Dependencies** | M36 (runtime model management), M40 (model registry), M46 (registry as source of truth) |
| **Deliverable** | Stage model selection contract (`model_diarize`, `model_align`, `model_pii_detect`), stage-aware orchestrator routing, refactored engines consuming `runtime_model_id`, and removal of legacy transcribe-only assumptions |
| **Status** | Planned |

## Intended Outcomes

### Functional outcomes

1. Diarize, align, and PII stages can select models at job runtime, not only engine runtime.
2. The orchestrator passes `runtime_model_id` to all model-backed stages, not just transcribe.
3. Models for these stages are first-class registry entries with explicit `stage`.
4. Per-stage model selection works consistently in both standard and per-channel DAGs.

### Architecture outcomes

1. Stage model selection uses one canonical pattern across all model-backed stages.
2. No hidden hardcoded model IDs remain in engine business logic.
3. Stage mismatch is impossible at selection time (`model.stage` must equal requested stage).
4. Control-plane semantics are explicit: model choice resolves to `(runtime, runtime_model_id)` for each stage.

### Operational outcomes

1. Operators can pull/remove/list non-transcribe models using existing model lifecycle APIs.
2. Engine heartbeat/runtime state remains observable for active model usage.
3. Failure modes are explicit when a selected model is unavailable or stage-incompatible.

### Clean-start outcomes

1. No backward compatibility for legacy transcribe-only model semantics.
2. No legacy `engine_*` stage override behavior retained for diarize/align/PII.
3. Legacy code paths and assumptions are removed in the same milestone.

### Success criteria

1. A job can specify stage models and those exact model IDs are used by diarize/align/PII engines.
2. Selector rejects cross-stage model IDs deterministically.
3. Non-transcribe engines have no hardcoded default model constants in process paths.
4. Registry and docs describe model lifecycle and selection for all model-backed batch stages.
5. Legacy code and tests for obsolete behavior are removed.

---

## Strategy To Reach Outcomes

### Strategy 1: Freeze the stage-model contract first

Define one canonical job-parameter contract before code changes:

1. `model_transcribe` (existing semantic, renamed internally if needed)
2. `model_diarize`
3. `model_align`
4. `model_pii_detect`

Each field is a model registry ID (not runtime ID). The selector resolves model -> `(runtime, runtime_model_id)`.

### Strategy 2: Make model resolution stage-aware everywhere

Enforce `ModelRegistryModel.stage == requested_stage` at selection time. Any mismatch is a hard error, never fallback.

### Strategy 3: Push model identity through DAG configs uniformly

Every model-backed task config carries:

1. `runtime_model_id` (resolved loader identifier)
2. Stage-specific tuning fields (existing config keys)

No stage should infer model identity from environment-only defaults.

### Strategy 4: Refactor engines around explicit runtime model IDs

Each target engine must:

1. Read `input.config["runtime_model_id"]`
2. Load/cache models based on that ID
3. Report model identity in runtime state/health

### Strategy 5: Remove obsolete abstractions immediately after cutover

Do cleanup in the same branch:

1. Remove transcribe-only assumptions in shared services.
2. Remove deprecated parameter handling and stale tests.
3. Remove hardcoded model constants replaced by config-driven selection.

### Strategy 6: Documentation is an exit gate, not a follow-up

All specs and operational docs must match final behavior before milestone close.

---

## What Not To Do

1. Do not keep dual old/new parameter formats for stage model selection.
2. Do not allow model IDs to silently act as runtime IDs.
3. Do not silently fallback to wrong-stage models.
4. Do not leave hardcoded model IDs in diarize/align/PII engines "temporarily."
5. Do not keep transcribe-only lifecycle checks in registry services.
6. Do not defer docs/spec updates to a later milestone.

---

## Tactical Plan

### Phase 0: Contract Freeze (Hard Gate)

1. Freeze stage model parameter names and meanings.
2. Freeze error taxonomy:
   - `model_not_found`
   - `model_stage_mismatch`
   - `model_not_ready`
   - `runtime_unavailable`
3. Freeze clean-cut policy:
   - no compatibility layer for legacy stage override semantics
   - one coordinated migration branch

Expected files:

- `dalston/gateway/api/v1/transcription.py`
- `dalston/orchestrator/engine_selector.py`
- `docs/specs/MODEL_SELECTION.md`

### Phase 1: Control-Plane Refactor (Gateway/Orchestrator/Registry)

1. Gateway request mapping:
   - accept and persist `model_diarize`, `model_align`, `model_pii_detect`
   - map them into job parameters
2. Selector updates:
   - enforce stage check for DB model lookup
   - stage-aware auto-selection helper (not transcribe-only)
3. DAG updates:
   - pass `runtime_model_id` into diarize/align/pii task configs
   - include per-channel variants where applicable
4. Registry service updates:
   - remove transcribe-only "model in use" checks
   - make in-use checks stage-parameter aware
5. HF resolver policy:
   - extend only where metadata is reliable (e.g., diarization pipeline tag)
   - keep explicit registration for ambiguous align/PII cases

Expected files:

- `dalston/gateway/api/v1/transcription.py`
- `dalston/orchestrator/engine_selector.py`
- `dalston/orchestrator/dag.py`
- `dalston/gateway/services/model_registry.py`
- `dalston/gateway/services/hf_resolver.py`

### Phase 2: Diarization Engine Refactor

1. `pyannote-4.0`:
   - replace hardcoded model constant with `runtime_model_id` from config
   - add model caching/manager semantics
2. `nemo-msdd`:
   - remove hardcoded component model paths from static config
   - make component model set selectable via resolved runtime model
3. Engine YAML:
   - add `runtime_model_id` to config schema for diarize runtimes

Expected files:

- `engines/stt-diarize/pyannote-4.0/engine.py`
- `engines/stt-diarize/pyannote-4.0/engine.yaml`
- `engines/stt-diarize/nemo-msdd/engine.py`
- `engines/stt-diarize/nemo-msdd/engine.yaml`

### Phase 3: Alignment Engine Refactor

1. `phoneme-align` engine:
   - accept `runtime_model_id` in task config
   - resolve model choice by explicit ID first, then stage default policy
   - refactor cache keying to reflect chosen model IDs (not language-only)
2. Config schema:
   - add `runtime_model_id` support
3. Model loader:
   - keep deterministic model resolution rules
   - fail loudly on invalid explicit overrides

Expected files:

- `engines/stt-align/phoneme-align/engine.py`
- `engines/stt-align/phoneme-align/model_loader.py`
- `engines/stt-align/phoneme-align/engine.yaml`

### Phase 4: PII Engine Refactor

1. `pii-presidio`:
   - replace hardcoded GLiNER model ID with `runtime_model_id`
   - keep Presidio rule path explicit and deterministic
2. Config schema:
   - add `runtime_model_id` for NER backbone selection
3. Runtime state:
   - expose active model in health/runtime reporting

Expected files:

- `engines/stt-detect/pii-presidio/engine.py`
- `engines/stt-detect/pii-presidio/engine.yaml`

### Phase 5: Model Registry Data and Catalog Seeding

1. Add non-transcribe model YAML entries for:
   - diarize runtimes
   - align runtime defaults/variants
   - pii_detect variants
2. Ensure seeding populates correct `stage` and metadata.
3. Verify model list/filter UX for these stages remains coherent.

Expected files:

- `models/*.yaml` (new stage entries)
- `dalston/gateway/services/model_yaml_loader.py` (if schema extensions required)
- `dalston/gateway/api/v1/models.py` (if response semantics need updates)

### Phase 6: Legacy Removal (Required Exit Gate)

1. Remove transcribe-only comments/logic implying only transcribe has runtime models.
2. Remove obsolete `engine_*` stage override pathways for diarize/align/pii.
3. Remove hardcoded model constants superseded by runtime model config.
4. Remove tests that validate legacy behavior and replace with new contract tests.

Expected files:

- `dalston/orchestrator/engine_selector.py`
- `dalston/orchestrator/dag.py`
- `engines/stt-diarize/*/engine.py`
- `engines/stt-align/phoneme-align/engine.py`
- `engines/stt-detect/pii-presidio/engine.py`
- related unit/integration tests

### Phase 7: Documentation and Spec Updates

1. Update model selection spec to cover all model-backed stages.
2. Update engine spec for non-transcribe runtime model management.
3. Update pipeline interfaces where stage config contracts changed.
4. Add implementation report for M55 with behavior deltas and migration notes.

Expected files:

- `docs/specs/MODEL_SELECTION.md`
- `docs/specs/batch/ENGINES.md`
- `docs/specs/PIPELINE_INTERFACES.md`
- `docs/README.md`
- `docs/reports/M55-non-transcribe-runtime-model-management-implementation.md` (new)

---

## Testing Plan

### 1) Unit Tests

Control plane:

1. Selector rejects stage-mismatched model IDs.
2. Selector returns correct `(runtime, runtime_model_id)` for diarize/align/pii model IDs.
3. DAG injects `runtime_model_id` into diarize/align/pii configs (including per-channel branch).

Engines:

1. Pyannote engine uses config `runtime_model_id`.
2. Nemo diarize engine resolves model set from config `runtime_model_id`.
3. Align engine model cache/load keyed by selected model identity.
4. PII engine loads GLiNER from config `runtime_model_id`.

Registry/services:

1. In-use checks are stage-aware.
2. Registry list/filter and seeding include non-transcribe stage models.

### 2) Integration Tests

1. Submit job with `model_diarize` and verify diarize task receives expected `runtime_model_id`.
2. Submit job with `model_align` and verify align output path uses selected model.
3. Submit job with `model_pii_detect` and verify detected output includes selected model in metadata/runtime state.
4. Stage mismatch request fails with deterministic error code.

### 3) End-to-End Matrix

1. `transcribe + diarize(model override) + merge`
2. `transcribe + align(model override) + merge`
3. `transcribe + align + pii_detect(model override) + audio_redact + merge`
4. per-channel with `pii_detect` model override

### 4) Regression and Cleanup Verification

1. Search-based guardrail:
   - no legacy hardcoded model constants in target engines
   - no stale `engine_*` stage override logic for diarize/align/pii
2. Ensure removed legacy tests are replaced by new contract tests.
3. Verify docs/spec examples match implemented parameter names and behavior.

Suggested command set:

```bash
pytest \
  tests/unit/test_engine_selector.py \
  tests/unit/test_dag.py \
  tests/unit/test_phoneme_align_engine.py \
  tests/unit/test_pii_engine.py \
  tests/unit/test_pyannote_engine.py \
  tests/unit/test_nemo_msdd_engine.py \
  tests/integration/test_model_endpoints_auth.py \
  tests/integration/test_transcription_api.py -q
```

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Stage-model contract drift between gateway/orchestrator/engines | High | Freeze parameter and config contracts in Phase 0 and enforce with unit tests |
| HF auto-resolution ambiguity for align/PII | Medium | Restrict auto-resolution to reliable cases; require explicit registration otherwise |
| Excessive scope from compatibility handling | Medium | Clean-cut policy: remove old paths instead of dual support |
| Hidden legacy assumptions survive migration | Medium | Phase 6 cleanup as mandatory exit gate with code-search assertions |

---

## Exit Criteria

1. Non-transcribe model selection works end-to-end via registry IDs.
2. Diarize/align/pii engines consume runtime-selected model IDs.
3. Selector enforces stage correctness for model IDs.
4. Legacy transcribe-only model management assumptions are removed.
5. Docs/specs/report are updated to reflect final behavior.
