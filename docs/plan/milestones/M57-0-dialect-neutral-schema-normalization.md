# M57.0: Dialect-Neutral Schema Normalization

| | |
|---|---|
| **Goal** | Eliminate Postgres-specific types and query patterns from the ORM layer so that a single SQLAlchemy model set and single Alembic migration track can target both Postgres and SQLite |
| **Duration** | 5-8 days |
| **Dependencies** | M47 (SQL layer separation — complete) |
| **Deliverable** | Dialect-neutral ORM models, junction tables replacing ARRAY columns, normalized replacements for avoidable JSONB, portable query rewrites, single Alembic migration chain proven against both dialects, and Postgres migration to match |
| **Status** | Planned |

## Motivation

Dalston supports two runtime modes — `distributed` (Postgres) and `lite` (SQLite).
Today the ORM models import `JSONB`, `ARRAY`, `INET`, and `PG_UUID` from
`sqlalchemy.dialects.postgresql`, forcing the lite path to maintain a separate
hand-rolled DDL bootstrap with manual type mapping.  This creates two problems:

1. **Drift risk** — every schema change must be done twice (Alembic migration +
   inline DDL in `_init_lite_schema()`), with no compile-time or CI guard that
   they stay in sync.
2. **Unnecessary coupling** — analysis of the query layer shows almost zero use
   of Postgres-specific query operators.  The only runtime query that exploits a
   dialect feature is `WebhookEndpointModel.events.any()`, and several JSONB
   columns store data with known, finite keys that belong in regular columns or
   normalized tables.

M57.0 removes these dialect dependencies at the source so that M57.1 can
introduce a single Alembic migration track for both dialects instead of
maintaining two.

---

## Approach: Dialect-Adaptive Types via `TypeDecorator`

Rather than downgrading every column to the lowest common denominator (e.g.,
replacing UUID with `String(36)` everywhere), we use SQLAlchemy's
`TypeDecorator` with `load_dialect_impl()` to get the best of both worlds:

- **Postgres keeps native types** — real `UUID` (16-byte storage), `JSONB`
  (indexable if ever needed), `INET` (validated network addresses).
- **SQLite gets portable fallbacks** — `CHAR(36)`, `TEXT`, `VARCHAR(45)`.
- **Models reference one type name** — no dialect imports in `models.py`.
- **Alembic renders the right DDL per dialect** — `TypeDecorator` is
  dialect-aware at DDL generation time.

### Core type adapters (`dalston/db/types.py`)

```python
"""Dialect-adaptive column types for Postgres/SQLite portability."""

import json
import uuid
from typing import Any

from sqlalchemy import String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class UUIDType(TypeDecorator):
    """UUID: native on Postgres, CHAR(36) on SQLite."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


class JSONType(TypeDecorator):
    """JSONB on Postgres, TEXT with JSON serialization on SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB)
        return dialect.type_descriptor(Text)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # asyncpg handles dict → JSONB natively
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value  # already a dict (Postgres)


class InetType(TypeDecorator):
    """INET on Postgres, VARCHAR(45) on SQLite."""

    impl = String(45)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(INET)
        return dialect.type_descriptor(String(45))

    def process_bind_param(self, value, dialect):
        return value  # both store as string

    def process_result_value(self, value, dialect):
        return value
```

With these adapters, model definitions become dialect-agnostic:

```python
# Before (Postgres-coupled)
from sqlalchemy.dialects.postgresql import JSONB, PG_UUID
id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), server_default=func.gen_random_uuid())
parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

# After (portable)
from dalston.db.types import UUIDType, JSONType
id: Mapped[UUID] = mapped_column(UUIDType, default=uuid4)
parameters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
```

Postgres-dialect imports move from `models.py` (used everywhere) to
`dalston/db/types.py` (one file, three classes, never touched by feature work).

---

## Intended Outcomes

### Functional outcomes

1. All ORM models use dialect-adaptive types — no direct Postgres imports.
2. A single `alembic upgrade head` works against both Postgres and SQLite.
3. Postgres retains native type performance (UUID, JSONB, INET).
4. Existing API request/response contracts are unchanged.
5. Distributed-mode behavior and performance are unchanged.

