# ADR-007: Admin Webhook Design

## Status

Proposed

## Context

Dalston's current webhook system (M5) lets callers pass a `webhook_url` per job at
submission time. This creates security concerns (arbitrary URL targeting), breaks
ElevenLabs API compatibility, and provides no delivery observability. We need a
design for admin-registered webhook endpoints that addresses these issues.

Three interconnected design questions must be answered:

1. **Where to store webhook endpoints and delivery logs** — Redis vs. PostgreSQL
2. **How to sign payloads** — shared secret vs. per-endpoint secrets
3. **How to retry failed deliveries** — in-memory vs. persistent queue

## Options Considered

### 1. Storage: Redis vs. PostgreSQL

**Option A — Redis hashes (like API keys)**

API keys are stored in Redis for O(1) lookup on every request (hot path). Webhook
endpoints could follow the same pattern.

**Pros:**
- Consistent with API key storage pattern
- Fast lookups

**Cons:**
- Delivery logs grow unbounded — Redis has no good story for paginated range queries
  over structured data
- No relational integrity (can't CASCADE delete deliveries when endpoint deleted)
- Webhook endpoint lookups are not on the hot path — they happen only when events
  fire (orders of magnitude less frequent than auth checks)

**Option B — PostgreSQL (chosen)**

Store both endpoints and delivery logs as relational tables.

**Pros:**
- Queryable delivery logs with pagination, filtering, ordering
- Foreign key constraints (endpoint → tenant, delivery → endpoint with CASCADE)
- Consistent with how jobs/tasks are stored (durable business data)
- Alembic migrations for schema changes

**Cons:**
- Adds PostgreSQL dependency to orchestrator's webhook dispatch path (already exists
  for job lookups)

### 2. Signing: Shared Secret vs. Per-Endpoint Secrets

**Option A — Single shared WEBHOOK_SECRET (current behavior)**

One env var, all webhooks signed with the same key.

**Pros:**
- Simple configuration
- Already implemented

**Cons:**
- Compromising one receiver's secret compromises all endpoints
- Cannot rotate for one endpoint without affecting all others
- Receivers cannot independently verify they are the intended recipient

**Option B — Per-endpoint signing secrets (chosen)**

Each registered endpoint gets its own `signing_secret`, generated server-side at
creation time.

**Pros:**
- Secret compromise is isolated to one endpoint
- Independent rotation per endpoint
- Follows industry convention (Stripe, GitHub, Svix all use per-endpoint secrets)
- `whsec_` prefix makes secrets identifiable

**Cons:**
- Must look up endpoint to retrieve secret at delivery time (mitigated: we already
  look up the endpoint for the URL)
- Admin must store the secret when first shown (one-time display, like API keys)

### 3. Retry: In-Memory vs. Persistent Queue

**Option A — In-memory retry loop (current behavior)**

Orchestrator holds pending retries in memory with `asyncio.sleep` delays.

**Pros:**
- Simple implementation
- Low latency for transient failures

**Cons:**
- Orchestrator crash loses all pending retries silently
- Retry window limited to seconds (current: 1s, 2s, 4s) — misses longer outages
- No visibility into retry state
- Cannot manually retry

**Option B — Redis list as retry queue**

Push failed deliveries to a Redis list, pop and retry on a timer.

**Pros:**
- Survives orchestrator restart (if Redis persists)
- Decouples dispatch from delivery

**Cons:**
- Redis persistence is not guaranteed (RDB/AOF tradeoffs)
- No structured querying of delivery history
- Duplicates the problem: crashes can still lose in-flight items

**Option C — PostgreSQL delivery table (chosen)**

Insert delivery rows with `status` and `next_retry_at`. Worker polls for pending
rows using `SELECT ... FOR UPDATE SKIP LOCKED`.

**Pros:**
- Fully durable — survives any component crash
- Queryable delivery log for free (same table)
- Manual retry is a simple UPDATE
- `SKIP LOCKED` enables safe concurrent processing by multiple orchestrator instances
- Retry window can span hours (immediate → 30s → 2m → 10m → 1h)

**Cons:**
- Polling adds ~2 second latency to first delivery (acceptable — HTTP call itself
  takes hundreds of milliseconds)
- More rows in PostgreSQL (mitigated: retention cleanup as future follow-up)

## Decision

1. **PostgreSQL** for webhook endpoint and delivery storage (follows ADR-001: PostgreSQL
   for durable business data).
2. **Per-endpoint signing secrets** with `whsec_` prefix, generated server-side,
   displayed once at creation time.
3. **PostgreSQL-backed persistent delivery** with polling worker, replacing in-memory
   retries. 5-attempt schedule spanning ~72 minutes.

Per-job `webhook_url` is retained for backward compatibility but routed through the
same delivery table (with `endpoint_id=NULL` and `url_override` set). A config flag
`ALLOW_PER_JOB_WEBHOOKS` allows operators to disable the legacy behavior.

## Consequences

### Easier

- Adding new event types (just add to the allowed set, endpoints with `*` get them
  automatically)
- Debugging webhook failures (delivery log with HTTP status, error, attempt count)
- Recovering from outages (manual retry via API, or automatic retry over ~72 minutes)
- Scaling orchestrator horizontally (`SKIP LOCKED` prevents duplicate delivery)
- Rotating compromised secrets (per-endpoint, no global impact)

### Harder

- Initial setup requires admin to register endpoints (one-time, but more steps than
  passing a URL per job)
- Delivery table grows over time (need retention cleanup — documented as follow-up)
- Orchestrator now polls PostgreSQL every 2 seconds (low overhead, but new load pattern)

### Mitigations

- Per-job `webhook_url` remains as opt-in fallback during migration
- Delivery log retention cleanup planned as follow-up task
- Polling interval is configurable; 2 seconds is conservative default
- Admin API provides full CRUD so endpoint management can be automated
