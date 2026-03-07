# Implementation Prompt: M57.0 + M57.1 — Dialect-Neutral Schema & Unified Lite Migration

## Role

You are a senior software engineer implementing two tightly coupled milestones for the Dalston project. You have deep expertise in SQLAlchemy (async), Alembic migrations, Python 3.11+, and dual-dialect database portability (PostgreSQL + SQLite). You write production-quality code that follows existing project conventions exactly.

---

## Project Context

Dalston is a modular, self-hosted audio transcription server. It runs in two modes:

- **`distributed`** — PostgreSQL, Redis, containerized engines, multi-process
- **`lite`** — SQLite, in-process, zero-config CLI bootstrap

Today the ORM models in `dalston/db/models.py` import Postgres-specific types (`PG_UUID`, `JSONB`, `ARRAY`, `INET`) directly. The lite path maintains a separate hand-rolled DDL bootstrap (`_init_lite_schema()` in `dalston/db/session.py`) that manually maps types to SQLite equivalents. This creates drift risk and duplicated maintenance.

**Your job:** Execute M57.0 (make models dialect-portable) and M57.1 (wire Alembic to SQLite at startup, remove legacy bootstrap).

---

## Constraints

1. **Do not change API request/response contracts.** No visible behavior change to API consumers.
2. **Do not introduce a second Alembic migration track.** One revision chain targets both dialects.
3. **Postgres must retain native types** — UUID (16-byte), JSONB (indexable), INET (validated) — via `TypeDecorator.load_dialect_impl()`.
4. **SQLite minimum version: 3.35.0** (for `RETURNING` support). Python 3.11+ ships 3.39+.
5. **Existing tests must pass** without modification beyond adapting to new column names from the `jobs.parameters` flattening.
6. **Follow existing code conventions** — structlog logging, Pydantic settings, async-first, no fire-and-forget tasks.
7. **Alembic revision IDs** follow the pattern: `revision = "NNNN"` with sequential numbering. The current latest is `revision = "0032"`, `down_revision = "0031"`. Continue from `"0033"`.
8. **Alembic file naming**: `YYYYMMDD_HHMM_NNNN_slug.py`. Use today's date.
9. **Never mix Docker and local processes** for the same service.
10. **All mutations must be idempotent.** Migrations must be safe to re-run.

---

## Current Codebase State (Exact Code)

### `dalston/db/models.py` — Current Postgres-coupled imports

```python
from sqlalchemy import (
    ARRAY, TIMESTAMP, BigInteger, Boolean, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
```

### Postgres-specific columns across models (exhaustive list)

**UUID columns (28 total)** — All PKs and FKs use `PG_UUID(as_uuid=True)` with `server_default=func.gen_random_uuid()`:
- `AuditLogModel.tenant_id`
- `TenantModel.id`
- `JobModel.id`, `.tenant_id`, `.created_by_key_id`
- `TaskModel.id`, `.job_id`
- `APIKeyModel.id`, `.tenant_id`, `.created_by_key_id`
- `WebhookEndpointModel.id`, `.tenant_id`, `.created_by_key_id`
- `WebhookDeliveryModel.id`, `.endpoint_id`, `.job_id`
- `ArtifactObjectModel.id`, `.tenant_id`, `.owner_id`
- `SettingModel.id`, `.tenant_id`, `.updated_by`
- `RealtimeSessionModel.id`, `.tenant_id`, `.previous_session_id`, `.created_by_key_id`

**JSONB columns (8 total)**:
- `jobs.parameters` — **Flatten** (known keys used in `dag.py`)
- `tenants.settings` — Keep as `JSONType`
- `tasks.config` — Keep as `JSONType`
- `audit_log.detail` — Keep as `JSONType`
- `webhook_deliveries.payload` — Keep as `JSONType`
- `settings.value` — Keep as `JSONType`
- `models.languages` — **Normalize** to `model_languages` junction table
- `models.model_metadata` — Keep as `JSONType`

