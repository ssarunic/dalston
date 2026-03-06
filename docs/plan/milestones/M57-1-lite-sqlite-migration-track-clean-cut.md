# M57.1: Lite SQLite Migration Track and Schema Compatibility (Clean-Cut)

| | |
|---|---|
| **Goal** | Replace ad hoc lite SQLite schema drift handling with a versioned migration track and deterministic upgrade contract |
| **Duration** | 4-7 days |
| **Dependencies** | M56 (lite infra backends), M57 (zero-config bootstrap), M47 (SQL layer separation) |
| **Deliverable** | SQLite migration strategy (Alembic-backed or equivalent versioned runner), startup migration gate for `DALSTON_MODE=lite`, legacy DB upgrade path, CI drift/upgrade coverage, and closure of M57 carry-forward safety/type issues (#2, #3, #6, #7, #10, #12) |
| **Status** | Planned |

Dependency clarification:

1. M56 introduced lite-mode schema bootstrap but explicitly deferred full migration parity.
2. M57 increased real-world lite usage, exposing schema drift risks sooner in first-run and repeat-run flows.
3. M47 service boundaries make migration ownership enforceable without handler-level SQL changes.

## Intended Outcomes

### Functional outcomes

1. Lite mode upgrades schema automatically between releases without requiring DB deletion.
2. Existing user `~/.dalston/lite.db` files created by older milestones remain upgradeable.
3. Required lite-path tables/columns/indexes are versioned and enforced before request handling.
4. Startup fails fast with explicit remediation when migration cannot be applied safely.

### Architecture outcomes

1. Lite schema evolution becomes append-only and versioned, not scattered `CREATE/ALTER` logic.
2. Distributed (Postgres) migration path remains unchanged and isolated.
3. One documented ownership model exists for lite schema changes.

### Operational outcomes

1. Zero-config CLI flow remains stable across upgrades.
2. Schema drift is detected in CI before release.
3. Downgrade/rollback behavior is explicit for lite runtime operators.

### Clean-start outcomes

1. No silent runtime 500s caused by missing columns/tables on older lite DBs.
2. No hidden schema mutations outside the migration path.
3. No requirement to manually delete local DB state as a standard upgrade step.

### M57 carry-forward closure outcomes

1. Bootstrap lock stale-reclaim path is race-safe under concurrent local invocations (#2).
2. Lite SQLite DDL helper path is constrained to explicit allowlists/validated identifiers (#3).
3. Lite inline pipeline execution path avoids event-loop blocking behavior (#6).
4. Lite-mode job creation always emits an audit record, including failure paths (#7).
5. CLI bootstrap-disabled prerequisite helper uses explicit client typing (#10).
6. Gateway rate limiter dependency exposes an accurate shared return type without suppression (#12).

### Success criteria

1. Fresh lite DB initializes to latest lite schema revision through migration entrypoint.
2. Legacy minimal lite DB upgrades to current revision and passes lite transcribe integration tests.
3. CI includes migration upgrade tests (baseline, N-1, legacy minimal fixture).
4. M57 zero-config path remains green after migration-track integration.
5. Lock stale-reclaim behavior is covered by contention-focused unit tests.
6. Lite DDL helper safeguards are covered by unit tests for rejected unsafe identifiers/DDL.
7. Lite transcription failure path still records `log_job_created` audit event.
8. Type-check/lint passes without return-type suppression in rate limiter dependency flow.

---

## Strategy To Reach Outcomes

### Strategy 1: Versioned migration contract for lite mode

Use a dedicated lite migration history (Alembic branch label or equivalent versioned migration runner) with a clear schema version table and append-only revisions.

### Strategy 2: Separate migration ownership by runtime mode

Keep distributed migration behavior as-is. Lite mode runs lite migration history only when `DALSTON_MODE=lite`.

### Strategy 3: Deterministic startup gate

Run lite migration check/upgrade during startup before serving requests. If migration fails, fail process startup with actionable message.

### Strategy 4: Legacy upgrade first

Treat previously bootstrapped minimal lite DB files as first-class upgrade targets, not best-effort edge cases.

### Strategy 5: Drift detection in CI

Add automated checks that compare expected lite schema contract against migrated DB state and catch missing migration coverage.

---

## What We Will Not Do In M57.1

1. Do not port every historical Postgres migration 1:1 to SQLite.
2. Do not redesign distributed migration tooling.
3. Do not expand lite feature scope beyond schema/migration reliability, except targeted M57 carry-forward closures (#2, #3, #6, #7, #10, #12).
4. Do not change API request/response contracts in this milestone.
5. Do not bundle runtime-isolation or packaging scope (M59/M60).

---

## Tactical Plan

### Phase 0: Freeze Lite Migration Contract

1. Choose and document migration mechanism for lite:
   - preferred: Alembic-supported lite branch/history
   - acceptable fallback: dedicated versioned SQLite migration runner with equivalent guarantees
2. Define lite schema ownership and revision policy (append-only, no in-place rewrite of released revisions).
3. Define startup order:
   - settings resolution
   - migration check/upgrade
   - service startup

Expected files:

- `alembic/` (lite migration branch or lite migration config extensions)
- `dalston/db/session.py`
- `docs/specs/ARCHITECTURE.md`
- `docs/specs/batch/ORCHESTRATOR.md`

### Phase 1: Baseline and Legacy Upgrade Migrations

1. Create lite baseline revision representing required M57 runtime schema contract.
2. Add forward migrations that upgrade legacy minimal lite schema (M56 bootstrap subset) to current contract.
3. Ensure migration scripts include deterministic defaults/backfills for new NOT NULL fields.

Expected files:

- `alembic/versions/*` (or `dalston/db/lite_migrations/*` if fallback runner)
- `tests/integration/test_lite_db_bootstrap.py`
- `tests/integration/test_lite_db_migration_upgrade.py` (new)

### Phase 2: Startup Integration and Safety Controls

1. Replace schema-mutating bootstrap hot path with migration invocation in lite startup path.
2. Keep bootstrap limited to initial DB creation and connection prerequisites.
3. Add explicit error taxonomy for migration failures (corrupt DB, lock timeout, unsupported version).

Expected files:

- `dalston/db/session.py`
- `dalston/gateway/main.py`
- `dalston/orchestrator/lite_main.py`

### Phase 2B: M57 Carry-Forward Hardening Closures

1. Close bootstrap lock stale-reclaim race window and verify contention timeout behavior (#2).
2. Remove unsafe SQLite DDL composition patterns with explicit table/identifier/DDL guards (#3).
3. Ensure lite inline execution path is non-blocking in async request flow (thread offload where required) (#6).
4. Ensure audit logging parity for lite success/failure branches (#7).
5. Type the CLI bootstrap-disabled prerequisite client surface explicitly (#10).
6. Introduce shared rate-limiter typing contract and remove return-type suppression (#12).

Expected files:

- `cli/dalston_cli/bootstrap/server_manager.py`
- `dalston/db/session.py`
- `dalston/gateway/api/v1/transcription.py`
- `dalston/gateway/dependencies.py`
- `cli/dalston_cli/commands/transcribe.py`
- `tests/unit/test_cli_server_manager.py`
- `tests/unit/test_db_session_sqlite_guards.py`
- `tests/unit/test_gateway_dependencies_auth_none.py`
- `tests/integration/test_openai_api.py`

### Phase 3: Tooling and Diagnostics

1. Add developer/operator command(s) for lite migration status/check.
2. Add migration state to diagnostics surfaces where applicable.
3. Document recovery procedures (backup, retry, rebuild path).

Expected files:

- `cli/dalston_cli/commands/server.py` (or dedicated db command group)
- `docs/guides/self-hosted-deployment-tutorial.md`
- `docs/guides/installation.md`

### Phase 4: Regression Coverage and Exit Gate

1. Add upgrade-path integration tests from legacy lite DB fixtures.
2. Add schema drift assertions in CI for lite mode.
3. Run full M57 path tests to confirm no bootstrap regression.

Suggested command sets:

```bash
pytest tests/integration/test_lite_db_bootstrap.py \
       tests/integration/test_lite_db_migration_upgrade.py \
       tests/integration/test_cli_zero_config_transcribe.py -q
```

```bash
pytest -q tests/unit/test_cli_server_manager.py \
          tests/unit/test_db_session_sqlite_guards.py \
          tests/unit/test_gateway_dependencies_auth_none.py \
          tests/unit/test_engine_selector.py \
          tests/integration/test_openai_api.py
```

---

## Risks and Mitigations

1. **Risk:** SQLite migration path diverges from actual lite runtime schema usage.
   - **Mitigation:** schema contract snapshot + drift tests in CI.
2. **Risk:** Legacy local DBs fail upgrade in user environments.
   - **Mitigation:** explicit legacy fixtures and upgrade tests for known historical schema shapes.
3. **Risk:** Startup latency regression from migration checks.
   - **Mitigation:** no-op fast path when already at head revision; measure startup in integration tests.

---

## Follow-On Milestones

1. M58 should consume the M57.1 migration contract as prerequisite for expanded lite capability coverage.
2. M59/M60 should rely on stable lite schema evolution guarantees from M57.1.