### Architecture outcomes

1. One model definition, one migration chain, two dialects — no fork.
2. ARRAY columns are replaced by proper junction tables (standard relational design).
3. JSONB columns with known schemas are normalized into typed columns or key/value rows.
4. Remaining schemaless JSONB columns use `JSONType` — renders as `JSONB` on Postgres,
   `TEXT` on SQLite, with automatic serialization in both directions.
5. Postgres-specific query patterns (`.any()`, `on_conflict_do_nothing`,
   `.returning()`, `FOR UPDATE SKIP LOCKED`, raw `INTERVAL` casts) are replaced
   with portable equivalents or wrapped in dialect helpers.

### Operational outcomes

1. Schema changes are made once — a single Alembic revision covers both dialects.
2. CI runs migration tests against both Postgres and SQLite from the same revision chain.
3. The ad-hoc `_init_lite_schema()` bootstrap in `session.py` is no longer needed for
   schema creation (Alembic handles it).

### Clean-start outcomes

1. No direct `from sqlalchemy.dialects.postgresql` imports remain in `models.py`.
2. No parallel DDL maintenance path for lite mode.
3. No silent type mismatch between distributed and lite schemas.

---

## Inventory of Non-Portable Features

### A. Column types to replace

#### A1. `PG_UUID(as_uuid=True)` — 28 columns across all models

All primary keys and foreign keys use Postgres-native UUID with `server_default=func.gen_random_uuid()`.

| Replacement | `UUIDType` (dialect-adaptive `TypeDecorator`) |
|---|---|
| Postgres behavior | Native `UUID` column (16-byte binary storage) |
| SQLite behavior | `CHAR(36)` storing string representation |
| Default generation | App-side `default=uuid4` — works on both dialects |
| Impact | Mechanical — replace type + remove `server_default` across models |

#### A2. `JSONB` — 8 columns

| Table | Column | Schema known? | Action |
|-------|--------|--------------|--------|
| `jobs` | `parameters` | **Yes** — finite keys used in `dag.py` | **Flatten** into typed columns on `jobs` |
| `tenants` | `settings` | Yes — tenant config | `JSONType` — `JSONB` on Postgres, `TEXT` on SQLite |
| `tasks` | `config` | Partially — varies per engine | `JSONType` — opaque blob passed to engines |
| `audit_log` | `detail` | No — intentionally schemaless | `JSONType` |
| `webhook_deliveries` | `payload` | No — mirrors API response shape | `JSONType` |
| `settings` | `value` | Yes — single typed value | `JSONType` |
| `models` | `languages` | **Yes** — list of language codes | **Normalize** into `model_languages` junction table |
| `models` | `model_metadata` | No — external HuggingFace schema | `JSONType` |

Columns kept as `JSONType` retain `JSONB` on Postgres (preserving indexability
if ever needed) while transparently serializing to `TEXT` on SQLite.

#### A3. `ARRAY(...)` — 4 columns

All four are textbook junction-table candidates. Using ARRAY was a Postgres shortcut.

| Table | Column | Element type | Junction table |
|-------|--------|-------------|----------------|
| `jobs` | `pii_entity_types` | `String` | `job_pii_entity_types(job_id FK, entity_type_id FK → pii_entity_types.id)` |
| `tasks` | `dependencies` | `UUID` | `task_dependencies(task_id FK, depends_on_id FK → tasks.id)` |
| `webhook_endpoints` | `events` | `String` | `webhook_endpoint_events(endpoint_id FK, event_type VARCHAR)` |
| `artifact_objects` | `compliance_tags` | `String` | `artifact_compliance_tags(artifact_id FK, tag VARCHAR)` |

#### A4. `INET` — 1 column

| Table | Column | Replacement |
|-------|--------|-------------|
| `audit_log` | `ip_address` | `InetType` — `INET` on Postgres (validated), `VARCHAR(45)` on SQLite |

#### A5. `CREATE RULE` — 2 rules on `audit_log`

