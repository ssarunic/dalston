# ADR-002: Migrate API Key Storage from Redis to PostgreSQL

## Status

Proposed

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
5. **Zero auth-path performance regression** — The hot path (validate key on every request) must remain sub-millisecond. Redis continues to serve this role as a read-through cache.

### Non-goals

- Changing the API key format, hashing algorithm, or scope model.
- Migrating session tokens out of Redis (they are genuinely ephemeral — TTL-based, 10-minute default).
- Migrating rate-limit counters out of Redis (high-frequency, sliding-window — Redis is the right tool).

### Success criteria

- All existing tests pass (unit + integration).
- API keys persist across `docker compose down && docker compose up` with Redis data wiped.
- Auth validation latency on the hot path does not measurably increase (Redis cache hit).
- `has_any_api_keys`, `list_api_keys`, and `get_api_key_by_id` work correctly even when Redis is cold (empty).
- Existing API keys are migrated from Redis to PostgreSQL without downtime.

---

## 2. Tactical — Implementation Choices

### 2.1 PostgreSQL as source of truth, Redis as read-through cache

| Operation | Storage | Why |
|---|---|---|
| Create key | Write to PostgreSQL, then populate Redis cache | PostgreSQL owns the record. Redis is populated eagerly to avoid a cache miss on the first request. |
| Validate key (hot path) | Read from Redis; on miss, read from PostgreSQL and populate Redis | Sub-millisecond for 99%+ of requests (cache hit). Cold-start fallback to PostgreSQL (~1-5ms). |
| List keys | Query PostgreSQL directly | Infrequent admin operation. SQL gives filtering, sorting, pagination for free. No need to cache. |
| Get key by ID | Query PostgreSQL directly | Same as list — admin operation, no caching needed. |
| Revoke key | Update PostgreSQL, then delete from Redis cache | Revocation is immediately effective: the cached entry is removed, so the next validation falls through to PostgreSQL which returns the revoked key (rejected). |
| Update last_used_at | Write to Redis only (fire-and-forget); periodic flush to PostgreSQL | High-frequency write. Batching avoids per-request database writes. Acceptable to lose a few minutes of `last_used_at` data on crash. |
| Bootstrap check | Query PostgreSQL (`SELECT EXISTS`) | No Redis scan needed. Single indexed query. |

### 2.2 What stays in Redis (unchanged)

| Data | Why it stays |
|---|---|
| Session tokens (`tk_`) | Genuinely ephemeral (10-min TTL). Redis TTL auto-cleanup is the right fit. No value in persisting. |
| Rate-limit counters | High-frequency INCR with sliding-window expiry. Redis is purpose-built for this. |
| Pub/Sub events | Ephemeral by nature. |

### 2.3 New SQLAlchemy model

A new `APIKeyModel` table in `dalston/db/models.py`, columns mapping 1:1 to the existing `APIKey` dataclass fields. Foreign key to `tenants.id`. Index on `key_hash` for the cache-miss lookup path and on `tenant_id` for listing.

We keep the existing `APIKey` dataclass as the domain object. The model is only the persistence layer. `AuthService` methods translate between them.

### 2.4 Alembic migration

A new Alembic migration creates the `api_keys` table. A data-migration step reads any existing keys from Redis and inserts them into PostgreSQL, so existing deployments don't lose keys during upgrade.

### 2.5 AuthService changes

`AuthService` currently takes only a `Redis` client. After migration it takes both `Redis` and `AsyncSession`. The dependency injection in `dependencies.py` is updated to pass both.

The `APIKey.to_dict` / `from_dict` methods remain for Redis cache serialization. A new `APIKeyModel.to_domain()` / `from_domain()` handles the SQLAlchemy ↔ dataclass translation.

### 2.6 Cache invalidation strategy

Simple and conservative:

- **Write-through on create**: write to PostgreSQL, then set in Redis.
- **Invalidate on revoke**: delete the Redis cache entry. Next validation hits PostgreSQL, sees `revoked_at` is set, returns None.
- **No TTL on cache entries**: API keys are long-lived. Cache entries are explicitly invalidated on revoke. This avoids unnecessary cache misses.
- **Graceful degradation**: if Redis is down, validation falls back to PostgreSQL. Slower but functional.

### 2.7 `last_used_at` handling

Writing `last_used_at` to PostgreSQL on every request would add a write per API call — unacceptable. Instead:

- Continue updating `last_used_at` in Redis on every validation (current behavior).
- Add a periodic background task (e.g., every 5 minutes) that flushes `last_used_at` values from Redis to PostgreSQL.
- On key listing/detail (admin operations), read `last_used_at` from Redis if available, fall back to PostgreSQL value.

This is an acceptable tradeoff: `last_used_at` is informational, not security-critical.

---

## 3. Plan — Task List for the Coding Agent

### Phase 1: Database model and migration

