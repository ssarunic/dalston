# M52: Engine SDK Local Runner DX (Clean-Cut)

| | |
|---|---|
| **Goal** | Make engine development truly no-infra by adding a first-class local runner utility (`audio + config.json -> output.json`) and removing leftover compatibility bridges |
| **Duration** | 5-7 days |
| **Dependencies** | M51 (stateless engine contract + artifact materialization) |
| **Deliverable** | File-based local runner command, canonical `output.json`, strict no-compat cleanup, updated docs/specs/tests |
| **Status** | Planned |

## Starting State (Post-M51)

M51 already delivered the core primitives:

1. Programmatic local runner class in `dalston/engine_sdk/local_runner.py`.
2. Filesystem-backed artifact transport in `dalston/engine_sdk/materializer.py` (`LocalFilesystemArtifactStore`).
3. M51 tests/docs proving no-Redis/no-S3 execution path.

Known gaps at the start of M52:

1. No file-based CLI entrypoint (`audio + config.json -> output.json`).
2. `LocalRunner.run()` still has stage-specific behavior (`if stage == "merge"` writing `transcript.json`).
3. Canonical `output.json` schema is not explicitly frozen in specs.
4. Legacy SDK alias surface still exists (`TaskInput` / `TaskOutput`).
5. Batch runner still has mixed-version stream fallback logic.
6. `EngineInput._get_typed_output()` silently swallows parse errors and returns `None`.
7. `EngineInput.__post_init__` falls back to `/tmp/dalston-empty-audio` when no audio artifact exists.

---

## Desired Outcomes (M52 Scope)

### Functional outcomes

1. Any engine can be run locally with no Redis, no S3, and no orchestrator process.
2. Default developer flow is one command with a local audio file and a JSON config file.
3. The local runner emits a canonical `output.json` envelope that mirrors runner output shape (`task_id`, `job_id`, `stage`, `data`, `produced_artifacts`, `produced_artifact_ids`).
4. Advanced stages (`align`, `diarize`, `pii_detect`, `audio_redact`, `merge`) are supported with optional local JSON files for `payload`, `previous_outputs`, and non-audio artifacts.

### DX outcomes

1. Inner-loop changes to engine logic can be validated in seconds without stack startup.
2. Engine authors have one documented local-run contract, not ad-hoc scripts.
3. Local runner becomes the default harness used before and during remaining engine refactors.

### Clean-start outcomes

1. No compatibility with pre-M51 engine interfaces/signatures.
2. Legacy compatibility code introduced for mixed-version rollouts is removed.
3. SDK docs/scaffolding no longer teach deprecated alias types.
4. Typed output parsing and audio-path behavior are explicit and fail-closed.

### Success criteria

1. Command-level flow works:
   - `python -m dalston.engine_sdk.local_runner run --engine <module:Class> --audio <file> --config <file> --output <file>`
2. Unit tests prove no Redis/S3 requirement in the local-run path.
3. At least one side-effect-heavy engine can be executed locally through the file-based flow.
4. No remaining `TaskInput`/`TaskOutput` compatibility aliases in SDK public surface.
5. No legacy queue fallback path remains in batch runner.
6. `_get_typed_output` no longer fails silently.
7. `/tmp/dalston-empty-audio` placeholder fallback is removed.

---

## Strategy To Reach Outcomes

### Strategy 1: Workflow-first, then engine migration

Build and stabilize the local runner utility before refactoring remaining engines (diarization/alignment/PII). This turns M52 into the safety harness and velocity multiplier for the refactor sweep.

### Strategy 2: Single explicit local-run contract

Define one file-based contract for local runs and enforce it strictly:

1. Required: engine reference, stage, `config.json`, and output path.
2. Required for simple path: `audio` file.
3. Optional for advanced stages: `payload.json`, `previous_outputs.json`, `artifacts.json`.
4. Strict validation with fail-closed errors for malformed input.

Canonical `output.json` schema frozen in Phase 0:

```json
{
  "task_id": "task-local",
  "job_id": "job-local",
  "stage": "transcribe",
  "data": {},
  "produced_artifacts": [],
  "produced_artifact_ids": []
}
```

Baseline invocation:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines.stt-transcribe.faster-whisper.engine:FasterWhisperEngine \
  --stage transcribe \
  --audio ./fixtures/audio.wav \
  --config ./fixtures/transcribe-config.json \
  --output ./tmp/output.json