**ARRAY columns (4 total)**:
- `jobs.pii_entity_types` → `ARRAY(String)` → Junction table `job_pii_entity_types`
- `tasks.dependencies` → `ARRAY(PG_UUID(as_uuid=True))` → Junction table `task_dependencies`
- `webhook_endpoints.events` → `ARRAY(String)` → Junction table `webhook_endpoint_events`
- `artifact_objects.compliance_tags` → `ARRAY(String)` → Junction table `artifact_compliance_tags`

**INET column (1)**:
- `audit_log.ip_address` → `InetType`

### `dalston/db/session.py` — Legacy bootstrap (to be removed in M57.1)

Contains `_init_lite_schema()` (lines 157-363) with hand-rolled `CREATE TABLE IF NOT EXISTS` statements, `_ensure_sqlite_columns()` for backward-compat column additions, and regex-based DDL validation (`_SQLITE_IDENTIFIER_RE`, `_SQLITE_COLUMN_DDL_RE`, `_SQLITE_BOOTSTRAP_TABLES`).

`init_db()` dispatches:
```python
async def init_db() -> None:
    settings = get_settings()
    if settings.runtime_mode == "lite":
        await _init_lite_schema()
    else:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await _ensure_default_tenant()
```

### `alembic/env.py` — Current (Postgres-only)

Currently only handles `postgresql://` → `postgresql+asyncpg://` conversion. Needs to also handle `sqlite://` → `sqlite+aiosqlite://` and render `TypeDecorator` subclasses correctly.

### `alembic.ini`

```ini
sqlalchemy.url = postgresql+asyncpg://dalston:password@localhost:5432/dalston
```

### Dialect-specific query patterns (exact locations)

**B1. `ARRAY.any()` — `dalston/gateway/services/webhook_endpoints.py:451-452`**
```python
WebhookEndpointModel.events.any(event_type)
| WebhookEndpointModel.events.any("*"),
```
After normalization → JOIN on `webhook_endpoint_events` table.

**B2. `on_conflict_do_nothing` — `dalston/orchestrator/delivery.py:343-348`**
```python
stmt = (
    insert(WebhookDeliveryModel)
    .values(**values)
    .on_conflict_do_nothing()
    .returning(WebhookDeliveryModel.id)
)
```
→ Dialect helper: Postgres uses `ON CONFLICT DO NOTHING`, SQLite uses `INSERT OR IGNORE`.

**B3. `.returning()` — 4 locations**
- `delivery.py:347` — `WebhookDeliveryModel.id`
- `handlers.py:377` — `TaskModel.stage`
- `handlers.py:527` — `TaskModel.id`
- `realtime_sessions.py:216` — `RealtimeSessionModel`

SQLite 3.35+ supports `RETURNING`. **No change needed** — just document minimum version.

**B4. `FOR UPDATE SKIP LOCKED` — `dalston/orchestrator/delivery.py:97`**
```python
.with_for_update(skip_locked=True)
```
→ Dialect helper: emit on Postgres, no-op on SQLite (single-writer).

**B5. Raw `INTERVAL` cast — `dalston/gateway/services/artifacts.py:64-66`**
```python
SET purge_after = CAST(:available_at AS TIMESTAMPTZ)
                + CAST(ttl_seconds || ' seconds' AS INTERVAL)
```
→ Compute `purge_after` in Python, pass as bound parameter.

### `dalston/orchestrator/dag.py` — How `parameters` dict is consumed

`dag.py` receives `parameters: dict` and reads keys via `parameters.get("key")` / `parameters["key"]`:
- `word_timestamps`, `timestamps_granularity`, `speaker_detection`
- `num_speakers`, `min_speakers`, `max_speakers`
- `exclusive`, `language`, `beam_size`, `vad_filter`
- `transcribe_config`, `num_channels`
- `pii_detection`, `pii_entity_types`, `pii_confidence_threshold`
- `redact_pii_audio`, `pii_redaction_mode`, `pii_buffer_ms`