1. **Add `APIKeyModel` to `dalston/db/models.py`**
   - Columns: `id` (UUID PK), `key_hash` (String, unique, indexed), `prefix` (String), `name` (String), `tenant_id` (UUID FK → tenants.id, indexed), `scopes` (String — comma-separated, matching Redis format), `rate_limit` (Integer, nullable), `created_at` (TIMESTAMP), `last_used_at` (TIMESTAMP, nullable), `expires_at` (TIMESTAMP), `revoked_at` (TIMESTAMP, nullable).
   - Add relationship on `TenantModel`: `api_keys` back-populates.

2. **Create Alembic migration**
   - `alembic revision --autogenerate -m "add_api_keys_table"`
   - Verify the generated migration looks correct.
   - Add a `post-migrate` data migration function that reads existing keys from Redis (`dalston:apikey:hash:*` scan) and inserts them into the new table, skipping duplicates. This runs once as part of the migration.

3. **Verify migration**
   - Run `alembic upgrade head` against a test database.
   - Confirm table exists with correct schema.

### Phase 2: AuthService refactor

4. **Update `AuthService.__init__`** to accept both `Redis` and `AsyncSession`.

5. **Refactor `create_api_key`**
   - Write to PostgreSQL first (insert `APIKeyModel`).
   - Then populate Redis cache (existing `_store_api_key` logic).
   - Wrap in a try/except: if Redis write fails, the key still exists in PostgreSQL (degraded but functional).

6. **Refactor `validate_api_key` (hot path)**
   - Try Redis first (existing logic).
   - On cache miss: query PostgreSQL by `key_hash`, check revoked/expired, populate Redis cache, return.
   - Keep the fire-and-forget `last_used_at` update in Redis.

7. **Refactor `get_api_key_by_id`**
   - Query PostgreSQL directly (`SELECT ... WHERE id = :id AND tenant_id = :tenant_id`).
   - Remove the Redis id→hash→data double-lookup.

8. **Refactor `list_api_keys`**
   - Query PostgreSQL: `SELECT ... WHERE tenant_id = :tenant_id ORDER BY created_at DESC`.
   - Add `include_revoked` filter in the WHERE clause.
   - Remove Redis set iteration.

9. **Refactor `revoke_api_key`**
   - Update `revoked_at` in PostgreSQL.
   - Delete all Redis cache entries for this key (hash key, id key, tenant set member).

10. **Refactor `has_any_api_keys`**
    - Replace Redis SCAN with `SELECT EXISTS(SELECT 1 FROM api_keys WHERE revoked_at IS NULL)`.

### Phase 3: Dependency injection and startup

11. **Update `dependencies.py`**
    - `get_auth_service` now depends on both `get_redis` and `get_db`.
    - Pass both to `AuthService(redis, db)`.

12. **Update `_ensure_admin_key_exists` in `main.py`**
    - Use a database session instead of (or in addition to) Redis.
    - The check uses PostgreSQL; the create writes to both.

### Phase 4: Cache maintenance

13. **Add `last_used_at` flush task**
    - A background coroutine in the gateway lifespan that periodically (every 5 minutes):
      - Scans Redis for `dalston:apikey:hash:*` entries.
      - Reads `last_used_at` from each.
      - Batch-updates PostgreSQL rows where the Redis value is newer.
    - Runs as an `asyncio.create_task` in the lifespan startup, cancelled on shutdown.

14. **Remove orphaned Redis-only indexes**
    - The `dalston:apikey:id:{id}` and `dalston:tenant:{tenant_id}:apikeys` keys are no longer needed for management operations (those go through PostgreSQL now).
    - Keep `dalston:apikey:hash:{hash}` as the cache entry.
    - Clean up the `_store_api_key` method to only write the hash-keyed cache entry.

### Phase 5: Tests

15. **Update unit tests (`tests/unit/test_auth.py`)**
    - Mock both Redis and AsyncSession.
    - Test cache-miss path (Redis empty → falls through to PostgreSQL).
    - Test cache-hit path (Redis populated → no database query).
    - Test revocation invalidates cache.
    - Test `list_api_keys` uses PostgreSQL.
    - Test `has_any_api_keys` uses PostgreSQL.

16. **Update integration tests (`tests/integration/test_auth_api.py`)**
    - Tests should pass without changes to the API contract (same request/response shapes).
    - Add a test that creates a key, wipes Redis, and validates the key still works (cache-miss path).

17. **Run full test suite** — `pytest` — confirm everything passes.

### Phase 6: Documentation

18. **Update ADR-001** — add a note that API key storage has been aligned with the stated architecture as of ADR-002.

19. **Update `docs/specs/implementations/auth-patterns.md`** — reflect that PostgreSQL is now the source of truth, Redis is cache-only for auth validation.

### Order of operations

Phases 1-3 are the core migration. Phase 4 is an optimization that can land in the same PR or follow. Phase 5 must be done alongside each phase. Phase 6 is final cleanup.

The entire change should be a single PR to avoid an intermediate state where some code expects PostgreSQL and some expects Redis-only.
