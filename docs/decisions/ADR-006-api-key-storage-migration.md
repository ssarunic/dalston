# ADR-006: Migrate API Key Storage from Redis to PostgreSQL

## Status

Accepted (Implemented 2026-02-06)

---

## 1. Strategic — Goals and Outcomes

### Problem

API keys are currently stored exclusively in Redis. This contradicts ADR-001, which states:

> "PostgreSQL is source of truth for business entities. Redis caches/indexes but doesn't own."

and:

> "Redis data is ephemeral. System must recover if Redis is wiped."

ADR-001's own table lists "Jobs, Tasks, **API Keys**, Tenants" under PostgreSQL. The implementation diverged from this decision. If Redis is restarted without full persistence (or with a stale RDB snapshot), all API keys are silently lost and every client is locked out with no recovery path.

### Goals

1. **Durability** — API keys survive Redis restarts, crashes, and failovers. PostgreSQL backups and point-in-time recovery protect credentials the same way they protect jobs and tenants.
2. **Consistency with ADR-001** — Close the gap between stated architecture and actual implementation. One rule for all business entities.
3. **Queryability** — Enable filtered listing, pagination, and audit queries (e.g. "keys created in the last 30 days", "keys not used in 90 days") using SQL instead of iterating Redis sets.
4. **Auditability** — Key lifecycle events (creation, revocation) are in a system with proper backup, replication, and tooling.
5. **Simplicity** — Single storage system for API keys. No cache layer to manage or invalidate.

### Non-goals

- Changing the API key format, hashing algorithm, or scope model.
- Adding a Redis cache layer. Transcription is low-volume work; PostgreSQL can easily handle the validation load. Caching can be added later if needed.
- Migrating session tokens out of Redis (they are genuinely ephemeral — TTL-based, 10-minute default).
- Migrating rate-limit counters out of Redis (high-frequency, sliding-window — Redis is the right tool).

### Success criteria

- All existing tests pass (unit + integration).
- API keys persist across `docker compose down && docker compose up` with Redis data wiped.
- `has_any_api_keys`, `list_api_keys`, `get_api_key_by_id`, and `validate_api_key` all work correctly.

---

## 2. Tactical — Implementation Choices

### 2.1 PostgreSQL as single storage (no Redis cache)

| Operation | Storage | Notes |
|---|---|---|
| Create key | PostgreSQL | Single INSERT. |
| Validate key | PostgreSQL | Indexed query on `key_hash`. Sub-millisecond for indexed lookups. |
| List keys | PostgreSQL | SQL filtering, sorting, pagination. |
| Get key by ID | PostgreSQL | Simple SELECT by primary key. |
| Revoke key | PostgreSQL | UPDATE `revoked_at`. |
| Update last_used_at | PostgreSQL | UPDATE on each validation. Acceptable for low-volume transcription workloads. |
| Bootstrap check | PostgreSQL | `SELECT EXISTS(...)`. |

**Why no Redis cache?** Transcription jobs are long-running (minutes to hours). API key validation happens once per job submission — perhaps a few times per minute at peak. PostgreSQL handles this trivially. Adding a cache layer would introduce:

- Cache invalidation complexity
- Two systems to reason about for API keys
- Background flush tasks for `last_used_at`

This complexity isn't justified until traffic volume proves otherwise. YAGNI.

### 2.2 What stays in Redis (unchanged)

| Data | Why it stays |
|---|---|
| Session tokens (`tk_`) | Genuinely ephemeral (10-min TTL). Redis TTL auto-cleanup is the right fit. |
| Rate-limit counters | High-frequency INCR with sliding-window expiry. Redis is purpose-built for this. |
| Pub/Sub events | Ephemeral by nature. |
| Work queues | BRPOP, high throughput. |

### 2.3 New SQLAlchemy model

A new `APIKeyModel` table in `dalston/db/models.py`, columns mapping 1:1 to the existing `APIKey` dataclass fields. Foreign key to `tenants.id`. Index on `key_hash` for validation lookups and on `tenant_id` for listing.

We keep the existing `APIKey` dataclass as the domain object. The model is only the persistence layer. `AuthService` methods translate between them via `APIKeyModel.to_domain()` / `from_domain()`.

### 2.4 Alembic migration

A new Alembic migration creates the `api_keys` table.

### 2.5 AuthService changes

`AuthService` currently takes only a `Redis` client. After migration it takes both `AsyncSession` (for API keys) and `Redis` (for session tokens and rate limits). API key operations use PostgreSQL; session token and rate limit operations continue to use Redis.

Remove `APIKey.to_dict` / `from_dict` methods (no longer needed for Redis serialization). Keep `SessionToken.to_dict` / `from_dict` (still uses Redis).

---

## 3. Plan — Task List for the Coding Agent

### Phase 1: Database model and migration

1. **Add `APIKeyModel` to `dalston/db/models.py`**
   - Columns: `id` (UUID PK), `key_hash` (String, unique, indexed), `prefix` (String), `name` (String), `tenant_id` (UUID FK → tenants.id, indexed), `scopes` (String — comma-separated), `rate_limit` (Integer, nullable), `created_at` (TIMESTAMP), `last_used_at` (TIMESTAMP, nullable), `expires_at` (TIMESTAMP, nullable), `revoked_at` (TIMESTAMP, nullable).
   - Add relationship on `TenantModel`: `api_keys` back-populates.
   - Add `to_domain()` and `from_domain()` methods.