After flattening, `dag.py` must read from `job.param_language` etc. instead of `job.parameters["language"]`. **However**, `dag.py` currently receives `parameters` as a pre-built dict from `selection.effective_parameters` — so the mapping from DB columns to dict happens in the service layer that loads the job, not in `dag.py` itself. Verify the actual call path before changing `dag.py`.

---

## M57.0 Implementation Plan (Phases 1-6)

### Phase 1: Create `dalston/db/types.py` + Replace Primitive Types

Create `dalston/db/types.py` with three `TypeDecorator` classes:

```python
class UUIDType(TypeDecorator):
    """UUID: native PG_UUID on Postgres, CHAR(36) on SQLite."""
    impl = String(36)
    cache_ok = True
    # load_dialect_impl → PG_UUID(as_uuid=True) on postgres, String(36) on sqlite
    # process_bind_param → uuid.UUID on postgres, str on sqlite
    # process_result_value → always uuid.UUID

class JSONType(TypeDecorator):
    """JSONB on Postgres, TEXT with JSON serde on SQLite."""
    impl = Text
    cache_ok = True
    # load_dialect_impl → JSONB on postgres, Text on sqlite
    # process_bind_param → passthrough on postgres, json.dumps on sqlite
    # process_result_value → json.loads if str, passthrough if dict

class InetType(TypeDecorator):
    """INET on Postgres, VARCHAR(45) on SQLite."""
    impl = String(45)
    cache_ok = True
```

Then in `models.py`:
- Replace all `PG_UUID(as_uuid=True)` → `UUIDType`
- Replace `server_default=func.gen_random_uuid()` → `default=uuid4`
- Replace `JSONB` → `JSONType` (for columns NOT being flattened/normalized)
- Replace `INET` → `InetType`
- Remove `from sqlalchemy.dialects.postgresql import ...` entirely from `models.py`
- Remove `from sqlalchemy import ARRAY` (after Phase 2)

Generate Alembic migration `0033`.

### Phase 2: Normalize ARRAY Columns → Junction Tables

Create four junction table models in `models.py`:

```python
class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    task_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("tasks.id"), primary_key=True)
    depends_on_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("tasks.id"), primary_key=True)

class WebhookEndpointEvent(Base):
    __tablename__ = "webhook_endpoint_events"
    endpoint_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("webhook_endpoints.id"), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), primary_key=True)

class JobPIIEntityType(Base):
    __tablename__ = "job_pii_entity_types"
    job_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("jobs.id"), primary_key=True)
    entity_type_id: Mapped[str] = mapped_column(String(50), ForeignKey("pii_entity_types.id"), primary_key=True)

class ArtifactComplianceTag(Base):
    __tablename__ = "artifact_compliance_tags"
    artifact_id: Mapped[UUID] = mapped_column(UUIDType, ForeignKey("artifact_objects.id"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(50), primary_key=True)
```

Update ORM relationships on parent models. Write data migration (unpack ARRAY → junction rows, then drop ARRAY columns).

Rewrite `webhook_endpoints.py:451` from `.any()` to:
```python
from sqlalchemy import exists, select
exists_clause = exists(
    select(WebhookEndpointEvent.endpoint_id).where(
        WebhookEndpointEvent.endpoint_id == WebhookEndpointModel.id,
        WebhookEndpointEvent.event_type.in_([event_type, "*"]),
    )
)
```

Update all code reading/writing ARRAY columns. Generate migration `0034`.

### Phase 3: Flatten `jobs.parameters` + Normalize `models.languages`

Add typed columns to `JobModel` with `param_` prefix:
- `param_language` (String(10)), `param_model` (String(200))
- `param_word_timestamps` (Boolean), `param_timestamps_granularity` (String(20))
- `param_speaker_detection` (String(20)), `param_num_speakers` (Integer)
- `param_min_speakers` (Integer), `param_max_speakers` (Integer)
- `param_beam_size` (Integer), `param_vad_filter` (Boolean)
- `param_exclusive` (Boolean), `param_num_channels` (Integer)
- `param_pii_confidence_threshold` (Float), `param_pii_buffer_ms` (Integer)
- `param_transcribe_config` (JSONType — engine-specific opaque blob)

