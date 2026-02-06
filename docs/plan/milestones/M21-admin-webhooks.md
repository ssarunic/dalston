# M21: Admin-Registered Webhooks

| | |
|---|---|
| **Goal** | Replace per-job webhook URLs with admin-registered endpoints for security, observability, and ElevenLabs API compatibility |
| **Duration** | 3-4 days |
| **Dependencies** | M5 (webhooks), M11 (API authentication) |
| **Deliverable** | CRUD API for webhook endpoints, persistent delivery with retries, delivery log, per-endpoint signing secrets |

## User Story

> *"As an admin, I can register webhook endpoints once and have all transcription events delivered to them automatically, without callers needing to specify URLs per job."*

> *"As an operator, I can inspect webhook delivery history and retry failed deliveries from the admin API."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ADMIN WEBHOOK FLOW                                       │
│                                                                              │
│   Admin (one-time setup)                                                     │
│   POST /v1/webhooks                                                          │
│   { url, events }                                                            │
│         │                                                                    │
│         ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                     PostgreSQL                                       │   │
│   │                                                                      │   │
│   │   webhook_endpoints: url, events[], signing_secret, is_active       │   │
│   │   webhook_deliveries: payload, status, attempts, next_retry_at      │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   Job completes / fails                                                      │
│         │                                                                    │
│         ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                    Orchestrator                                       │   │
│   │                                                                      │   │
│   │   1. Query active endpoints WHERE events @> {event_type}            │   │
│   │   2. INSERT webhook_deliveries row per endpoint                     │   │
│   │   3. (Also per-job webhook_url if set — backward compat)            │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                    │
│         ▼                                                                    │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                  Delivery Worker                                     │   │
│   │                                                                      │   │
│   │   Poll: WHERE status='pending' AND next_retry_at <= now()           │   │
│   │   SELECT ... FOR UPDATE SKIP LOCKED                                 │   │
│   │   Deliver with per-endpoint signing_secret                          │   │
│   │   Retry: immediate → 30s → 2m → 10m → 1h (5 attempts)             │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                    │
│         ▼                                                                    │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│   │ Endpoint A   │  │ Endpoint B   │  │ Per-job URL  │                    │
│   │ (registered) │  │ (registered) │  │ (legacy)     │                    │
│   └──────────────┘  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Motivation

The current per-job `webhook_url` parameter (M5) has two structural problems:

1. **Security exposure.** Any caller with `jobs:write` can direct webhook traffic to arbitrary URLs. SSRF protections mitigate the worst cases, but the attack surface is the entire internet. An admin-registered allowlist constrains it to verified endpoints.

2. **ElevenLabs API incompatibility.** The ElevenLabs Speech-to-Text API does not accept `webhook_url` as a submission parameter. Jobs submitted via `/v1/speech-to-text/*` cannot use webhooks at all without admin registration.

Secondary concerns: no delivery observability (no logs, no manual retry), and in-memory retries lost on orchestrator crash.

---

## Steps

### 21.1: Database Models & Migration

**New tables:**

| Table | Key columns |
|-------|------------|
| `webhook_endpoints` | `id`, `tenant_id` FK, `url`, `events` TEXT[], `signing_secret`, `is_active`, `created_at`, `updated_at` |
| `webhook_deliveries` | `id`, `endpoint_id` FK (nullable), `job_id` FK, `event_type`, `payload` JSONB, `url_override` (nullable), `status`, `attempts`, `next_retry_at`, `last_status_code`, `last_error`, `created_at` |

**Deliverables:**

- SQLAlchemy models in `dalston/db/models.py`
- Alembic migration
- Composite index on `(status, next_retry_at)` for delivery worker polling
- Index on `(endpoint_id, created_at DESC)` for delivery log queries

---

### 21.2: Webhook Endpoint Service (CRUD)

**Deliverables:**

- `WebhookEndpointService` class in `dalston/gateway/services/webhook_endpoints.py`
- `create_endpoint()` — validate URL (reuse SSRF checks), validate events, generate `whsec_`-prefixed signing secret
- `list_endpoints()` — query by tenant, optional `is_active` filter
- `get_endpoint()` — by ID, verify tenant ownership
- `update_endpoint()` — partial update of url, events, description, is_active
- `delete_endpoint()` — hard delete, CASCADE to deliveries
- `rotate_secret()` — generate new secret, invalidate old immediately
- `ALLOW_PER_JOB_WEBHOOKS` config flag (default: true)

**Allowed event types:**

| Event | Description |
|-------|------------|
| `transcription.completed` | Batch job finished successfully |
| `transcription.failed` | Batch job failed permanently |
| `*` | Wildcard — all events |

---

