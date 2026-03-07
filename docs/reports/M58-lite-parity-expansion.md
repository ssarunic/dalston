# M58: Lite Pipeline Expansion and Capability Parity — Implementation Report

| | |
|---|---|
| **Milestone** | M58 |
| **Status** | Completed |
| **Date** | 2026-03-07 |
| **Dependencies** | M56 (lite infra backends), M57 (zero-config bootstrap) |

## Summary

M58 expands `lite` mode from the minimal `core` path (prepare → transcribe →
merge) established in M56/M57 to a broader, explicitly-documented capability
set with named profiles, fail-fast validation, and machine-readable discovery.

## What Was Delivered

### Phase 0 — Canonical Capability Matrix

Created `dalston/orchestrator/lite_capabilities.py` as the single source of
truth for all lite profile information.  CLI output, API responses, and docs
all derive from this module; nothing is duplicated.

Key exports:

- `CAPABILITY_MATRIX` — versioned dict of `LiteProfile → ProfileCapability`
- `MATRIX_VERSION = "1.0.0"` — semver for schema compatibility
- `resolve_profile(name)` — validate and resolve a profile name
- `validate_request(profile, parameters)` — enforce capability guardrails
- `check_prerequisites(profile)` — detect missing runtime packages
- `get_matrix_as_dict()` — serialise matrix for API/docs

### Phase 1 — Profile-Aware Planner and Validation

- Extended `LitePipeline` (in `dalston/orchestrator/lite_main.py`) to accept a
  `profile` parameter.  The existing `build_default_pipeline()` function is
  retained as a backward-compatible alias calling `build_pipeline("core")`.
- Added `validate_request()` call inside `LitePipeline.run_job()` so that
  validation applies even when the pipeline is constructed directly (not via
  the gateway).
- Added `lite_profile` form parameter to `POST /v1/audio/transcriptions`.  In
  lite mode the endpoint validates the profile before creating any DB records
  (fail-fast) and constructs the pipeline with the chosen profile.
- Added `LiteUnsupportedFeatureError`, `LiteProfileNotFoundError`, and
  `LitePrerequisiteMissingError` — all carry remediation hints.

### Phase 2 — Expanded Stage Coverage

- **`speaker` profile**: adds a `diarize` stage between `transcribe` and
  `merge`.  Output includes `speakers: ["SPEAKER_00", ...]` and per-segment
  `speaker` labels.  The `num_speakers` parameter is honoured.
- **`compliance` profile**: adds a `pii_detect` stage.  Requires
  `presidio_analyzer` + `presidio_anonymizer`.  Absent packages raise
  `LitePrerequisiteMissingError` at pipeline construction time with
  deterministic pip install instructions.

Both profiles write intermediate task artifacts (`diarize/output.json`,
`pii_detect/output.json`) to the local filesystem consistent with the existing
artifact store contract.

### Phase 3 — CLI/API Surface Alignment

- Added `--profile` flag to `dalston transcribe`.  Default: `core`.  In lite
  mode the CLI validates the profile name locally (using `lite_capabilities.py`)
  before sending the request.  In distributed mode the flag is passed through
  (server ignores it).
- Added `lite_profile` parameter to `Dalston.transcribe()` and
  `AsyncDalston.transcribe()` in the SDK.

### Phase 4 — Capability Discovery

- Added `GET /v1/lite/capabilities` endpoint (no auth, no Redis dependency).
  Returns the full capability matrix as JSON, including `active_profile` and
  per-profile `missing_prereqs`.
- Mounted at `/v1/lite/capabilities` via a new `lite_router` in `engines.py`.

### Phase 5 — Docs

- `docs/specs/batch/lite-capability-matrix.md` — human-readable profile guide.
- This report.

## Files Changed

| File | Change |
|------|--------|
| `dalston/orchestrator/lite_capabilities.py` | **New** — canonical matrix |
| `dalston/orchestrator/lite_main.py` | Extended with profile support |
| `dalston/gateway/api/v1/transcription.py` | Added `lite_profile` param + validation |
| `dalston/gateway/api/v1/engines.py` | Added `GET /v1/lite/capabilities` |
| `dalston/gateway/api/v1/router.py` | Registered `lite_router` |
| `sdk/dalston_sdk/client.py` | Added `lite_profile` to `transcribe()` (sync+async) |
| `cli/dalston_cli/commands/transcribe.py` | Added `--profile` flag |
| `docs/specs/batch/lite-capability-matrix.md` | **New** — profile guide |

## Tests Added

| Test file | Type | Coverage |
|-----------|------|----------|
| `tests/unit/test_lite_profile_validation.py` | Unit | Capability matrix, resolve_profile, validate_request, error types |
| `tests/unit/test_cli_lite_profile_selection.py` | Unit | CLI --profile flag, SDK signature, distributed mode bypass |
| `tests/integration/test_lite_profile_speaker_flow.py` | Integration | speaker profile e2e, M56/M57 regression |
| `tests/integration/test_lite_profile_compliance_flow.py` | Integration | compliance prereq missing, mocked e2e, validation guardrails |
| `tests/integration/test_lite_capability_discovery.py` | Integration | GET /v1/lite/capabilities, matrix correctness, env var |

## Exit Criteria Status

| Criterion | Status |
|-----------|--------|
| Lite capability matrix implemented and enforced at runtime | ✅ |
| At least one expanded profile (`speaker`) works end-to-end | ✅ |
| Unsupported features fail deterministically with clear guidance | ✅ |
| M57 zero-config default path still passes unchanged | ✅ |
| M58 target test suite (5 test files, 95 tests) all green | ✅ |
| `pytest -q` passes with no regressions | ✅ |

## Design Decisions

**Single source of truth**: `lite_capabilities.py` is authoritative.  All
other surfaces (API endpoint, CLI help, docs) derive from it.  This prevents
drift between documented and actual capabilities — the primary risk identified
in the M58 spec.

**No silent fallback**: Every unsupported feature raises an error with a
`remediation` field that tells the user exactly what to do next.  There is no
automatic degradation to a lesser capability.

**Backward compatibility**: `build_default_pipeline()` is preserved unchanged.
The `core` profile is identical to the M56 baseline pipeline.  Existing tests
pass without modification.

**Compliance profile is conditional, not absent**: The profile exists in the
matrix with clear prerequisite documentation.  `check_prerequisites()` returns
the missing packages at runtime.  The `GET /v1/lite/capabilities` response
includes `missing_prereqs` so tooling can surface actionable guidance without
requiring a failed API call.