Create `ModelLanguage` junction table: `model_languages(model_id FK, language_code String(10))`.

Write data migration to:
1. Extract values from `parameters` JSON → new columns
2. Unpack `models.languages` JSON array → `model_languages` rows
3. Keep `parameters` column temporarily as nullable `Text` (drop in follow-up once confirmed empty)

Update service layer that loads jobs to map new columns → dict for `dag.py` compatibility.

Generate migrations `0035` and `0036`.

### Phase 4: Replace Dialect-Specific Query Patterns

Create `dalston/db/dialect_helpers.py`:

```python
"""Thin dialect-aware query helpers (~50 lines)."""

def insert_or_ignore(model_class, values: dict, *, returning=None):
    """Postgres: ON CONFLICT DO NOTHING. SQLite: INSERT OR IGNORE."""
    ...

def apply_for_update(query, *, skip_locked: bool = False):
    """Postgres: FOR UPDATE SKIP LOCKED. SQLite: no-op."""
    ...
```

Update:
- `delivery.py:343-348` → use `insert_or_ignore()`
- `delivery.py:97` → use `apply_for_update()`
- `artifacts.py:64-66` → compute `purge_after` in Python:
  ```python
  from datetime import timedelta
  purge_after = available_at + timedelta(seconds=row.ttl_seconds)
  ```

Remove `CREATE RULE` on `audit_log` from migration chain (if present); enforce append-only via repository pattern (audit service has no UPDATE/DELETE methods).

Generate migration `0037`.

### Phase 5: Dual-Dialect Alembic Configuration

Update `alembic/env.py`:
- Detect dialect from `DATABASE_URL` and configure engine (`asyncpg` vs `aiosqlite`)
- Handle `sqlite://` → `sqlite+aiosqlite://` conversion (like existing PG conversion)
- Configure `render_as_batch=True` for SQLite (required for ALTER TABLE operations)
- Ensure `TypeDecorator` subclasses render correctly in autogenerated migrations

Update `alembic.ini`: no changes needed (env.py overrides URL from environment).

### Phase 6: Verification

- `dalston/db/models.py` has zero imports from `sqlalchemy.dialects.postgresql`
- `alembic upgrade head` succeeds on both Postgres and SQLite from empty DB
- All existing tests pass
- New test: `DATABASE_URL=sqlite+aiosqlite:///test.db alembic upgrade head`

---

## M57.1 Implementation Plan (Phases 1-6)

### Phase 1: Programmatic Migration Runner

Create `dalston/db/migrate.py`:

```python
"""Programmatic Alembic migration runner."""

async def upgrade_to_head(database_url: str) -> MigrationResult:
    """Run alembic upgrade head programmatically."""
    # Pre-flight checks for SQLite:
    # - Version >= 3.35.0
    # - DB file writable
    # - No WAL lock held by another process
    # Use alembic.command.upgrade() with alembic.config.Config
    ...

class MigrationResult:
    current_revision: str
    applied_count: int

class MigrationError(Exception): ...
class MigrationLockError(MigrationError): ...
class MigrationCorruptError(MigrationError): ...
class MigrationVersionError(MigrationError): ...
```

Write unit tests against in-memory SQLite.

### Phase 2: Startup Integration

Replace `_init_lite_schema()` in `init_db()` with `upgrade_to_head()`:

```python
async def init_db() -> None:
    settings = get_settings()
    database_url = (
        settings.database_url if settings.runtime_mode == "distributed"
        else settings.lite_database_url
    )
    await upgrade_to_head(database_url)
    await _ensure_default_tenant()
```

Startup order: Settings → Migration → Engine/Session factory → Services.

