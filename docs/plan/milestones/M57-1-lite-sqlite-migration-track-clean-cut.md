# M57.1: Unified Alembic Startup for Lite Mode (Clean-Cut)

| | |
|---|---|
| **Goal** | Wire the single Alembic migration chain to run against SQLite at lite-mode startup, replace the ad-hoc `_init_lite_schema()` bootstrap, and close M57 carry-forward safety issues |
| **Duration** | 3-5 days |
| **Dependencies** | M57.0 (dialect-neutral schema normalization), M57 (zero-config bootstrap) |
| **Deliverable** | Startup migration gate for `DALSTON_MODE=lite` using the shared Alembic revision chain, removal of `_init_lite_schema()`, legacy DB upgrade path, CI drift coverage, and closure of M57 carry-forward issues (#2, #3, #6, #7, #10, #12) |
| **Status** | Planned |

## Relationship to M57.0

M57.0 made the ORM models dialect-portable via `TypeDecorator` adapters
(`UUIDType`, `JSONType`, `InetType`) and replaced `ARRAY` columns with junction
tables.  After M57.0, Alembic migrations render correct DDL for both Postgres
and SQLite from a single revision chain.

**M57.1 is now purely an operational milestone** — it wires that existing
migration chain into the lite startup path and removes the legacy hand-rolled
schema bootstrap.  There is no longer a need to:

- Choose between a separate lite migration track vs. a shared one (decided: shared).
- Map Postgres types to SQLite equivalents (handled by `TypeDecorator`).
- Maintain parallel DDL in `_init_lite_schema()`.

---

## Intended Outcomes

### Functional outcomes

1. Lite mode upgrades schema automatically between releases without requiring DB deletion.
2. Existing user `~/.dalston/lite.db` files created by older milestones remain upgradeable.
3. Fresh `DALSTON_MODE=lite` startup runs `alembic upgrade head` against SQLite before serving requests.
4. Startup fails fast with explicit remediation when migration cannot be applied safely.

### Architecture outcomes

1. One migration chain, one model set, two dialects — no fork, no drift.
2. `_init_lite_schema()` and all inline DDL helpers (`_ensure_sqlite_columns`,
   `_SQLITE_BOOTSTRAP_TABLES`, `_SQLITE_IDENTIFIER_RE`, `_SQLITE_COLUMN_DDL_RE`)
   are removed from `session.py`.
3. Lite and distributed modes share the same Alembic entrypoint, differing only
   in the `DATABASE_URL` passed to the migration runner.

### Operational outcomes

1. Zero-config CLI flow remains stable across upgrades.
2. Schema drift between lite and distributed is impossible — same migrations.
3. Downgrade/rollback behavior is explicit: `alembic downgrade` works on both dialects.

### Clean-start outcomes

1. No silent engine_id 500s caused by missing columns/tables on older lite DBs.
2. No hidden schema mutations outside the Alembic migration path.
3. No requirement to manually delete local DB state as a standard upgrade step.

### M57 carry-forward closure outcomes

1. Bootstrap lock stale-reclaim path is race-safe under concurrent local invocations (#2).
2. Unsafe SQLite DDL composition patterns are removed entirely — Alembic handles all DDL (#3).
3. Lite inline pipeline execution path avoids event-loop blocking behavior (#6).
4. Lite-mode job creation always emits an audit record, including failure paths (#7).
5. CLI bootstrap-disabled prerequisite helper uses explicit client typing (#10).
6. Gateway rate limiter dependency exposes an accurate shared return type without suppression (#12).

### Success criteria

1. Fresh lite DB initializes to latest schema revision via `alembic upgrade head`.
2. Legacy minimal lite DB (pre-M57.0) upgrades to current revision and passes lite
   transcribe integration tests.
3. CI runs `alembic upgrade head` against both Postgres and SQLite — same revision chain.
4. `_init_lite_schema()` and all SQLite DDL helper code is deleted from `session.py`.
5. M57 zero-config path remains green after migration integration.
6. Lock stale-reclaim behavior is covered by contention-focused unit tests.
7. Lite transcription failure path still records `log_job_created` audit event.
8. Type-check/lint passes without return-type suppression in rate limiter dependency flow.

---

## Strategy

### Strategy 1: Alembic as the single schema owner

Both `DALSTON_MODE=distributed` and `DALSTON_MODE=lite` call the same
`alembic upgrade head` at startup, differing only in `DATABASE_URL`.  M57.0
already proved this works in CI.  M57.1 integrates it into the application
startup sequence.

### Strategy 2: Programmatic Alembic invocation

Use `alembic.command.upgrade()` with `alembic.config.Config` programmatically
in the startup path, rather than shelling out.  This keeps the migration in-process,
avoids subprocess overhead, and allows proper error handling.

### Strategy 3: Legacy DB bridging migration

Create one special migration revision that detects a pre-M57.0 lite DB (by
checking for the presence of old-style TEXT columns where junction tables now
exist) and backfills/reshapes data into the normalized schema.  This revision
is a no-op on fresh databases and on Postgres (which was migrated in M57.0).

### Strategy 4: Deterministic startup gate

Migration runs after settings resolution but before any service (gateway,
orchestrator) starts accepting work.  If migration fails, the process exits
with an actionable error message — no partial startup.

---

## What We Will Not Do In M57.1

1. Do not introduce a second migration track — M57.0 eliminated the need.
2. Do not change ORM models or column types — that was M57.0's scope.
3. Do not expand lite feature scope beyond migration reliability, except
   targeted M57 carry-forward closures (#2, #3, #6, #7, #10, #12).
4. Do not change API request/response contracts.
5. Do not bundle engine_id-isolation or packaging scope (M59/M60).

---

## Tactical Plan

### Phase 1: Programmatic Migration Runner

1. Create `dalston/db/migrate.py` — thin wrapper around `alembic.command.upgrade()`:
   - Accepts `database_url` parameter.
   - Configures Alembic `Config` programmatically (script location, URL override).
   - Returns migration result (current revision, applied count).
   - Raises typed exceptions for failure cases.
2. Add SQLite-specific pre-flight check:
   - Verify SQLite version ≥ 3.35.0 (required for `RETURNING`).
   - Verify DB file is writable.
   - Verify no other process holds the WAL lock (relevant for concurrent CLI invocations).
3. Write unit tests for the migration runner against an in-memory SQLite database.

Expected files:

- `dalston/db/migrate.py` (new)
- `tests/unit/test_db_migrate.py` (new)

### Phase 2: Startup Integration

1. Replace `_init_lite_schema()` call in lite startup path with
   `dalston.db.migrate.upgrade_to_head()`.
2. Replace `_init_lite_schema()` call in distributed startup path with
   `dalston.db.migrate.upgrade_to_head()` (optional — distributed mode may
   continue using external `alembic upgrade head` if preferred).
3. Define startup order:
   - Settings resolution (`get_settings()`)
   - Migration check/upgrade (`upgrade_to_head()`)
   - Engine/session factory initialization
   - Service startup (gateway / orchestrator)
4. Add explicit error taxonomy:
   - `MigrationLockError` — another process is migrating (concurrent CLI).
   - `MigrationCorruptError` — DB file is corrupt or unrecognized.
   - `MigrationVersionError` — DB is ahead of code (downgrade needed).
   - `MigrationError` — base class for all migration failures.
5. Map errors to actionable startup messages with recovery instructions.

Expected files:

- `dalston/db/session.py` (remove `_init_lite_schema` and all DDL helpers)
- `dalston/gateway/main.py`
- `dalston/orchestrator/lite_main.py`

### Phase 3: Legacy DB Upgrade Bridge

1. Create a bridge migration revision that handles pre-M57.0 lite databases:
   - Detect legacy schema (e.g., `parameters` column as TEXT on `jobs`, no
     junction tables).
   - Reshape data into normalized M57.0 schema (backfill junction tables from
     JSON-encoded arrays, extract `parameters` keys into typed columns).
   - This revision is idempotent — safe to run on databases already at M57.0 schema.
2. Create test fixtures:
   - `tests/fixtures/legacy_lite_m56.db` — minimal lite DB from M56 era.
   - `tests/fixtures/legacy_lite_m57.db` — lite DB from M57 (pre-normalization).
3. Write upgrade-path integration tests that apply migrations to legacy fixtures
   and verify data integrity.

Expected files:

- `alembic/versions/NNNN_bridge_legacy_lite_schema.py`
- `tests/fixtures/legacy_lite_m56.db`
- `tests/fixtures/legacy_lite_m57.db`
- `tests/integration/test_lite_db_migration_upgrade.py` (new)

### Phase 4: Remove Legacy Bootstrap Code

1. Delete from `dalston/db/session.py`:
   - `_init_lite_schema()` function
   - `_ensure_sqlite_columns()` helper
   - `_SQLITE_BOOTSTRAP_TABLES` allowlist
   - `_SQLITE_IDENTIFIER_RE` regex
   - `_SQLITE_COLUMN_DDL_RE` regex
   - All inline `CREATE TABLE` / `ALTER TABLE` SQL strings
2. Update `_build_engine()` to no longer call `_init_lite_schema()` after
   engine creation.
3. Verify that `tests/unit/test_db_session_sqlite_guards.py` tests are either
   migrated to test the new migration runner or removed (the guards they tested
   no longer exist).

Expected files:

- `dalston/db/session.py`
- `tests/unit/test_db_session_sqlite_guards.py` (remove or rewrite)

### Phase 5: M57 Carry-Forward Hardening Closures

1. Close bootstrap lock stale-reclaim race window and verify contention timeout behavior (#2).
2. Ensure lite inline execution path is non-blocking in async request flow (thread offload where required) (#6).
3. Ensure audit logging parity for lite success/failure branches (#7).
4. Type the CLI bootstrap-disabled prerequisite client surface explicitly (#10).
5. Introduce shared rate-limiter typing contract and remove return-type suppression (#12).

Note: Issue #3 (unsafe SQLite DDL composition) is resolved by Phase 4 — the
DDL helpers are deleted entirely.

Expected files:

- `cli/dalston_cli/bootstrap/server_manager.py`
- `dalston/gateway/api/v1/transcription.py`
- `dalston/gateway/dependencies.py`
- `cli/dalston_cli/commands/transcribe.py`
- `tests/unit/test_cli_server_manager.py`
- `tests/unit/test_gateway_dependencies_auth_none.py`
- `tests/integration/test_openai_api.py`

### Phase 6: CI Integration and Exit Gate

1. Add/update CI job: `alembic upgrade head` against empty SQLite (already added
   in M57.0 — verify it covers the bridge migration).
2. Add CI job: apply migrations to legacy lite fixtures and run schema assertions.
3. Add CI job: fresh lite startup → transcription → verify output.
4. Run full test suite.

Suggested command sets:

```bash
pytest tests/integration/test_lite_db_bootstrap.py \
       tests/integration/test_lite_db_migration_upgrade.py \
       tests/integration/test_cli_zero_config_transcribe.py -q
```

```bash
pytest -q tests/unit/test_cli_server_manager.py \
          tests/unit/test_db_migrate.py \
          tests/unit/test_gateway_dependencies_auth_none.py \
          tests/unit/test_engine_selector.py \
          tests/integration/test_openai_api.py
```

---

## Risks and Mitigations

1. **Risk:** Legacy lite DBs have data that cannot be cleanly reshaped into
   normalized schema (e.g., malformed JSON in `parameters`).
   - **Mitigation:** Bridge migration logs warnings for un-parseable rows and
     sets columns to defaults rather than failing.  Integration tests cover
     known legacy shapes.

2. **Risk:** Concurrent CLI invocations race on Alembic migration.
   - **Mitigation:** SQLite WAL mode + Alembic's `alembic_version` table provide
     basic serialization.  Add explicit file-lock around migration invocation
     with configurable timeout and clear error message.

3. **Risk:** Startup latency regression from migration checks.
   - **Mitigation:** No-op fast path when already at head revision (single
     `SELECT version_num FROM alembic_version` check).  Measure startup time
     in integration tests.

4. **Risk:** Removing `_init_lite_schema()` breaks existing lite users who
   haven't upgraded through M57.0.
   - **Mitigation:** The bridge migration (Phase 3) handles this — it detects
     pre-M57.0 schema and reshapes it.  Users upgrading from any previous
     version get automatic migration.

---

## Follow-On Milestones

1. M58 consumes the unified migration contract for expanded lite capability coverage.
2. M59/M60 rely on stable schema evolution guarantees from M57.1.
