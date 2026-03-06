# M58: Lite Pipeline Expansion and Capability Parity (Clean-Cut)

| | |
|---|---|
| **Goal** | Expand `lite` mode from minimal first-success path to a broader, explicit capability set with deterministic parity and fallback behavior |
| **Duration** | 8-10 days |
| **Dependencies** | M56 (lite infra backends), M57 (zero-config bootstrap UX), M57.1 (lite SQLite migration track), existing batch pipeline contracts |
| **Deliverable** | Lite capability matrix, profile-aware lite pipeline planner, explicit unsupported-feature semantics, and expanded integration coverage across selected stages/options |
| **Status** | Planned |

Dependency clarification:

1. M56 provides the runtime substrate (`sqlite + in-memory queue + localfs`) that M58 expands functionally.
2. M57 ensures users can access lite mode through one-command UX, which M58 broadens in scope.
3. M57.1 stabilizes schema evolution and upgrade safety so expanded M58 capabilities do not rely on ad hoc lite schema mutation paths.
4. M58 does not introduce runtime-isolation redesign; M59 remains responsible for dependency isolation profiles.

## Intended Outcomes

### Functional outcomes

1. Lite mode supports multiple predefined pipeline profiles beyond `prepare -> transcribe -> merge`.
2. Additional stage/option coverage is explicitly enabled where runtime prerequisites are satisfied.
3. Unsupported features fail fast with deterministic, actionable messages.
4. Output contracts for supported features remain API-compatible with distributed mode.
5. Default lite profile is explicit: `core`.

### Product outcomes

1. Capability boundaries are explicit and discoverable (not implied by runtime failures).
2. Users can choose predictable lite profiles based on quality/speed/features.
3. The same CLI/API request shape works across lite and distributed for supported features.

### Operational outcomes

1. Lite mode startup validates requested profile against local capabilities.
2. Feature flags and profile behavior are testable in CI.
3. Distributed mode behavior remains unchanged.
4. Capability schema is versioned and backward compatible with M56 lite artifacts/config defaults.

### Clean-start outcomes

1. No silent fallback from unsupported stage to degraded behavior.
2. No hidden stage activation based on accidental dependency availability.
3. No drift between documented and actual lite capabilities.

### Success criteria

1. Capability matrix is published and enforced at runtime.
2. At least one expanded profile (for example, diarize-enabled) works end-to-end in lite mode.
3. Unsupported profile/option combinations return deterministic validation errors.
4. Existing zero-config path remains successful and stable.
5. Capability data exposed by CLI/API/status and docs is generated from one source artifact.

---

## Strategy To Reach Outcomes

### Strategy 1: Capability matrix first, implementation second

Freeze one authoritative lite capability matrix before adding coverage. This matrix drives planner behavior, validation, and docs.

### Strategy 1.1: Single machine-readable source of truth

Define one canonical capability artifact (versioned) that is consumed by planner validation, diagnostics surfaces, and docs generation to prevent drift.

### Strategy 2: Profile-driven expansion

Expand via named profiles rather than ad hoc feature toggles:

1. `core` (default, required)
2. `speaker` (required target in M58)
3. `compliance` (conditional in M58, only if prerequisites are pre-provisioned)

### Strategy 3: Deterministic failure over implicit degradation

If a requested feature is unsupported in the selected profile/runtime, fail explicitly with remediation guidance.

### Strategy 4: Preserve shared request/output contracts

Keep API/CLI contracts aligned with distributed mode for supported features. Differences must be explicit, versioned, and documented.

### Strategy 5: Stage expansion in controlled slices

Enable additional stages incrementally with profile gates, dependency checks, and dedicated integration tests.

---

## What We Will Not Do In M58

1. Do not claim full distributed feature parity.
2. Do not auto-install heavy runtime dependencies during command execution.
3. Do not introduce hidden fallback logic that masks unsupported features.
4. Do not change distributed scheduler semantics to fit lite behavior.
5. Do not combine runtime isolation redesign (M59) into this milestone.
6. Do not claim `compliance` profile availability on fresh lite installs without preinstalled dependencies.

---

## Tactical Plan

### Phase 0: Freeze Lite Capability Matrix

1. Define supported profiles, stages, and options.
2. Define explicit unsupported combinations and error taxonomy.
3. Define profile selection precedence (CLI flag > env > default=`core`).
4. Create versioned machine-readable capability schema artifact and compatibility rules.
5. Freeze orchestrator ownership boundary for lite profile planning/execution:
   - `dalston/orchestrator/lite_main.py` is the primary lite entrypoint
   - `dalston/orchestrator/main.py` only dispatches by mode