### 21.3: Admin API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/webhooks` | POST | Register endpoint (returns `signing_secret` once) |
| `/v1/webhooks` | GET | List tenant's endpoints |
| `/v1/webhooks/{id}` | GET | Get endpoint (no secret) |
| `/v1/webhooks/{id}` | PATCH | Update endpoint fields |
| `/v1/webhooks/{id}` | DELETE | Delete endpoint + delivery history |
| `/v1/webhooks/{id}/rotate-secret` | POST | Rotate signing secret |
| `/v1/webhooks/{id}/deliveries` | GET | Paginated delivery log |
| `/v1/webhooks/{id}/deliveries/{did}/retry` | POST | Retry failed delivery |

All endpoints require `webhooks` scope (existing `Scope.WEBHOOKS` from M11).

**Webhook payload (unchanged from M5):**

```json
{
  "event": "transcription.completed",
  "transcription_id": "job_abc123",
  "status": "completed",
  "timestamp": "2025-01-28T12:00:00Z",
  "text": "First 500 chars of transcript...",
  "duration": 45.2,
  "webhook_metadata": {"user_id": "123"}
}
```

**New header:**

| Header | Description |
|--------|------------|
| `X-Dalston-Webhook-Id` | Delivery UUID for deduplication |

---

### 21.4: Persistent Delivery System

**Deliverables:**

- Refactor `WebhookService` to accept per-endpoint signing secret
- Create `DeliveryWorker` in `dalston/orchestrator/delivery.py`
- Polls `webhook_deliveries` every 2 seconds for pending rows
- Uses `SELECT ... FOR UPDATE SKIP LOCKED` for safe concurrency
- Max 10 concurrent deliveries
- Replace in-memory retry loop with delivery table rows

**Retry schedule:**

| Attempt | Delay after failure |
|---------|-------------------|
| 1 (initial) | immediate |
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 10 minutes |
| 5 | 1 hour |

After 5 attempts → `status = 'failed'`. Admin can manually retry via API.

---

### 21.5: Backward Compatibility & Deprecation

**Deliverables:**

- Per-job `webhook_url` continues to work (creates delivery row with `endpoint_id=NULL`, `url_override` set)
- `ALLOW_PER_JOB_WEBHOOKS=false` rejects `webhook_url` parameter with helpful error message
- Deprecation warning logged when per-job `webhook_url` is used

---

### 21.6: SDK & Documentation Updates

**Deliverables:**

- Update SDK `verify_webhook_signature()` docs for per-endpoint secrets
- Update `docs/specs/examples/webhook-verification.md`
- Note `whsec_` prefix convention in docs

---

## Verification

```bash
# Register a webhook endpoint
curl -X POST http://localhost:8000/v1/webhooks \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://my-server.com/hooks/dalston",
    "events": ["transcription.completed", "transcription.failed"],
    "description": "Production handler"
  }'
# → Returns { id, signing_secret: "whsec_...", ... }

# List endpoints
curl http://localhost:8000/v1/webhooks \
  -H "Authorization: Bearer $DALSTON_API_KEY"

# Submit job (no webhook_url needed)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@audio.mp3"
# → Registered endpoints receive webhook on completion

# Check delivery log
curl http://localhost:8000/v1/webhooks/{endpoint_id}/deliveries \
  -H "Authorization: Bearer $DALSTON_API_KEY"

# Retry a failed delivery
curl -X POST http://localhost:8000/v1/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry \
  -H "Authorization: Bearer $DALSTON_API_KEY"

# Rotate signing secret
curl -X POST http://localhost:8000/v1/webhooks/{endpoint_id}/rotate-secret \
  -H "Authorization: Bearer $DALSTON_API_KEY"
# → Returns new signing_secret (old one immediately invalid)
```

---

## Checkpoint

- [ ] **Database models** for webhook_endpoints and webhook_deliveries
- [ ] **CRUD API** at `/v1/webhooks` with `webhooks` scope
- [ ] **Per-endpoint signing secrets** with `whsec_` prefix
- [ ] **Persistent delivery worker** with crash-resilient retries
- [ ] **Event fan-out** to all matching endpoints per tenant
- [ ] **Delivery log** queryable with status filter
- [ ] **Manual retry** for failed deliveries
- [ ] **Backward compat** — per-job `webhook_url` still works
- [ ] **Deprecation path** — `ALLOW_PER_JOB_WEBHOOKS` config flag
- [ ] **E2E tests** covering full flow

**Next**: TBD

See also: [ADR-007: Admin Webhook Design](../../decisions/ADR-007-admin-webhook-design.md), [Implementation Plan](../impl/M21-21.1-admin-webhooks.md)