```

Advanced invocation (align/diarize/pii_detect path):

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines.stt-align.phoneme-align.engine:PhonemeAlignEngine \
  --stage align \
  --config ./fixtures/align-config.json \
  --payload ./fixtures/align-payload.json \
  --previous-outputs ./fixtures/previous-outputs.json \
  --artifacts ./fixtures/artifacts.json \
  --output ./tmp/output.json
```

### Strategy 3: Clean-cut compatibility policy

No bridge code for legacy engine versions or old SDK aliases. If code paths exist only to support mixed old/new behavior, remove them as part of M52.

### Strategy 4: Keep local runner generic and stage-agnostic

No stage-specific output side effects inside runner utility code. Any stage-specific files must be expressed as produced artifacts or handled by caller-level workflows.

### Strategy 5: Contract hardening in the SDK surface

1. `audio_path` policy:
   - Preserve as optional convenience field.
   - Derive from `materialized_artifacts["audio"]` (or first artifact) when available.
   - If no materialized artifacts are present, keep `audio_path=None` (no placeholder path).
2. Typed output policy:
   - Remove catch-all exception swallowing in `_get_typed_output`.
   - Parse failures must produce explicit failure signals (raised or structured error path), not silent `None`.

### Strategy 6: Docs/specs as part of the implementation, not follow-up

The local-run workflow and contracts are part of the deliverable. Implementation is incomplete unless docs/specs and tests are updated in the same milestone.

---

## What Not To Do

1. Do not add compatibility shims for old engine signatures.
2. Do not support URI-based engine input contracts in local-run tooling.
3. Do not emulate full orchestrator behavior inside the local runner.
4. Do not maintain parallel deprecated docs or examples (`TaskInput`/`TaskOutput` style).
5. Do not keep temporary fallback code once cutover is complete.
6. Do not bundle the full diarize/align/PII refactor sweep into M52 implementation scope.

---

## Tactical Plan

### Phase 0: Freeze Local-Run Contract

1. Define CLI UX and JSON schemas before code changes:
   - command args
   - required vs optional files
   - canonical `output.json` shape
2. Define error model (missing files, invalid JSON, invalid engine ref, missing artifacts).
3. Freeze `audio_path` and `_get_typed_output` contract decisions for clean-cut behavior.
4. Lock contract in docs/specs first to prevent tool drift.

Expected files:

- `docs/specs/batch/ENGINES.md`
- `docs/guides/new-transcription-engine-tutorial.md`

### Phase 1: Implement File-Based Local Runner Utility

1. Add CLI entrypoint in `dalston.engine_sdk.local_runner`.
2. Add dynamic engine loader from `<module:Class>` reference.
3. Load `config.json` and optional JSON files.
4. Resolve local artifact paths and invoke `LocalRunner`.
5. Always write a requested `output.json` file (all stages).

Expected files:

- `dalston/engine_sdk/local_runner.py`
- `tests/unit/test_m52_local_runner_cli.py` (new)

### Phase 2: Harden Contract and Remove Stage Leak

1. Validate JSON schemas and path existence.
2. Enforce deterministic defaults (`job_id`, `task_id`) when not provided.
3. Ensure emitted `output.json` is stable and reproducible.
4. Remove hardcoded `if stage == "merge"` sidecar write behavior from `LocalRunner.run()`.
5. Keep runner generic; no stage-specific branches in local utility flow.

Expected files:

- `dalston/engine_sdk/local_runner.py`
- `tests/unit/test_m52_local_runner_cli.py`
- `tests/unit/test_m52_local_runner_contract.py` (new)

### Phase 3: Remove Legacy Compatibility and Harden SDK Surface

1. Remove SDK alias bridge types:
   - `TaskInput` / `TaskOutput` alias exports.
2. Remove mixed-version queue polling fallback in batch runner.
3. Replace silent parse behavior in `_get_typed_output` with explicit failure behavior.
4. Remove `/tmp/dalston-empty-audio` placeholder fallback; keep `audio_path` optional.
5. Update scaffolder/templates to generate only current types.

Expected files:

