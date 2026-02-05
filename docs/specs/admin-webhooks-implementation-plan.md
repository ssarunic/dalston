# Admin-Registered Webhooks: Implementation Plan

This is a step-by-step plan for implementing admin-registered webhooks in Dalston.
Each step is a coherent, testable unit of work. Complete them in order. Run `pytest`
after each step to ensure nothing is broken.

Read the companion documents before starting:
- [Strategy](admin-webhooks-strategy.md) — why we are doing this
- [Design & Decisions](admin-webhooks-design.md) — technical choices and reasoning

---

## Step 1: Database Models & Migration

### Files to create/modify

- `dalston/db/models.py` — Add `WebhookEndpointModel` and `WebhookDeliveryModel`
- `alembic/versions/YYYYMMDD_0001_add_webhook_endpoints.py` — Migration

### What to do

Add two new SQLAlchemy models to `dalston/db/models.py`:

**WebhookEndpointModel:**
```
__tablename__ = "webhook_endpoints"

id: UUID PK, server_default=gen_random_uuid()
tenant_id: UUID FK → tenants.id, NOT NULL, indexed
url: Text, NOT NULL
description: String(255), nullable
events: ARRAY(Text), NOT NULL  (use sqlalchemy ARRAY(String))
signing_secret: Text, NOT NULL
is_active: Boolean, NOT NULL, default=True
created_at: TIMESTAMPTZ, NOT NULL, server_default=now()
updated_at: TIMESTAMPTZ, NOT NULL, server_default=now(), onupdate=now()
```

Add relationship on `TenantModel`: `webhook_endpoints: Mapped[list["WebhookEndpointModel"]]`

**WebhookDeliveryModel:**
```
__tablename__ = "webhook_deliveries"

id: UUID PK, server_default=gen_random_uuid()
endpoint_id: UUID FK → webhook_endpoints.id (ON DELETE CASCADE), nullable
    (nullable because per-job webhooks have no registered endpoint)
job_id: UUID FK → jobs.id (ON DELETE SET NULL), nullable
event_type: String(50), NOT NULL
payload: JSONB, NOT NULL
url_override: Text, nullable
    (used for per-job webhook_url when endpoint_id is NULL)
status: String(20), NOT NULL, default="pending", indexed
attempts: Integer, NOT NULL, default=0
last_attempt_at: TIMESTAMPTZ, nullable
last_status_code: Integer, nullable
last_error: Text, nullable
next_retry_at: TIMESTAMPTZ, nullable, indexed
created_at: TIMESTAMPTZ, NOT NULL, server_default=now()
```

Add composite index on `(status, next_retry_at)` for the delivery worker poller.
Add index on `(endpoint_id, created_at DESC)` for the delivery log query.

Create the Alembic migration. Follow the existing naming convention:
`YYYYMMDD_0001_add_webhook_endpoints.py`. Use `op.create_table` for both tables
and `op.create_index` for the composite indexes.

### Verification

- `alembic upgrade head` succeeds (or if no live database, the migration file has
  correct `upgrade()` / `downgrade()` functions)
- `pytest` still passes (no regressions)

---

## Step 2: Webhook Endpoint Service (CRUD)

### Files to create/modify

- `dalston/gateway/services/webhook_endpoints.py` — New service class
- `dalston/config.py` — Add `ALLOW_PER_JOB_WEBHOOKS` setting

### What to do

Create `WebhookEndpointService` class with these methods:

```python
class WebhookEndpointService:
    async def create_endpoint(
        self, db, tenant_id, url, events, description=None
    ) -> tuple[WebhookEndpointModel, str]:
        """
        1. Validate url with existing validate_webhook_url()
        2. Validate events against allowed set:
           {"transcription.completed", "transcription.failed", "*"}
        3. Generate signing_secret: secrets.token_urlsafe(32), prefix "whsec_"
        4. Insert row, return (model, raw_signing_secret)
        """

    async def list_endpoints(
        self, db, tenant_id, is_active=None
    ) -> list[WebhookEndpointModel]:
        """Query with optional is_active filter, ordered by created_at DESC."""

    async def get_endpoint(
        self, db, endpoint_id, tenant_id
    ) -> WebhookEndpointModel | None:
        """Fetch by ID, verify tenant_id matches."""

    async def update_endpoint(
        self, db, endpoint_id, tenant_id, **fields
    ) -> WebhookEndpointModel | None:
        """
        Partial update. Allowed fields: url, events, description, is_active.
        If url changes, re-validate with validate_webhook_url().
        If events changes, validate against allowed set.
        """

    async def delete_endpoint(
        self, db, endpoint_id, tenant_id
    ) -> bool:
        """Hard delete. CASCADE deletes delivery log rows."""

    async def rotate_secret(
        self, db, endpoint_id, tenant_id
    ) -> tuple[WebhookEndpointModel, str] | None:
        """Generate new signing_secret, return (model, raw_secret)."""
```

