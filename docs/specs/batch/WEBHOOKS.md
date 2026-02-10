# Webhook API Reference

## Overview

Dalston delivers webhook notifications when transcription jobs complete or fail. Webhooks use the [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) specification for payload format and signature verification.

Register webhook endpoints via the API, and all matching events are automatically delivered with crash-resilient retries.

---

## Authentication

All webhook management endpoints require an API key with `webhooks` scope:

```bash
curl -X POST http://localhost:8000/v1/webhooks \
  -H "Authorization: Bearer dk_your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/webhook", "events": ["*"]}'
```

---

## Webhook Endpoints API

### Create Endpoint

Register a new webhook endpoint. The signing secret is only returned once.

```
POST /v1/webhooks
```

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | Webhook callback URL (must be HTTPS in production) |
| `events` | string[] | Yes | Event types to subscribe to |
| `description` | string | No | Human-readable description (max 255 chars) |

**Allowed Events:**

| Event | Description |
|-------|-------------|
| `transcription.completed` | Job finished successfully |
| `transcription.failed` | Job failed permanently |
| `*` | Wildcard — all events |

**Response:** `201 Created`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "url": "https://example.com/webhook",
  "events": ["transcription.completed", "transcription.failed"],
  "description": "Production handler",
  "is_active": true,
  "signing_secret": "whsec_abc123...",
  "created_at": "2026-02-10T12:00:00Z",
  "updated_at": "2026-02-10T12:00:00Z"
}
```

> **Important:** Store `signing_secret` securely — it's only shown once!

---

### List Endpoints

```
GET /v1/webhooks
```

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `is_active` | boolean | Filter by active status |

**Response:** `200 OK`

```json
{
  "endpoints": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "url": "https://example.com/webhook",
      "events": ["*"],
      "description": "Production handler",
      "is_active": true,
      "disabled_reason": null,
      "consecutive_failures": 0,
      "last_success_at": "2026-02-10T11:55:00Z",
      "created_at": "2026-02-10T10:00:00Z",
      "updated_at": "2026-02-10T10:00:00Z"
    }
  ]
}
```

---

### Get Endpoint

```
GET /v1/webhooks/{endpoint_id}
```

**Response:** `200 OK` (same shape as list item, without `signing_secret`)

---

### Update Endpoint

```
PATCH /v1/webhooks/{endpoint_id}
```

**Request Body:** (all fields optional)

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | New callback URL |
| `events` | string[] | New event subscriptions |
| `description` | string | New description |
| `is_active` | boolean | Enable/disable endpoint |

**Response:** `200 OK`

---

### Delete Endpoint

```
DELETE /v1/webhooks/{endpoint_id}
```

Deletes the endpoint and all its delivery history.

**Response:** `204 No Content`

---

### Rotate Signing Secret

```
POST /v1/webhooks/{endpoint_id}/rotate-secret
```

Generate a new signing secret. The old secret becomes invalid immediately.

**Response:** `200 OK` (includes new `signing_secret`)

---

### List Deliveries

```
GET /v1/webhooks/{endpoint_id}/deliveries
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | — | Filter: `pending`, `success`, `failed` |
| `limit` | int | 20 | Max results (1-100) |
| `offset` | int | 0 | Pagination offset |

**Response:** `200 OK`