Postgres rules enforcing append-only behavior. Replace with application-level enforcement (repository pattern — no UPDATE/DELETE methods exposed).

### B. Query patterns to replace

#### B1. `ARRAY.any()` — 1 location

**File:** `dalston/gateway/services/webhook_endpoints.py:451-452`

```python
WebhookEndpointModel.events.any(event_type)
| WebhookEndpointModel.events.any("*"),
```

**After normalization:** becomes a JOIN on `webhook_endpoint_events` table — standard SQL.

#### B2. `postgresql.insert().on_conflict_do_nothing()` — 1 location

**File:** `dalston/orchestrator/delivery.py:344-347`

```python
stmt = (
    insert(WebhookDeliveryModel)
    .values(**values)
    .on_conflict_do_nothing()
    .returning(WebhookDeliveryModel.id)
)
```

**Replacement:** Use a dialect-aware helper:
- Postgres: `INSERT ... ON CONFLICT DO NOTHING`
- SQLite: `INSERT OR IGNORE ...`

Both supported by SQLAlchemy's `insert().prefix_with("OR IGNORE")` for SQLite
or a small `upsert_or_ignore()` helper that checks `bind.dialect.name`.

#### B3. `.returning()` — 4 locations

| File | Line | Table |
|------|------|-------|
| `orchestrator/delivery.py` | 347 | `WebhookDeliveryModel.id` |
| `orchestrator/handlers.py` | 377 | `TaskModel.stage` |
| `orchestrator/handlers.py` | 527 | `TaskModel.id` |
| `gateway/services/realtime_sessions.py` | 216 | `RealtimeSessionModel` |

**Note:** SQLite added `RETURNING` support in version 3.35.0 (2021-03-12).
Python 3.11+ ships SQLite 3.39+, so `RETURNING` is available on all supported
platforms.  **No change needed** if we set minimum SQLite version floor at 3.35.

If older SQLite support is needed, replace with select-after-write pattern.

#### B4. `FOR UPDATE SKIP LOCKED` — 1 location

**File:** `dalston/orchestrator/delivery.py:97`

```python
.with_for_update(skip_locked=True)
```

**Context:** Used for concurrent webhook delivery dequeue.  In lite mode the
orchestrator is single-process, so row-level locking is unnecessary.

**Replacement:** Dialect-aware helper — emit `FOR UPDATE SKIP LOCKED` on
Postgres, no-op on SQLite (single-writer guarantees ordering).

#### B5. Raw SQL with `CAST(... AS INTERVAL)` — 1 location

**File:** `dalston/gateway/services/artifacts.py:64-71`

```sql
SET purge_after = CAST(:available_at AS TIMESTAMPTZ)
                + CAST(ttl_seconds || ' seconds' AS INTERVAL)
```

**Replacement:** Compute `purge_after` in Python (`available_at + timedelta(seconds=ttl)`)
and pass as a bound parameter.  Removes dialect-specific SQL entirely.

---

## What We Will Not Do In M57.0

1. Do not change API request/response contracts.
2. Do not introduce a second Alembic migration track — the point is to make one track work.
3. Do not change the lite mode's functional scope (that is M58).
4. Do not migrate existing data in production — schema migration handles column
   additions; data backfill is a separate concern if needed.
5. Do not optimize query performance — keep changes mechanical and behavior-preserving.
6. Do not remove the `_init_lite_schema()` bootstrap — M57.1 handles that transition.
   M57.0 makes the models portable; M57.1 wires Alembic to run against SQLite.

---

## Strategy

### Strategy 1: Bottom-up type replacement

Replace column types first (models.py), then fix compilation errors in queries
and services.  This ensures the ORM is the single source of truth and changes
propagate naturally to both Alembic and query layers.

### Strategy 2: Junction tables via Alembic migration

Add new junction tables and drop ARRAY columns in a single Alembic revision with
data migration (copy existing ARRAY values into junction rows, then drop column).

### Strategy 3: Dialect helpers over abstraction layers

For the small number of dialect-specific query patterns (B2, B4, B5), write
thin helper functions rather than an abstraction layer.  Three helpers total.