Map errors to actionable startup messages with recovery instructions.

### Phase 3: Legacy DB Upgrade Bridge

Create bridge migration that handles pre-M57.0 lite databases:
- Detect legacy schema (presence of `parameters` TEXT column without junction tables)
- Reshape data: backfill junction tables from JSON-encoded arrays
- Extract `parameters` keys into typed columns
- Idempotent — safe on databases already at M57.0 schema

Create test fixtures and upgrade-path integration tests.

### Phase 4: Remove Legacy Bootstrap Code

Delete from `dalston/db/session.py`:
- `_init_lite_schema()` function (lines 157-363)
- `_ensure_sqlite_columns()` helper
- `_SQLITE_BOOTSTRAP_TABLES`, `_SQLITE_IDENTIFIER_RE`, `_SQLITE_COLUMN_DDL_RE`
- All inline SQL strings

Update `_build_engine()` to no longer call `_init_lite_schema()`.

### Phase 5: M57 Carry-Forward Hardening

Close carry-forward issues from M57:
1. **#2 — Bootstrap lock stale-reclaim race**: Make `server_manager.py` lock reclaim race-safe under concurrent CLI invocations
2. **#3 — Unsafe SQLite DDL**: Resolved by Phase 4 (DDL helpers deleted entirely)
3. **#6 — Lite inline pipeline blocking**: Ensure lite execution path uses `asyncio.to_thread()` for blocking I/O
4. **#7 — Audit logging parity**: Ensure `log_job_created` audit event fires for lite failure paths too
5. **#10 — CLI bootstrap typing**: Use explicit client typing for bootstrap-disabled prerequisite helper
6. **#12 — Rate limiter return type**: Remove return-type suppression in rate limiter dependency

### Phase 6: CI Integration & Exit Gate

Verify:
- `alembic upgrade head` against empty SQLite — same revision chain as Postgres
- Legacy lite fixture upgrades to current revision
- Fresh lite startup → transcription → verify output
- Full test suite passes: `make lint && make test`

---

## File Inventory (All Expected Changes)

### New Files
- `dalston/db/types.py` (~80 lines)
- `dalston/db/dialect_helpers.py` (~50 lines)
- `dalston/db/migrate.py` (~100 lines)
- `alembic/versions/0033_dialect_adaptive_types.py`
- `alembic/versions/0034_normalize_array_to_junction_tables.py`
- `alembic/versions/0035_flatten_job_parameters.py`
- `alembic/versions/0036_normalize_model_languages.py`
- `alembic/versions/0037_remove_audit_log_rules_and_dialect_fixes.py`
- `alembic/versions/0038_bridge_legacy_lite_schema.py`
- `tests/unit/test_db_types.py`
- `tests/unit/test_db_dialect_helpers.py`
- `tests/unit/test_db_migrate.py`
- `tests/integration/test_lite_db_migration_upgrade.py`
- `tests/fixtures/legacy_lite_m56.db` (or SQL script to generate)

### Modified Files
- `dalston/db/models.py` (major — type replacement, junction models, parameter flattening)
- `dalston/db/session.py` (major — remove `_init_lite_schema()`, update `init_db()`)
- `alembic/env.py` (dual-dialect support)
- `dalston/gateway/services/webhook_endpoints.py` (ARRAY.any → JOIN)
- `dalston/gateway/services/artifacts.py` (INTERVAL → Python timedelta)
- `dalston/gateway/services/jobs.py` (parameter flattening read/write)
- `dalston/gateway/services/realtime_sessions.py` (verify `.returning()` compat)
- `dalston/gateway/services/model_registry.py` (languages normalization)
- `dalston/orchestrator/delivery.py` (on_conflict_do_nothing, FOR UPDATE)
- `dalston/orchestrator/handlers.py` (verify `.returning()` compat)
- `dalston/orchestrator/dag.py` (if parameter access changes)
- `dalston/gateway/main.py` (startup migration gate)
- `dalston/gateway/dependencies.py` (rate limiter typing fix)
- `cli/dalston_cli/bootstrap/server_manager.py` (lock stale-reclaim fix)
- `cli/dalston_cli/commands/transcribe.py` (bootstrap typing fix)

