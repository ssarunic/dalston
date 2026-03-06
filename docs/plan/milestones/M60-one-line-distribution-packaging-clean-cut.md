# M60: One-Line Distribution and Packaging (Clean-Cut)

| | |
|---|---|
| **Goal** | Deliver installation/distribution experience that matches zero-config UX goals across supported platforms |
| **Duration** | 6-10 days |
| **Dependencies** | M57 (zero-config CLI bootstrap), M59 (runtime isolation profiles) |
| **Deliverable** | Distribution channels (pip + installer/binary path as scoped), one-line install bootstrap, reproducible artifacts, and cross-platform smoke validation |
| **Status** | Planned |

Dependency clarification:

1. M57 defines desired first-run behavior; M60 packages and distributes that behavior safely.
2. M59 stabilizes runtime execution profiles so packaging targets and prerequisites are explicit.
3. M58 capability matrix remains a transitive input to docs/claims for packaged lite behavior.
4. Release automation baseline is delivered inside M60 (Phase 3), not an external prerequisite.

## Intended Outcomes

### Functional outcomes

1. Users can install and run Dalston via one-line, documented install paths.
2. First-run command flow works after install without manual infra setup for scoped local mode.
3. Distribution artifacts are reproducible, versioned, and integrity-checked.
4. Upgrade and rollback behavior is deterministic.
5. Multi-package publish chain is coordinated (`dalston`, `dalston-sdk`, `dalston-cli`) with explicit compatibility policy.

### Product outcomes

1. Install experience is consistent with README quick-start promise.
2. Channel matrix is explicit (what is supported via pip vs packaged binary/installer).
3. Troubleshooting and diagnostics for install/bootstrap failures are documented.

### Operational outcomes

1. Release pipeline produces signed/checksummed artifacts.
2. Cross-platform smoke tests gate releases.
3. Packaging does not regress distributed deployment paths.
4. Release publishing is gated on existing CI workflow success.

### Clean-start outcomes

1. No undocumented post-install manual steps for the scoped quick path.
2. No ambiguous/implicit dependency acquisition behavior.
3. No channel-specific behavior drift without documentation.

### Success criteria

1. One-line install instructions work on supported OS targets.
2. Post-install `dalston transcribe <file>` passes smoke test in CI on supported targets.
3. Release artifacts include provenance and integrity metadata.
4. Existing developer/install workflows remain functional.
5. Published package versions are coordinated across `dalston`, `dalston-sdk`, and `dalston-cli`.

---

## Strategy To Reach Outcomes

### Strategy 1: Channel matrix first

Freeze supported channels and platform scope before implementation:

1. pip/wheel path (mandatory)
2. optional binary/installer path (scoped by platform readiness)
3. runtime scope per channel (`lite core` first-run path; distributed path documented separately)

### Strategy 2: Reproducible packaging pipeline

Use pinned build inputs, deterministic versioning, and artifact checksums/signatures.

### Strategy 3: Keep post-install bootstrap explicit

Install flow should include a deterministic first-run verification step and diagnostics output.

### Strategy 4: Separate packaging from runtime behavior

M60 packages stable runtime behavior; it does not redefine server/model bootstrap contracts.

### Strategy 5: Release-gated smoke validation

No artifact publish without passing install + first-run smoke tests on supported matrix.

### Strategy 6: Channel-specific lifecycle semantics

Define upgrade/rollback semantics separately per channel:

1. pip channel (`pip install --upgrade`, pinned-version rollback)
2. installer/binary channel (versioned install directory + explicit rollback command/path)

---

## What We Will Not Do In M60

1. Do not expand feature scope beyond packaged distribution concerns.
2. Do not add unsupported platform claims without CI smoke coverage.
3. Do not rely on unsigned/unverified release artifacts.
4. Do not merge runtime isolation redesign work into packaging implementation.
5. Do not deprecate existing developer install paths without migration guidance.

---

## Tactical Plan

### Phase 0: Freeze Distribution Matrix and Release Policy

1. Define supported OS/arch matrix per channel.
2. Freeze installer scope for first release:
   - installs CLI plus required local server runtime for M57 lite-core first-run path
   - no claim of full distributed runtime provisioning
3. Define coordinated release policy for multi-package chain:
   - package set: `dalston`, `dalston-sdk`, `dalston-cli`
   - versioning policy and publish order (`dalston-sdk` + `dalston` before `dalston-cli`)