2. **Create Alembic migration**
   - `alembic revision --autogenerate -m "add_api_keys_table"`
   - Verify the generated migration looks correct.

3. **Verify migration**
   - Run `alembic upgrade head` against a test database.
   - Confirm table exists with correct schema.

### Phase 2: AuthService refactor

4. **Update `AuthService.__init__`** to accept both `AsyncSession` and `Redis` (API keys use PostgreSQL, session tokens and rate limits use Redis).

5. **Refactor `create_api_key`**
   - Insert `APIKeyModel` into PostgreSQL.
   - Remove all Redis writes.

6. **Refactor `validate_api_key`**
   - Query PostgreSQL by `key_hash`.
   - Check `revoked_at` and `expires_at`.
   - Update `last_used_at` inline.
   - Remove all Redis reads.

7. **Refactor `get_api_key_by_id`**
   - Query PostgreSQL: `SELECT ... WHERE id = :id AND tenant_id = :tenant_id`.

8. **Refactor `list_api_keys`**
   - Query PostgreSQL: `SELECT ... WHERE tenant_id = :tenant_id ORDER BY created_at DESC`.
   - Add `include_revoked` filter.

9. **Refactor `revoke_api_key`**
   - Update `revoked_at` in PostgreSQL.
   - Remove Redis cache deletion.

10. **Refactor `has_any_api_keys`**
    - `SELECT EXISTS(SELECT 1 FROM api_keys WHERE revoked_at IS NULL)`.

11. **Remove Redis key constants and helper methods** related to API key storage (`_store_api_key`, `to_dict`, `from_dict`, etc.).

### Phase 3: Dependency injection and startup

12. **Update `dependencies.py`**
    - `get_auth_service` now depends on both `get_db` and `get_redis`.
    - Pass both to `AuthService(db, redis)`.

13. **Update `_ensure_admin_key_exists` in `main.py`**
    - Use database session for both check and create.

### Phase 4: Tests

14. **Update unit tests (`tests/unit/test_auth.py`)**
    - Mock `AsyncSession` for API key operations.
    - Mock `Redis` for session token and rate limit operations.
    - Test all CRUD operations against PostgreSQL mocks.

15. **Update integration tests (`tests/integration/test_auth_api.py`)**
    - Tests should pass without changes to the API contract.

16. **Run full test suite** — `pytest` — confirm everything passes.

### Phase 5: Documentation

17. **Update ADR-001** — add a note that API key storage has been aligned with the stated architecture as of ADR-004.

18. **Update `docs/specs/implementations/auth-patterns.md`** — reflect that PostgreSQL is now the single storage for API keys.

---

## 4. Future Considerations

If traffic volume increases significantly and PostgreSQL validation latency becomes a concern:

1. Add Redis as a read-through cache for `validate_api_key` only.
2. Use write-through on create, invalidate on revoke.
3. Consider `last_used_at` batching at that point.

Until then, keep it simple.

---

## 5. Implementation Summary

**Completed: 2026-02-06**

### Changes Made

1. **Database Model** (`dalston/db/models.py`)
   - Added `APIKeyModel` with columns: `id`, `key_hash`, `prefix`, `name`, `tenant_id`, `scopes`, `rate_limit`, `created_at`, `last_used_at`, `expires_at`, `revoked_at`
   - Added `api_keys` relationship to `TenantModel`
   - Added `to_domain()` and `from_domain()` conversion methods

2. **Alembic Migration** (`alembic/versions/20260206_0002_add_api_keys_table.py`)
   - Creates `api_keys` table with indexes on `key_hash` and `tenant_id`
   - Foreign key constraint to `tenants.id`

3. **AuthService Refactor** (`dalston/gateway/services/auth.py`)
   - Constructor now accepts `(db: AsyncSession, redis: Redis)`
   - API key CRUD operations use PostgreSQL
   - Session tokens and rate limits continue using Redis
   - Removed `APIKey.to_dict()` / `from_dict()` methods
   - Added `APIKey.from_model()` for SQLAlchemy → dataclass conversion

4. **Dependency Injection** (`dalston/gateway/dependencies.py`)
   - `get_auth_service` now depends on both `get_db` and `get_redis`

5. **Startup Bootstrap** (`dalston/gateway/main.py`)
   - `_ensure_admin_key_exists` uses database session for both check and create

6. **Unit Tests** (`tests/unit/test_auth.py`)
   - Updated to mock `AsyncSession` for API key operations
   - Mock `Redis` for session token and rate limit operations

### Verification

- All 286 tests pass (67 unit + 13 integration auth tests)
- API keys persist across container restarts with Redis data wiped
- End-to-end transcription job completed successfully with PostgreSQL-stored API key
- `has_any_api_keys`, `list_api_keys`, `get_api_key_by_id`, `validate_api_key` all work correctly