---

## Execution Order

Execute phases in this exact order. Each phase should result in a passing test suite before moving to the next.

1. **M57.0 Phase 1** — `types.py` + primitive type replacement in `models.py`
2. **M57.0 Phase 2** — Junction tables (ARRAY replacement)
3. **M57.0 Phase 3** — `jobs.parameters` flattening + `models.languages` normalization
4. **M57.0 Phase 4** — Dialect helpers + query pattern fixes
5. **M57.0 Phase 5** — Dual-dialect Alembic config
6. **M57.0 Phase 6** — Verification gate
7. **M57.1 Phase 1** — `migrate.py` programmatic runner
8. **M57.1 Phase 2** — Startup integration
9. **M57.1 Phase 3** — Legacy DB bridge migration
10. **M57.1 Phase 4** — Remove legacy bootstrap code
11. **M57.1 Phase 5** — Carry-forward hardening
12. **M57.1 Phase 6** — CI integration + exit gate

---

## Quality Checklist (Verify After Each Phase)

- [ ] `ruff check dalston/ tests/` passes
- [ ] `mypy dalston/` passes (or existing mypy errors don't increase)
- [ ] No `from sqlalchemy.dialects.postgresql` in `models.py` (after Phase 1)
- [ ] `alembic upgrade head` works on Postgres
- [ ] `DATABASE_URL=sqlite+aiosqlite:///test.db alembic upgrade head` works (after Phase 5)
- [ ] All existing tests pass
- [ ] No N+1 queries introduced by junction table JOINs
- [ ] Junction tables have composite primary keys (not separate id columns)
- [ ] Data migrations are reversible (downgrade path restores columns)
- [ ] `_init_lite_schema()` is fully deleted (after M57.1 Phase 4)
- [ ] API contract tests remain green

---

## Critical Implementation Notes

1. **`server_default` vs `default`**: When replacing `server_default=func.gen_random_uuid()` with `default=uuid4`, remember that `server_default` is SQL-level and `default` is Python-level. For SQLite, Python-level is required. For Postgres, Python-level also works fine (uuid4 generates before INSERT). So `default=uuid4` is correct for both.

2. **`TIMESTAMP(timezone=True)`**: This type already works on both Postgres and SQLite (SQLAlchemy handles it). No change needed.

3. **`server_default=func.now()`**: Works on both dialects. No change needed.

4. **`onupdate=func.now()`**: Python-level event, works on both. No change needed.

5. **Junction table data migration**: When unpacking ARRAY values into junction rows, use `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` to make the migration idempotent.

6. **`render_as_batch=True`**: SQLite doesn't support `ALTER TABLE DROP COLUMN` natively. Alembic's batch mode handles this by recreating the table. Set this in `env.py` when dialect is SQLite.

7. **`server_default="{}"` on JSONB columns**: When converting to `JSONType`, the `server_default` must change from `"{}"` (which was valid for JSONB) to `default=dict` (Python-level). The string `"{}"` would be stored literally as a string in SQLite TEXT, not as a JSON object.

8. **Transaction semantics**: SQLite uses `BEGIN DEFERRED` by default. The `with_for_update()` no-op on SQLite is safe because SQLite's single-writer lock provides equivalent serialization.

9. **`NULLS NOT DISTINCT`**: This is Postgres 15+ syntax. If any unique constraints use this, they need a dialect-aware wrapper or removal for SQLite. Check `webhook_deliveries` unique constraints.

10. **The `parameters` column on `jobs`**: Keep it as nullable `Text` after flattening. Do NOT drop it in the same migration. Add a CI check that flags non-empty values after backfill. Drop in a follow-up once confirmed empty across all environments.