4. Resolve Python version floor mismatch and freeze supported interpreter baseline for one-line channel (`>=3.11`).
5. Define artifact naming/versioning and integrity/provenance requirements.
6. Define upgrade/rollback policy and support window by channel.
7. Define docs split to avoid overlap:
   - `docs/guides/installation.md` = one-line pip/installer path
   - `docs/guides/self-hosted-deployment-tutorial.md` = Docker/distributed deployment path

Expected files:

- `pyproject.toml`
- `cli/pyproject.toml`
- `sdk/pyproject.toml`
- `README.md`
- `docs/README.md`
- `docs/guides/self-hosted-deployment-tutorial.md`
- `docs/guides/installation.md` (new)

### Phase 1: Pip/Wheel Channel Hardening

1. Ensure package extras and dependency constraints align with M59 profile support for the scoped channel:
   - define minimal end-user runtime extra/profile set required for one-line local flow
   - keep advanced profile extras explicit and optional
2. Ensure CLI install path includes functional local-server bootstrap prerequisites for M57 path (not CLI-only proxy behavior).
3. Align/freeze Python version constraints across released packages for supported channel.
4. Add install-time/post-install diagnostics command.
5. Add pip install smoke test ownership contract:
   - root `tests/integration/*` owns install + first-run smoke
   - `cli/tests/*` remains package-local unit/command behavior validation
6. Add first-run smoke command to CI for pip installation path.

Expected files:

- `pyproject.toml`
- `cli/pyproject.toml`
- `sdk/pyproject.toml`
- `tests/integration/test_install_pip_smoke.py` (new)
- `.github/workflows/ci.yml`

### Phase 2: Installer/Binary Channel (Scoped)

1. Implement installer script and/or packaged binary build path for selected targets.
2. Ensure install places CLI and required runtime assets predictably for scoped lite-core bootstrap.
3. Add uninstall/cleanup semantics.
4. Add explicit upgrade + rollback semantics for installer/binary channel.

Expected files:

- `scripts/install.sh` (new)
- `scripts/uninstall.sh` (new)
- `docs/guides/installation.md` (new)
- CI workflow files for packaging (new/updated)

### Phase 3: Release Automation and Provenance

1. Automate multi-package build and publication with explicit order and compatibility checks.
2. Automate checksum/signature generation and publication manifests.
3. Freeze provenance/SBOM toolchain for verifiable exit criteria:
   - GitHub artifact attestations (OIDC-based)
   - CycloneDX SBOM generation for published artifacts
4. Gate release promotion on:
   - existing CI workflow success
   - matrix smoke success for install + first-run path

Expected files:

- `.github/workflows/release*.yml`
- `.github/workflows/ci.yml` (release gate integration, if needed)
- `docs/reports/M60-distribution-release-readiness.md` (new)

### Phase 4: Documentation and Migration Guidance

1. Update quick-start snippets to one-line install + one-command transcribe.
2. Add troubleshooting by install channel, including first-run model auto-pull behavior.
3. Add migration notes for existing users.
4. Keep installation and deployment docs non-overlapping and cross-linked.
5. Ensure docs only claim capabilities present in current lite matrix/profile scope.

Expected files:

- `README.md`
- `docs/README.md`
- `docs/guides/installation.md`
- `docs/plan/README.md`

---

## Testing Plan

### Automated tests

1. Packaging tests:
   - wheel/sdist build/install for `dalston`, `dalston-sdk`, `dalston-cli`
   - CLI invocation sanity
2. Cross-platform smoke tests:
   - install command succeeds
   - `dalston --version` works
   - first-run transcribe smoke path works on supported targets (including default model auto-ensure path from M57)
3. Release pipeline tests:
   - checksum/signature verification
   - SBOM/provenance artifact presence and verification
   - artifact manifest completeness

Suggested command sets:

```bash
pytest tests/integration/test_install_pip_smoke.py -q
```

```bash
pytest -q
```

### Manual verification

1. Fresh machine install on each supported channel and platform.
2. Upgrade from previous release and verify backward-compatible behavior.
3. Rollback to previous version works per channel contract.
4. Uninstall/cleanup path leaves no broken shell bindings or stale service state.

---

## Exit Criteria

1. Supported distribution channels are implemented and documented.
2. Cross-platform install + first-run smoke tests are release-gated.
3. Artifacts are reproducible and integrity-verified.
4. Quick-start docs reflect real, validated install/run path.
5. Multi-package release coordination and channel-specific upgrade/rollback behavior are documented and validated.