Add to `dalston/config.py`:
```python
allow_per_job_webhooks: bool = Field(
    default=True,
    alias="ALLOW_PER_JOB_WEBHOOKS",
    description="Allow webhook_url parameter on job submission (legacy behavior)",
)
```

### Verification

- Write unit tests in `tests/unit/test_webhook_endpoints.py`
- Test CRUD operations, validation (bad URLs, invalid event types), secret generation
- `pytest` passes

---

## Step 3: Webhook Admin API Endpoints

### Files to create/modify

- `dalston/gateway/api/v1/webhooks.py` — New router
- `dalston/gateway/api/v1/router.py` — Register the new router
- `dalston/gateway/dependencies.py` — Add dependency for WebhookEndpointService
- `dalston/gateway/models/responses.py` — Add response models (or create
  `dalston/gateway/models/webhook_responses.py` if the file is getting large)

### What to do

Create a new FastAPI router at `/v1/webhooks` with these endpoints:

```
POST   /v1/webhooks                                    → create_webhook_endpoint
GET    /v1/webhooks                                    → list_webhook_endpoints
GET    /v1/webhooks/{endpoint_id}                      → get_webhook_endpoint
PATCH  /v1/webhooks/{endpoint_id}                      → update_webhook_endpoint
DELETE /v1/webhooks/{endpoint_id}                      → delete_webhook_endpoint
POST   /v1/webhooks/{endpoint_id}/rotate-secret        → rotate_endpoint_secret
GET    /v1/webhooks/{endpoint_id}/deliveries           → list_endpoint_deliveries
POST   /v1/webhooks/{endpoint_id}/deliveries/{id}/retry → retry_delivery
```

All endpoints require `webhooks` scope (use existing `Scope.WEBHOOKS` from auth.py).
Create a `RequireWebhooks` dependency similar to `RequireJobsRead`/`RequireJobsWrite`.

**Request/response models (Pydantic):**

```python
class CreateWebhookRequest(BaseModel):
    url: HttpUrl
    events: list[str]  # validated against allowed set
    description: str | None = None

class WebhookEndpointResponse(BaseModel):
    id: UUID
    url: str
    events: list[str]
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # signing_secret is NOT included here

class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    signing_secret: str  # only returned on creation and rotation

class UpdateWebhookRequest(BaseModel):
    url: HttpUrl | None = None
    events: list[str] | None = None
    description: str | None = None
    is_active: bool | None = None

class WebhookDeliveryResponse(BaseModel):
    id: UUID
    endpoint_id: UUID | None
    job_id: UUID | None
    event_type: str
    status: str
    attempts: int
    last_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime

class DeliveryListResponse(BaseModel):
    deliveries: list[WebhookDeliveryResponse]
    total: int
    limit: int
    offset: int
```

Register the router in `dalston/gateway/api/v1/router.py` the same way the existing
transcription and jobs routers are registered.

### Verification

- Write integration tests in `tests/integration/test_webhook_admin_api.py`
- Test all CRUD endpoints, auth (requires `webhooks` scope), validation errors
- Test that `signing_secret` is only returned on create and rotate
- `pytest` passes

---

## Step 4: Persistent Delivery System

### Files to create/modify

- `dalston/gateway/services/webhook.py` — Refactor to support per-endpoint secrets
- `dalston/orchestrator/delivery.py` — New file: delivery worker
- `dalston/orchestrator/main.py` — Integrate delivery worker into event loop

### What to do

**4a. Refactor WebhookService**

Modify `WebhookService` to accept the signing secret as a parameter to `deliver()`
and `sign_payload()` rather than from `__init__`. This allows using different secrets
per endpoint. Keep backward compat: if no secret is passed, fall back to the
instance-level `self.secret`.

**4b. Create delivery worker**

Create `dalston/orchestrator/delivery.py`:

```python
RETRY_DELAYS = [0, 30, 120, 600, 3600]  # seconds: immediate, 30s, 2m, 10m, 1h
MAX_ATTEMPTS = 5
POLL_INTERVAL = 2.0  # seconds
MAX_CONCURRENT = 10

class DeliveryWorker:
    """Polls webhook_deliveries table for pending deliveries and executes them."""

    async def start(self):
        """Run the polling loop. Call as an asyncio task."""

    async def _poll_and_deliver(self):
        """
        SELECT id, endpoint_id, payload, url_override, attempts
        FROM webhook_deliveries
        WHERE status = 'pending' AND next_retry_at <= now()
        ORDER BY next_retry_at
        LIMIT {MAX_CONCURRENT}
        FOR UPDATE SKIP LOCKED

        For each row:
        - Look up signing_secret from endpoint (or use global secret for url_override)
        - Look up url from endpoint (or use url_override)
        - Call WebhookService.deliver()
        - On success: UPDATE status='success', last_attempt_at, last_status_code, attempts
        - On failure:
            - If attempts < MAX_ATTEMPTS:
              UPDATE attempts, last_attempt_at, last_error, last_status_code,
                     next_retry_at = now() + RETRY_DELAYS[attempts]
            - Else:
              UPDATE status='failed', last_attempt_at, last_error, last_status_code
        """

    async def stop(self):
        """Signal the polling loop to stop."""
```

**4c. Integrate into orchestrator**

In `dalston/orchestrator/main.py`:
- Start `DeliveryWorker` as a background task alongside the event loop
- On shutdown, stop the delivery worker gracefully

**4d. Change event dispatch to create delivery rows**

In `_handle_job_webhook()` (or create a new `_create_webhook_deliveries()` function):

```
When job.completed or job.failed fires:
1. Query active webhook_endpoints for tenant WHERE events contains event_type
2. For each endpoint: INSERT webhook_deliveries row
   (endpoint_id, job_id, event_type, payload, status='pending', next_retry_at=now())
3. If job.webhook_url is set (per-job webhook):
   INSERT webhook_deliveries row with endpoint_id=NULL, url_override=job.webhook_url
4. Remove the current direct-delivery code from _handle_job_webhook()
   (delivery is now handled by the DeliveryWorker)
```

### Verification

- Write unit tests in `tests/unit/test_delivery_worker.py`
- Test: delivery row is created on event, worker picks it up, successful delivery
  marks row as success, failed delivery schedules retry with correct delay, max
  retries exhausts to failed status
- Test: `SELECT FOR UPDATE SKIP LOCKED` behavior (mock or integration)
- Test: per-job webhook_url creates delivery row with url_override
- All existing M05 webhook tests should still pass (or be updated minimally)
- `pytest` passes

---

## Step 5: Delivery Log Query & Manual Retry

### Files to create/modify

- `dalston/gateway/services/webhook_endpoints.py` — Add delivery query methods
- `dalston/gateway/api/v1/webhooks.py` — Implement the two remaining endpoints

### What to do

Add to `WebhookEndpointService`:

```python
async def list_deliveries(
    self, db, endpoint_id, tenant_id, status=None, limit=20, offset=0
) -> tuple[list[WebhookDeliveryModel], int]:
    """
    1. Verify endpoint belongs to tenant
    2. Query deliveries for endpoint, ordered by created_at DESC
    3. Optional status filter
    4. Return (rows, total_count)
    """

async def retry_delivery(
    self, db, endpoint_id, delivery_id, tenant_id
) -> WebhookDeliveryModel | None:
    """
    1. Verify endpoint belongs to tenant
    2. Verify delivery belongs to endpoint
    3. Verify delivery status is 'failed'
    4. Reset: status='pending', next_retry_at=now(), do NOT reset attempts counter
    5. Return updated row
    """
```

Implement the `GET /v1/webhooks/{endpoint_id}/deliveries` and
`POST /v1/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry` endpoints using
these service methods.

### Verification

- Add tests for delivery log listing (pagination, status filter)
- Add tests for manual retry (can retry failed, cannot retry pending/success)
- `pytest` passes

---

## Step 6: Per-Job Webhook Deprecation Path

### Files to modify

- `dalston/gateway/api/v1/transcription.py` — Check `ALLOW_PER_JOB_WEBHOOKS`
- `dalston/gateway/api/v1/speech_to_text.py` — Same if it has webhook params

### What to do

In the `create_transcription` endpoint:

```python
if webhook_url and not settings.allow_per_job_webhooks:
    raise HTTPException(
        status_code=400,
        detail="Per-job webhook_url is disabled. "
               "Register webhook endpoints via POST /v1/webhooks instead.",
    )
```

Add a deprecation warning log when `webhook_url` is used even when allowed:

```python
if webhook_url:
    logger.warning(
        "per_job_webhook_deprecated",
        job_id=str(job.id),
        message="Per-job webhook_url is deprecated. Use registered webhook endpoints.",
    )
```

### Verification

- Test that setting `ALLOW_PER_JOB_WEBHOOKS=false` rejects webhook_url parameter
- Test that existing behavior works when `ALLOW_PER_JOB_WEBHOOKS=true` (default)
- `pytest` passes

---

## Step 7: Update SDK & Docs

### Files to create/modify

- `sdk/dalston_sdk/webhook.py` — Update verification helpers for per-endpoint secrets
- `docs/specs/examples/webhook-verification.md` — Update examples
- `docs/plan/milestones/M05-export-webhooks.md` — Add note about admin webhooks
- `docs/specs/admin-webhooks-strategy.md` — Mark as implemented
- `docs/specs/admin-webhooks-design.md` — Mark as implemented

### What to do

Update the SDK's `verify_webhook_signature()` to document that the secret is now
per-endpoint (the function signature doesn't change — it already takes `secret` as
a parameter). Add a note about the `whsec_` prefix.

Update the verification examples to show the new flow:
1. Register endpoint via API
2. Store the returned `signing_secret`
3. Verify incoming webhooks using that secret

Update M05 milestone doc to reference the new admin webhook feature.

### Verification

- Documentation is accurate and consistent
- SDK tests pass
- `pytest` passes

---

## Step 8: Final Integration Test

### Files to create

- `tests/integration/test_admin_webhooks_e2e.py`

### What to do

Write an end-to-end test that exercises the full flow:

1. Create a tenant and API key with `webhooks` scope
2. Register two webhook endpoints (use httpbin or a mock HTTP server)
3. Submit a transcription job (no per-job webhook_url)
4. Simulate job completion (publish `job.completed` event)
5. Verify: delivery rows are created for both endpoints
6. Verify: mock server received both webhook POSTs with correct signatures
7. Verify: delivery log shows success for both
8. Test failure path: register endpoint with unreachable URL, verify retry behavior
9. Test manual retry: after max retries exhausted, call retry endpoint, verify
   delivery is re-attempted

Also test the hybrid case:

1. Register one endpoint
2. Submit job with per-job `webhook_url`
3. Simulate completion
4. Verify: both registered endpoint AND per-job URL receive webhooks
5. Verify: delivery log contains entries for both

### Verification

- All new and existing tests pass
- `pytest --cov=dalston` shows coverage for new code
- `pytest` passes clean

---

## Summary of New/Modified Files

| File | Action | Step |
|------|--------|------|
| `dalston/db/models.py` | Modify (add 2 models) | 1 |
| `alembic/versions/YYYYMMDD_..._add_webhook_endpoints.py` | Create | 1 |
| `dalston/gateway/services/webhook_endpoints.py` | Create | 2 |
| `dalston/config.py` | Modify (add setting) | 2 |
| `dalston/gateway/api/v1/webhooks.py` | Create | 3 |
| `dalston/gateway/api/v1/router.py` | Modify (register router) | 3 |
| `dalston/gateway/dependencies.py` | Modify (add dependency) | 3 |
| `dalston/gateway/models/responses.py` | Modify (add response models) | 3 |
| `dalston/gateway/services/webhook.py` | Modify (per-endpoint secret) | 4 |
| `dalston/orchestrator/delivery.py` | Create | 4 |
| `dalston/orchestrator/main.py` | Modify (integrate worker, change dispatch) | 4 |
| `dalston/gateway/api/v1/transcription.py` | Modify (deprecation check) | 6 |
| `sdk/dalston_sdk/webhook.py` | Modify (docs) | 7 |
| `docs/specs/examples/webhook-verification.md` | Modify | 7 |
| `tests/unit/test_webhook_endpoints.py` | Create | 2 |
| `tests/integration/test_webhook_admin_api.py` | Create | 3 |
| `tests/unit/test_delivery_worker.py` | Create | 4 |
| `tests/integration/test_admin_webhooks_e2e.py` | Create | 8 |