### Strategy 4: Flatten `jobs.parameters` incrementally

Extract known keys from `parameters` JSONB into typed columns on `jobs`.
Add a data migration that copies values from the JSON blob into the new columns.
Existing code that reads `job.parameters["language"]` changes to `job.language_code`
(or similar).  Keep `parameters` column temporarily as `Text` for any
unforeseen keys, remove in a follow-up once confirmed empty.

---

## Tactical Plan

### Phase 1: Dialect-Adaptive Type Layer and Primitive Type Migration

1. Create `dalston/db/types.py` with three `TypeDecorator` classes:
   - `UUIDType` — `PG_UUID(as_uuid=True)` on Postgres, `String(36)` on SQLite
   - `JSONType` — `JSONB` on Postgres, `Text` with JSON serde on SQLite
   - `InetType` — `INET` on Postgres, `String(45)` on SQLite
2. Replace all `PG_UUID(as_uuid=True)` references in `models.py` with `UUIDType`.
3. Replace `server_default=func.gen_random_uuid()` with `default=uuid4`.
4. Replace `INET` with `InetType`.
5. Replace remaining `JSONB` references (columns not being flattened or normalized
   in later phases) with `JSONType`.
6. Remove `from sqlalchemy.dialects.postgresql import ...` from `models.py`.
7. Update Alembic `env.py` to support both `asyncpg` and `aiosqlite` engines,
   and to render `TypeDecorator` subclasses correctly in autogenerated migrations.
8. Generate Alembic migration for type changes.

Expected files:

- `dalston/db/types.py` (new — ~80 lines)
- `dalston/db/models.py`
- `alembic/env.py`
- `alembic/versions/NNNN_dialect_adaptive_types.py`

### Phase 2: Normalize ARRAY Columns into Junction Tables

1. Create four junction tables:
   - `task_dependencies(task_id, depends_on_id)` — replaces `tasks.dependencies`
   - `webhook_endpoint_events(endpoint_id, event_type)` — replaces `webhook_endpoints.events`
   - `job_pii_entity_types(job_id, entity_type_id)` — replaces `jobs.pii_entity_types`
   - `artifact_compliance_tags(artifact_id, tag)` — replaces `artifact_objects.compliance_tags`
2. Write data migration: for each existing row, unpack ARRAY values into junction rows.
3. Drop ARRAY columns.
4. Update ORM relationships to use the junction tables.
5. Rewrite `webhook_endpoints.py:451` query from `.any()` to JOIN.
6. Update all code that reads/writes these columns.

Expected files:

- `dalston/db/models.py` (junction models + relationship changes)
- `alembic/versions/NNNN_normalize_array_to_junction_tables.py`
- `dalston/gateway/services/webhook_endpoints.py`
- `dalston/orchestrator/dag.py` (task dependencies)
- `dalston/orchestrator/handlers.py` (task dependencies)
- `dalston/gateway/services/jobs.py` (pii_entity_types)

### Phase 3: Normalize and Reduce JSONB Usage

1. **Flatten `jobs.parameters`** — extract known keys into typed columns:
   - `language` → `String(10)`
   - `model` → `String(200)`
   - `word_timestamps` → `Boolean`
   - `timestamps_granularity` → `String(20)`
   - `speaker_detection` → `String(20)`
   - `num_speakers` → `Integer`
   - `min_speakers` → `Integer`
   - `max_speakers` → `Integer`
   - `beam_size` → `Integer`
   - `vad_filter` → `Boolean`
   - `exclusive` → `Boolean`
   - `num_channels` → `Integer`
   - `pii_detection` → `Boolean` (may overlap with `pii_detection_enabled`)
   - `redact_pii_audio` → `Boolean` (may overlap with `pii_redact_audio`)
   - `pii_entity_types` → already handled by junction table in Phase 2
   - `pii_redaction_mode` → already a column
   - `pii_confidence_threshold` → `Float`
   - `pii_buffer_ms` → `Integer`
   - `transcribe_config` → `Text` (JSON string — engine-specific opaque blob)