- `dalston/engine_sdk/types.py`
- `dalston/engine_sdk/__init__.py`
- `dalston/engine_sdk/runner.py`
- `dalston/tools/scaffold_engine.py`
- `tests/unit/test_m51_enforcement.py`
- `tests/unit/test_scaffold_engine.py`
- `tests/unit/test_m52_sdk_surface.py` (new)
- `tests/unit/test_m52_engine_input_contract.py` (new)
- `tests/unit/test_m52_runner_stream_contract.py` (new)

### Phase 4: Documentation and Spec Updates

1. Publish local runner usage guide (quick path + advanced artifact path).
2. Update engine authoring tutorial to use only current types and local-run command.
3. Update testing playbook with local-run CLI checks and expected output contract.
4. Record deprecations removed in milestone notes and plan index.

Expected files:

- `docs/guides/new-transcription-engine-tutorial.md`
- `docs/testing/M51-engine-testing-playbook.md`
- `docs/specs/batch/ENGINES.md`
- `docs/README.md`
- `docs/plan/README.md`

### Phase 5: Readiness Gate for Follow-On Engine Refactor Sweep

1. Run the new local-run command against one representative engine for:
   - `align`
   - `diarize`
   - `pii_detect`
2. Capture any contract gaps discovered by those dry runs.
3. Publish a short readiness note for the follow-on refactor milestone.
4. Full diarize/align/PII migration work is explicitly out of scope for M52.
5. If readiness findings exceed M52 scope, promote them into a dedicated follow-on milestone (for example M53) rather than expanding M52.

Expected files:

- `docs/reports/M52-local-runner-readiness.md` (new)

---

## Testing Plan

### Automated tests

1. New local runner CLI unit tests:
   - happy path (`audio + config -> output.json`)
   - advanced path (`payload/previous_outputs/artifacts`)
   - validation failures (missing/invalid JSON, bad engine ref)
   - deterministic envelope assertions
2. Materializer/local store regression tests:
   - no network dependencies
   - produced artifact persistence in local filesystem mode
3. Legacy cleanup tests:
   - no alias exports (`TaskInput`/`TaskOutput`)
   - no stage-stream fallback path in runner
   - no `/tmp/dalston-empty-audio` placeholder path behavior
   - no silent typed-output parse failures
4. Guardrail tests:
   - no URI/storage coupling regressions in engines

Suggested commands:

```bash
pytest \
  tests/unit/test_m52_local_runner_cli.py \
  tests/unit/test_m52_local_runner_contract.py \
  tests/unit/test_m52_sdk_surface.py \
  tests/unit/test_m52_engine_input_contract.py \
  tests/unit/test_m52_runner_stream_contract.py \
  tests/unit/test_m51_local_runner.py \
  tests/unit/test_m51_materializer.py \
  tests/unit/test_m51_enforcement.py \
  tests/unit/test_scaffold_engine.py -q
```

Follow-on sweep (outside M52 scope):

```bash
pytest \
  tests/unit/test_m51_engine_artifacts.py \
  tests/unit/test_engine_sdk_types.py \
  tests/integration/test_engine_typed_outputs.py -q
```

### Manual verification

1. Run local command for one transcribe engine on a local WAV and inspect `output.json`.
2. Run advanced local command (with `payload`, `previous_outputs`, `artifacts`) and inspect `output.json`.
3. Confirm no Redis/S3 services are running and local run still succeeds.
4. Confirm local runner no longer writes `transcript.json` through hardcoded stage checks.

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| CLI contract grows ad-hoc flags and becomes confusing | High | Freeze contract in Phase 0 and keep one canonical flow |
| Legacy cleanup breaks examples/tests | Medium | Update scaffolder + docs + tests in same change set |
| Engine-specific assumptions leak into utility | Medium | Keep utility generic; stage specifics live in input JSON |
| Refactor sweep starts before utility is stable | High | Treat M52 completion as prerequisite gate and publish readiness note |

---

## Exit Criteria

1. Local runner command supports `audio + config.json -> output.json` without Redis/S3.
2. Canonical output envelope is produced for all stages.
3. Hardcoded merge-specific side effect in local runner is removed.
4. Legacy compatibility code targeted by M52 is removed.
5. `_get_typed_output` no longer swallows parse errors silently.
6. `audio_path` placeholder fallback is removed and contract is explicit.
7. Documentation/specs reflect the new workflow and removed legacy patterns.
8. Test suite for local runner + cleanup guardrails passes.
9. Readiness gate is completed and documented for follow-on refactor milestone.