```json
{
  "deliveries": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "endpoint_id": "550e8400-e29b-41d4-a716-446655440000",
      "job_id": "770e8400-e29b-41d4-a716-446655440002",
      "event_type": "transcription.completed",
      "status": "success",
      "attempts": 1,
      "last_attempt_at": "2026-02-10T12:00:01Z",
      "last_status_code": 200,
      "last_error": null,
      "created_at": "2026-02-10T12:00:00Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

### Retry Failed Delivery

```
POST /v1/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry
```

Retry a failed webhook delivery. Only works for deliveries with `status: failed`.

**Response:** `200 OK` (delivery object with `status: pending`)

---

## Webhook Payload Format

Dalston uses the [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) specification.

### Headers

| Header | Description |
|--------|-------------|
| `webhook-id` | Unique delivery ID (for deduplication) |
| `webhook-timestamp` | Unix timestamp when sent |
| `webhook-signature` | HMAC-SHA256 signature (`v1,{base64}`) |
| `X-Dalston-Webhook-Id` | Same as `webhook-id` |

### Payload Structure

```json
{
  "object": "event",
  "id": "evt_abc123",
  "type": "transcription.completed",
  "created_at": 1707566400,
  "data": {
    "transcription_id": "job_xyz789",
    "status": "completed",
    "duration": 125.5,
    "webhook_metadata": {"user_id": "123"}
  }
}
```

### Event Types

**`transcription.completed`**

```json
{
  "data": {
    "transcription_id": "job_xyz789",
    "status": "completed",
    "duration": 125.5,
    "webhook_metadata": {...}
  }
}
```

**`transcription.failed`**

```json
{
  "data": {
    "transcription_id": "job_xyz789",
    "status": "failed",
    "error": "Transcription engine failed: CUDA out of memory",
    "webhook_metadata": {...}
  }
}
```

---

## Signature Verification

Verify signatures to ensure webhooks are from Dalston.

### Python (with SDK)

```python
from dalston import verify_webhook_signature, WebhookVerificationError

def handle_webhook(request):
    try:
        is_valid = verify_webhook_signature(
            payload=request.body,
            signature=request.headers["webhook-signature"],
            msg_id=request.headers["webhook-id"],
            timestamp=request.headers["webhook-timestamp"],
            secret="whsec_your_endpoint_secret",
        )
    except WebhookVerificationError as e:
        return Response(status=401, body=str(e))

    # Process the webhook...
```

### Python (manual)

```python
import base64
import hashlib
import hmac
import time

def verify_signature(payload: bytes, signature: str, msg_id: str,
                     timestamp: str, secret: str, max_age: int = 300) -> bool:
    # Check timestamp freshness
    ts = int(timestamp)
    if abs(time.time() - ts) > max_age:
        return False

    # Verify signature format
    if not signature.startswith("v1,"):
        return False

    provided_sig = base64.b64decode(signature[3:])

    # Standard Webhooks: sign "{msg_id}.{timestamp}.{body}"
    signed_payload = f"{msg_id}.{timestamp}.{payload.decode()}"
    expected = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256,
    ).digest()

    return hmac.compare_digest(expected, provided_sig)
```

### FastAPI Integration

```python
from fastapi import FastAPI, Depends
from dalston import fastapi_webhook_dependency, WebhookPayload, WebhookEventType

app = FastAPI()
verify_webhook = fastapi_webhook_dependency("whsec_your_secret")

@app.post("/webhooks/dalston")
async def handle_webhook(payload: WebhookPayload = Depends(verify_webhook)):
    if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        job_id = payload.data["transcription_id"]
        # Fetch full transcript and process...
```

### Flask Integration

```python
from flask import Flask
from dalston import flask_verify_webhook, WebhookPayload, WebhookEventType

app = Flask(__name__)
verify = flask_verify_webhook("whsec_your_secret")

@app.route("/webhooks/dalston", methods=["POST"])
@verify
def handle_webhook(payload: WebhookPayload):
    if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        job_id = payload.data["transcription_id"]
        # Process...
```

---

## Retry Behavior

Failed deliveries are automatically retried with exponential backoff:

| Attempt | Delay |
|---------|-------|
| 1 (initial) | Immediate |
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 10 minutes |
| 5 | 1 hour |

After 5 failed attempts, the delivery is marked as `failed`. Use the retry API to manually retry.

---

## Auto-Disable

Endpoints are automatically disabled when:

- 10+ consecutive delivery failures, AND
- No successful delivery in the last 7 days

Disabled endpoints show `disabled_reason: "auto_disabled"`. Re-enable via:

```bash
curl -X PATCH http://localhost:8000/v1/webhooks/{id} \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"is_active": true}'
```

---

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `WEBHOOK_SECRET` | (required) | Global signing secret (used when `ALLOW_PER_JOB_WEBHOOKS=true`) |
| `ALLOW_PER_JOB_WEBHOOKS` | `false` | Enable legacy per-job `webhook_url` parameter |