6. Define backward-compat policy for M56 lite artifacts/config behavior.

Expected files:

- `dalston/orchestrator/lite_capabilities.py` (new, canonical capability source)
- `docs/specs/ARCHITECTURE.md`
- `docs/specs/batch/ORCHESTRATOR.md`
- `docs/specs/batch/API.md`
- `docs/specs/batch/lite-capability-matrix.md` (generated/derived)

### Phase 1: Profile-Aware Planner and Validation

1. Add lite profile resolver reading from canonical capability artifact.
2. Add planner guardrails for profile-stage-option compatibility.
3. Add deterministic validation errors with remediation hints.

Expected files:

- `dalston/orchestrator/lite_capabilities.py`
- `dalston/orchestrator/dag.py`
- `dalston/orchestrator/scheduler.py`
- `dalston/gateway/api/v1/transcription.py`
- `tests/unit/test_lite_profile_validation.py` (new)

### Phase 2: Expand Stage Coverage by Profile

1. Enable selected optional stages in lite mode based on matrix and dependencies.
2. Gate stage activation on explicit profile capability, not auto-discovery.
3. Ensure artifact contracts remain stable for expanded flows.
4. Deliver `speaker` profile as required M58 expansion target.
5. Treat `compliance` as conditional:
   - if dependencies/prereqs are present, run according to matrix
   - if missing, fail deterministically as unsupported in current environment

Expected files:

- `dalston/orchestrator/dag.py`
- `dalston/orchestrator/lite_main.py`
- `dalston/orchestrator/main.py` (mode dispatch only, if needed)
- `tests/integration/test_lite_profile_speaker_flow.py` (new)
- `tests/integration/test_lite_profile_compliance_flow.py` (new, conditional/feature-gated)

### Phase 3: CLI/API Surface Alignment

1. Add profile selection UX for CLI/API.
2. Keep default path backward compatible with M57 (`core` when unspecified).
3. Ensure messaging clearly communicates active profile and capability limits.

Expected files:

- `cli/dalston_cli/commands/transcribe.py`
- `cli/dalston_cli/output.py`
- `dalston/gateway/api/v1/transcription.py`
- `tests/unit/test_cli_lite_profile_selection.py` (new)

### Phase 4: Capability Discovery and Diagnostics

1. Add machine-readable capability endpoint/status output for lite mode.
2. Add human-readable diagnostics command/path for unsupported requests.
3. Ensure docs and runtime capability data are generated from `dalston/orchestrator/lite_capabilities.py`.

Expected files:

- `dalston/gateway/api/v1/engines.py` (or equivalent capabilities/status endpoint)
- `cli/dalston_cli/commands/status.py`
- `tests/integration/test_lite_capability_discovery.py` (new)

### Phase 5: Docs and Acceptance Gate

1. Publish lite capability matrix and profile guide.
2. Add troubleshooting for unsupported feature requests.
3. Update roadmap and implementation report.

Expected files:

- `README.md`
- `docs/README.md`
- `docs/guides/self-hosted-deployment-tutorial.md`
- `docs/reports/M58-lite-parity-expansion.md` (new)

---

## Testing Plan

### Automated tests

1. Unit tests:
   - profile resolution
   - matrix validation
   - deterministic error mapping
   - compatibility loading for previous M56 defaults/artifacts
2. Integration tests:
   - expanded profile happy paths
   - unsupported combinations fail with expected errors
   - zero-config default profile non-regression
   - compliance profile deterministic "unsupported in environment" path when deps are absent
3. Regression tests:
   - distributed mode unaffected
   - API response schemas unchanged for shared features

Suggested command sets:

```bash
pytest tests/unit/test_lite_profile_validation.py \
       tests/unit/test_cli_lite_profile_selection.py \
       tests/integration/test_lite_profile_speaker_flow.py \
       tests/integration/test_lite_profile_compliance_flow.py \
       tests/integration/test_lite_capability_discovery.py -q
```

```bash
pytest -q
```

### Manual verification

1. Run default M57 flow and confirm unchanged success path.
2. Run at least one expanded profile and verify end-to-end output.
3. Trigger unsupported option/profile combination and verify actionable error.
4. Validate capability docs and status output match canonical capability artifact.

---

## Execution Estimates

1. Phase 0: 1-2 days
2. Phase 1: 2 days
3. Phase 2: 2-3 days
4. Phase 3: 1 day
5. Phase 4: 1 day
6. Phase 5: 1 day

---

## Exit Criteria

1. Lite capability matrix is implemented and enforced.
2. Expanded lite profiles are validated by integration tests.
3. Unsupported features fail deterministically with clear guidance.
4. M57 zero-config path remains stable.