2. **Normalize `models.languages`** — create `model_languages(model_id, language_code)`.
3. **Convert remaining JSONB to `JSONType`** (already done in Phase 1 for most;
   verify completeness):
   - `tenants.settings` → `JSONType` (JSONB on Postgres, TEXT on SQLite)
   - `tasks.config` → `JSONType`
   - `audit_log.detail` → `JSONType`
   - `webhook_deliveries.payload` → `JSONType`
   - `settings.value` → `JSONType`
   - `models.model_metadata` → `JSONType`
   - `jobs.param_transcribe_config` → `JSONType` (the one flattened field that remains JSON)

   No manual serialization helpers needed — `JSONType.process_bind_param()` and
   `process_result_value()` handle dict↔string conversion transparently.
5. Write Alembic migration with data backfill for `jobs.parameters` extraction.
6. Update `dalston/orchestrator/dag.py` to read from new columns instead of `parameters` dict.

Expected files:

- `dalston/db/models.py`
- `alembic/versions/NNNN_flatten_job_parameters.py`
- `alembic/versions/NNNN_normalize_model_languages.py`
- `alembic/versions/NNNN_convert_jsonb_to_text.py`
- `dalston/orchestrator/dag.py`
- `dalston/gateway/services/jobs.py`
- `dalston/gateway/services/model_registry.py`

### Phase 4: Replace Dialect-Specific Query Patterns

1. **`on_conflict_do_nothing`** — write `dialect_insert_or_ignore()` helper.
2. **`FOR UPDATE SKIP LOCKED`** — write `dialect_for_update()` helper that no-ops on SQLite.
3. **Raw `INTERVAL` arithmetic** — compute in Python, pass as parameter.
4. **`CREATE RULE`** — remove from migration; enforce append-only in application code
   (audit service has no update/delete methods).
5. Verify `.returning()` works on target SQLite version (3.35+); document minimum version.

Expected files:

- `dalston/db/dialect_helpers.py` (new — small module, ~50 lines)
- `dalston/orchestrator/delivery.py`
- `dalston/gateway/services/artifacts.py`
- `alembic/versions/NNNN_remove_audit_log_rules.py`

### Phase 5: Dual-Dialect Alembic Configuration

1. Update `alembic/env.py` to detect dialect from `DATABASE_URL` and configure
   engine accordingly (`asyncpg` vs `aiosqlite`).
2. Add `alembic.ini` setting for SQLite test URL (e.g., `sqlite+aiosqlite:///test.db`).
3. Add CI job: run `alembic upgrade head` against an empty SQLite database and
   verify schema matches expectations.
4. Add CI job: run `alembic upgrade head` against Postgres and verify no regression.

Expected files:

- `alembic/env.py`
- `alembic.ini`
- CI configuration (e.g., `.github/workflows/` or `Makefile` targets)

### Phase 6: Verification and Exit Gate

1. `make lint` passes (no `sqlalchemy.dialects.postgresql` imports in `models.py`).
2. `make test` passes against Postgres.
3. New CI target: `make test-lite-schema` — migrates empty SQLite, runs schema assertions.
4. Verify API contract tests remain green.
5. Verify distributed integration tests remain green.

Suggested command sets:

```bash
make lint && make test
```

```bash
# SQLite migration smoke test
DATABASE_URL=sqlite+aiosqlite:///test_migration.db alembic upgrade head
```

---

## Detailed: `jobs.parameters` Flattening

The `parameters` JSONB column on `jobs` is the most impactful normalization.
All keys accessed in the codebase (primarily `dalston/orchestrator/dag.py`) are
enumerated below with their proposed column mapping:

