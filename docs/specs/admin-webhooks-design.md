# Admin-Registered Webhooks: Design & Decisions

## 1. Storage: PostgreSQL, Not Redis

**Decision:** Store webhook endpoints and delivery logs in PostgreSQL.

**Context:** The existing codebase uses Redis for ephemeral/fast-access data (API keys,
rate limits, session tokens, queues) and PostgreSQL for durable relational data (jobs,
tasks, tenants). Webhook endpoints are configuration that must survive restarts,
support querying (list by tenant, filter by event type), and participate in foreign key
relationships (tenant_id). Delivery logs need ordered querying with pagination.

**Alternatives considered:**

- *Redis hashes* (like API keys): Would work for the endpoints themselves, but delivery
  logs would grow unbounded and Redis has no good story for paginated range queries
  over structured data. API keys went to Redis because auth validation is on the hot
  path (every request); webhook endpoint lookups happen only when events fire (much
  lower frequency).
- *Hybrid* (endpoints in Redis, logs in Postgres): Adds complexity for no clear
  benefit. The event-time lookup of endpoints is not latency-sensitive (we're about to
  make HTTP calls that take hundreds of milliseconds).

**Consequence:** We add two new tables and an Alembic migration. The orchestrator gains
a PostgreSQL dependency for webhook dispatch (it already has one via `async_session`).

---

## 2. Data Model

### `webhook_endpoints` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID FK → tenants | Scoped to tenant |
| `url` | TEXT NOT NULL | The callback URL |
| `description` | VARCHAR(255) | Human label, optional |
| `events` | TEXT[] NOT NULL | Array of event type strings to subscribe to |
| `signing_secret` | TEXT NOT NULL | Per-endpoint HMAC secret, generated server-side |
| `is_active` | BOOLEAN NOT NULL DEFAULT true | Soft-disable without deleting |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

**Design notes:**

- `events` is a Postgres `TEXT[]` array rather than a join table. The set of event
  types is small and fixed; a join table would be over-normalized. We query with
  `@>` (array contains) operator.
- `signing_secret` is stored server-side. The admin retrieves it once at creation
  time (returned in the POST response, never again — like an API key). This is
  more secure than the current single `WEBHOOK_SECRET` env var because a compromise
  of one endpoint's secret doesn't affect others.
- We considered adding `headers` (custom headers to include) and rejected it for
  now. Custom headers are a common webhook feature but add complexity (secret
  management, header injection risks). We can add this later if needed.
- No `url` uniqueness constraint per tenant. Legitimate use case: same URL subscribed
  to different event sets, or multiple URLs on the same domain for different services.

### `webhook_deliveries` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `endpoint_id` | UUID FK → webhook_endpoints | Which endpoint |
| `job_id` | UUID FK → jobs, nullable | Which job triggered this (null for non-job events) |
| `event_type` | VARCHAR(50) | e.g., `transcription.completed` |
| `payload` | JSONB | The full payload sent |
| `status` | VARCHAR(20) | `pending`, `success`, `failed` |
| `attempts` | INT DEFAULT 0 | Number of delivery attempts so far |
| `last_attempt_at` | TIMESTAMPTZ | |
| `last_status_code` | INT | HTTP status from last attempt |
| `last_error` | TEXT | Error message from last attempt |
| `next_retry_at` | TIMESTAMPTZ, nullable | When to retry next (null if succeeded or exhausted) |
| `created_at` | TIMESTAMPTZ | |

**Design notes:**

- This table replaces the in-memory retry loop. The orchestrator polls for rows where
  `status = 'pending' AND next_retry_at <= now()` on a timer. This survives crashes.
- We index `(endpoint_id, created_at DESC)` for the admin delivery log query and
  `(status, next_retry_at)` for the retry poller.
- Delivery rows are immutable once `status` reaches `success` or `failed` (max
  retries exhausted). We don't update them further.
- Old delivery rows should be cleaned up. A simple approach: a periodic task that
  deletes rows older than 7 days (configurable). This is not part of the initial
  implementation — we document it as a follow-up.

### Event types

Initial set of subscribable events:

| Event | Fires when |
|-------|-----------|
| `transcription.completed` | Batch job finishes successfully |
| `transcription.failed` | Batch job fails permanently |

Future candidates (not implemented now, but the schema supports them):

| Event | Fires when |
|-------|-----------|
| `transcription.created` | New job is submitted |
| `transcription.started` | Processing begins |
| `realtime.session_started` | Real-time session opens |
| `realtime.session_ended` | Real-time session closes |

Wildcard `*` subscribes to all events. This is a convenience — we expand it at
delivery time, not at storage time, so new event types are automatically included.

---

## 3. API Design

All endpoints require `webhooks` or `admin` scope.

### `POST /v1/webhooks`

Create a new webhook endpoint.

**Request body (JSON):**
```json
{
  "url": "https://example.com/hooks/dalston",
  "events": ["transcription.completed", "transcription.failed"],
  "description": "Production notification handler"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "url": "https://example.com/hooks/dalston",
  "events": ["transcription.completed", "transcription.failed"],
  "description": "Production notification handler",
  "signing_secret": "whsec_abc123...",
  "is_active": true,
  "created_at": "2026-02-05T..."
}
```

The `signing_secret` is only returned in the creation response. It uses a `whsec_`
prefix for easy identification (convention borrowed from Stripe).

**Why JSON body, not form data?** Webhook registration is a management operation, not
a file-upload flow. JSON is the natural fit and aligns with how the API key management
endpoints work.

