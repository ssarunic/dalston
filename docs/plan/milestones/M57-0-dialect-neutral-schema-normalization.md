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

## Intended Outcomes

### Functional outcomes

1. All ORM models use only ANSI SQL–portable column types.
2. A single `alembic upgrade head` works against both Postgres and SQLite.
3. Existing API request/response contracts are unchanged.
4. Distributed-mode behavior and performance are unchanged.

### Architecture outcomes

1. One model definition, one migration chain, two dialects — no fork.
2. ARRAY columns are replaced by proper junction tables (standard relational design).
3. JSONB columns with known schemas are normalized into typed columns or key/value rows.
4. Remaining schemaless JSONB columns become `Text` storing JSON strings, with
   serialization handled in a thin accessor layer.
5. Postgres-specific query patterns (`.any()`, `on_conflict_do_nothing`,
   `.returning()`, `FOR UPDATE SKIP LOCKED`, raw `INTERVAL` casts) are replaced
   with portable equivalents or wrapped in dialect helpers.

### Operational outcomes

1. Schema changes are made once — a single Alembic revision covers both dialects.
2. CI runs migration tests against both Postgres and SQLite from the same revision chain.
3. The ad-hoc `_init_lite_schema()` bootstrap in `session.py` is no longer needed for
   schema creation (Alembic handles it).

### Clean-start outcomes

1. No `from sqlalchemy.dialects.postgresql` imports remain in `models.py`.
2. No parallel DDL maintenance path for lite mode.
3. No silent type mismatch between distributed and lite schemas.

---

## Inventory of Non-Portable Features

### A. Column types to replace

#### A1. `PG_UUID(as_uuid=True)` — 28 columns across all models

All primary keys and foreign keys use Postgres-native UUID with `server_default=func.gen_random_uuid()`.

| Replacement | `String(36)` |
|---|---|
| Default generation | App-side `str(uuid4())` via column `default` |
| Impact | Mechanical — find-and-replace across models, no query changes |

#### A2. `JSONB` — 8 columns

| Table | Column | Schema known? | Action |
|-------|--------|--------------|--------|
| `jobs` | `parameters` | **Yes** — finite keys used in `dag.py` | **Flatten** into typed columns on `jobs` |
| `tenants` | `settings` | Yes — tenant config | Keep as `Text` (JSON string) — rarely queried, no indexed access |
| `tasks` | `config` | Partially — varies per engine | Keep as `Text` (JSON string) — opaque blob passed to engines |
| `audit_log` | `detail` | No — intentionally schemaless | Keep as `Text` (JSON string) |
| `webhook_deliveries` | `payload` | No — mirrors API response shape | Keep as `Text` (JSON string) |
| `settings` | `value` | Yes — single typed value | Change to `Text` — the table is already key/value structured |
| `models` | `languages` | **Yes** — list of language codes | **Normalize** into `model_languages` junction table |
| `models` | `model_metadata` | No — external HuggingFace schema | Keep as `Text` (JSON string) |

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
| `audit_log` | `ip_address` | `String(45)` — covers IPv4, IPv6, and mapped addresses |

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

### Phase 1: Replace Primitive Types (UUID, INET)

1. Replace all `PG_UUID(as_uuid=True)` with `String(36)`.
2. Replace `server_default=func.gen_random_uuid()` with `default=lambda: str(uuid4())`.
3. Replace `INET` with `String(45)`.
4. Remove `from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID`.
5. Update Alembic `env.py` to support both `asyncpg` and `aiosqlite` engines.
6. Generate Alembic migration for type changes.

Expected files:

- `dalston/db/models.py`
- `alembic/env.py`
- `alembic/versions/NNNN_normalize_uuid_inet_types.py`

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
3. **Convert remaining JSONB to Text:**
   - `tenants.settings` → `Text` (JSON string)
   - `tasks.config` → `Text` (JSON string)
   - `audit_log.detail` → `Text` (JSON string)
   - `webhook_deliveries.payload` → `Text` (JSON string)
   - `settings.value` → `Text` (JSON string)
   - `models.model_metadata` → `Text` (JSON string)
4. Add thin property accessors or helper for JSON serialization/deserialization
   on models that use `Text` for structured data.
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

5. **Risk:** Text-stored JSON loses indexed query capability on Postgres.
   - **Mitigation:** The codebase performs zero JSONB index queries today (confirmed
     by grep).  No functional regression.

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

1. `dalston/db/models.py` contains zero imports from `sqlalchemy.dialects.postgresql`.
2. `alembic upgrade head` succeeds on both Postgres and SQLite from an empty database.
3. All existing tests pass without modification (beyond adapting to new column names).
4. CI includes a SQLite migration smoke test.
5. The four ARRAY columns are replaced by junction tables with proper foreign keys.
6. `jobs.parameters` JSONB is replaced by typed columns; `dag.py` reads columns directly.