| JSON key | Proposed column | Type | Default | Notes |
|----------|----------------|------|---------|-------|
| `language` | `param_language` | `String(10)` | `NULL` | BCP-47 code |
| `model` | `param_model` | `String(200)` | `NULL` | Model ID |
| `word_timestamps` | `param_word_timestamps` | `Boolean` | `false` | |
| `timestamps_granularity` | `param_timestamps_granularity` | `String(20)` | `'segment'` | `segment` or `word` |
| `speaker_detection` | `param_speaker_detection` | `String(20)` | `'none'` | `none`, `auto`, `forced` |
| `num_speakers` | `param_num_speakers` | `Integer` | `NULL` | Exact speaker count hint |
| `min_speakers` | `param_min_speakers` | `Integer` | `NULL` | |
| `max_speakers` | `param_max_speakers` | `Integer` | `NULL` | |
| `beam_size` | `param_beam_size` | `Integer` | `NULL` | |
| `vad_filter` | `param_vad_filter` | `Boolean` | `NULL` | |
| `exclusive` | `param_exclusive` | `Boolean` | `false` | |
| `num_channels` | `param_num_channels` | `Integer` | `NULL` | |
| `pii_detection` | — | — | — | Already `pii_detection_enabled` column |
| `redact_pii_audio` | — | — | — | Already `pii_redact_audio` column |
| `pii_redaction_mode` | — | — | — | Already a column |
| `pii_confidence_threshold` | `param_pii_confidence_threshold` | `Float` | `NULL` | |
| `pii_buffer_ms` | `param_pii_buffer_ms` | `Integer` | `NULL` | |
| `transcribe_config` | `param_transcribe_config` | `Text` | `NULL` | Engine-specific JSON blob |

The `param_` prefix avoids collision with existing columns (e.g., `pii_detection_enabled`
vs the old `parameters["pii_detection"]`).  After migration and code update, the
`parameters` column is dropped.

---

## Risks and Mitigations

1. **Risk:** Alembic migration against Postgres is destructive (drops columns, changes types).
   - **Mitigation:** Data migration step copies values before dropping.  Test against
     a Postgres snapshot in CI before merging.  Downgrade path restores columns.

2. **Risk:** Junction table JOINs are slower than ARRAY containment checks.
   - **Mitigation:** The affected tables are small (webhook_endpoints, artifact_objects).
     Add composite indexes on junction tables.  Benchmark before/after.

3. **Risk:** `jobs.parameters` flattening misses an undocumented key.
   - **Mitigation:** Keep `parameters` column as `Text` (nullable) in the first
     migration.  Add a CI check that flags any non-empty `parameters` value after
     backfill.  Drop the column in a follow-up revision only when confirmed empty.

4. **Risk:** SQLite `RETURNING` requires version 3.35+.
   - **Mitigation:** Document minimum SQLite version.  Python 3.11 ships 3.39.4,
     Python 3.10 ships 3.37.2.  Both satisfy the requirement.  Add a startup
     check that validates SQLite version.

5. **Risk:** ~~Text-stored JSON loses indexed query capability on Postgres.~~
   **Eliminated** — `JSONType` renders as `JSONB` on Postgres, so native indexing
   remains available.  No capability regression on either dialect.

---

## Relationship to Other Milestones

| Milestone | Relationship |
|-----------|-------------|
| **M47** (SQL layer separation) | Prerequisite — complete.  Services own all queries, making query rewrites localized. |
| **M57.1** (lite migration track) | **Successor** — M57.0 makes models portable; M57.1 wires Alembic to run on SQLite at startup and removes `_init_lite_schema()`. With M57.0 done, M57.1 becomes simpler (no second migration track needed). |
| **M56** (lite infra backends) | Parallel concern — M56 abstracts queue/storage; M57.0 abstracts the schema. |
| **M58** (lite pipeline expansion) | Downstream — benefits from portable schema. |

---

## Exit Criteria

1. `dalston/db/models.py` contains zero direct imports from `sqlalchemy.dialects.postgresql`.
   All dialect-specific types are isolated in `dalston/db/types.py`.
2. `alembic upgrade head` succeeds on both Postgres and SQLite from an empty database.
3. Postgres retains native column types (UUID, JSONB, INET) via `TypeDecorator` dispatch.
4. All existing tests pass without modification (beyond adapting to new column names).
5. CI includes a SQLite migration smoke test.
6. The four ARRAY columns are replaced by junction tables with proper foreign keys.
7. `jobs.parameters` JSONB is replaced by typed columns; `dag.py` reads columns directly.