### `GET /v1/webhooks`

List all endpoints for the tenant. Supports `?is_active=true` filter.

### `GET /v1/webhooks/{endpoint_id}`

Get a single endpoint. Does **not** return `signing_secret`.

### `PATCH /v1/webhooks/{endpoint_id}`

Update endpoint fields. Supports partial updates to: `url`, `events`, `description`,
`is_active`. Changing `url` triggers re-validation (SSRF checks). Cannot update
`signing_secret` — must rotate instead.

### `DELETE /v1/webhooks/{endpoint_id}`

Hard delete. Also deletes associated delivery log rows (CASCADE). If the admin wants
to temporarily disable without losing history, use `PATCH` to set `is_active: false`.

### `POST /v1/webhooks/{endpoint_id}/rotate-secret`

Generate a new signing secret. Returns the new secret (one-time). The old secret
becomes invalid immediately. This handles the case where a secret is compromised.

### `GET /v1/webhooks/{endpoint_id}/deliveries`

Paginated delivery log. Supports `?status=failed` filter. Returns most recent first.

### `POST /v1/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry`

Manually retry a specific failed delivery. Resets `status` to `pending`, sets
`next_retry_at` to now.

---

## 4. Delivery Mechanics

### Flow

```
Event fires (job.completed/failed)
  → Orchestrator looks up all active webhook_endpoints for tenant
      WHERE events @> ARRAY[event_type] AND is_active = true
  → For each matching endpoint:
      INSERT INTO webhook_deliveries (status='pending', next_retry_at=now())
  → Also: if job has a per-job webhook_url, create a delivery row for it too
      (use a synthetic endpoint_id = NULL, store url directly on the delivery row)
  → Delivery worker picks up pending rows and delivers
```

### Delivery worker

The delivery worker is a loop inside the orchestrator (not a separate service). It
polls `webhook_deliveries` every 2 seconds for rows where
`status = 'pending' AND next_retry_at <= now()`.

**Why polling, not notify/listen?** We already poll Redis pub/sub in the orchestrator
main loop. Adding a Postgres LISTEN/NOTIFY channel is possible but adds complexity.
Polling with a 2-second interval is fine for webhook delivery latency — the HTTP
call itself will dominate.

**Why not a Redis queue?** The whole point is crash-resilient delivery. If we put
pending deliveries in a Redis list, we're back to the same problem: orchestrator crash
loses in-flight deliveries. Postgres rows with `next_retry_at` are the durable queue.

### Retry schedule

| Attempt | Delay after failure |
|---------|-------------------|
| 1 (initial) | immediate |
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 10 minutes |
| 5 | 1 hour |

After 5 failed attempts, `status` is set to `failed` permanently. The admin can
manually retry via the API.

**Why 5 attempts instead of the current 3?** The current in-memory retries happen
within seconds (1s, 2s, 4s delays). That's fine for transient network blips but
misses longer outages. With persistent retries we can afford a wider window — up to
~72 minutes total — which covers short maintenance windows on the receiving end.

### Concurrency

The delivery worker processes up to 10 deliveries concurrently (configurable).
Each delivery is an `asyncio.Task` with a 30-second timeout. We use
`SELECT ... FOR UPDATE SKIP LOCKED` to prevent multiple orchestrator instances
(if scaled) from processing the same delivery row.

---

## 5. Signing

Each registered endpoint has its own `signing_secret`. The signing algorithm remains
the same as today:

```
signature = HMAC-SHA256(signing_secret, "{timestamp}.{payload_json}")
header: X-Dalston-Signature: sha256={hex}
header: X-Dalston-Timestamp: {unix_ts}
```

For per-job webhook_url deliveries (backward compat), we continue using the global
`WEBHOOK_SECRET` env var. This is documented as deprecated behavior.

We add a new header `X-Dalston-Webhook-Id` containing the delivery UUID. This lets
receivers deduplicate deliveries (in case of retries that succeed but we don't see
the response due to a timeout).

---

## 6. Per-Job Webhook Backward Compatibility

The `webhook_url` and `webhook_metadata` parameters on
`POST /v1/audio/transcriptions` continue to work. When a job with a `webhook_url`
completes or fails:

1. All registered endpoints for the tenant matching the event type are notified (new).
2. The per-job `webhook_url` is also notified (existing behavior).

Per-job webhooks go through the same delivery table for persistence and
observability. They are stored as deliveries with `endpoint_id = NULL` and the URL
stored directly on the delivery row (via an additional nullable `url_override`
column on `webhook_deliveries`).

A new config flag `ALLOW_PER_JOB_WEBHOOKS` (default: `true`) lets operators disable
the per-job parameter entirely, forcing all webhook configuration through the admin
registration flow.

---

## 7. What We Are Deliberately Not Doing

| Feature | Why not |
|---------|---------|
| Custom HTTP headers per endpoint | Adds header-injection risk and secret-management scope. Revisit if requested. |
| Payload transformation/templates | Over-engineering. The payload format is documented; receivers adapt. |
| Webhook endpoint health checks | The delivery log serves as a health signal. Proactive pinging is unnecessary. |
| Rate limiting outbound webhooks | Could flood a slow receiver, but the concurrency cap (10) and retry backoff provide natural throttling. Revisit under load. |
| Delivery log retention policy | Noted as follow-up. For now, rows accumulate. Add a cleanup job later. |
| mTLS for webhook delivery | Would require certificate management UI. HMAC signing is sufficient for authentication. |
