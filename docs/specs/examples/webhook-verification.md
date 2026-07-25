# Webhook verification examples

Dalston follows the [Standard Webhooks](https://www.standardwebhooks.com/)
signing convention. Always verify the raw request body before parsing it.

Each delivery includes:

| Header | Purpose |
| --- | --- |
| `webhook-id` | Unique message ID and replay/idempotency key |
| `webhook-timestamp` | Unix timestamp |
| `webhook-signature` | One or more `v1,<base64>` signatures |

The signed content is:

```text
<webhook-id>.<webhook-timestamp>.<raw-body>
```

The JSON body uses the Standard Webhooks envelope:

```json
{
  "object": "event",
  "id": "evt_...",
  "type": "transcription.completed",
  "created_at": 1784912400,
  "data": {
    "transcription_id": "5e0f..."
  }
}
```

Supported event types are `transcription.completed`,
`transcription.failed`, and `transcription.cancelled`.

## Python SDK

Use the SDK helper instead of implementing the cryptography yourself:

<!-- doc-test: webhook-handler -->

```python
from dalston_sdk import (
    WebhookEventType,
    WebhookVerificationError,
    parse_webhook_payload,
    verify_webhook_signature,
)


def handle_webhook(
    headers: dict[str, str],
    body: bytes,
    secret: str,
) -> tuple[int, str]:
    try:
        valid = verify_webhook_signature(
            payload=body,
            signature=headers["webhook-signature"],
            msg_id=headers["webhook-id"],
            timestamp=headers["webhook-timestamp"],
            secret=secret,
            max_age=300,
        )
    except (KeyError, WebhookVerificationError):
        return 401, "invalid webhook"
    if not valid:
        return 401, "invalid webhook"

    event = parse_webhook_payload(body)
    if event.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        print(f"completed: {event.transcription_id}")
    elif event.type == WebhookEventType.TRANSCRIPTION_FAILED:
        print(f"failed: {event.transcription_id}")

    return 200, "ok"
```

`verify_webhook_signature()` returns `True` for a valid signature, `False` for
a well-formed signature that does not match, and raises
`WebhookVerificationError` for malformed or stale deliveries.

## FastAPI

```python
from fastapi import Depends, FastAPI
from dalston_sdk import (
    WebhookEventType,
    WebhookPayload,
    fastapi_webhook_dependency,
)

app = FastAPI()
verify = fastapi_webhook_dependency(secret="whsec_...", max_age=300)


@app.post("/webhooks/dalston")
async def receive_webhook(
    event: WebhookPayload = Depends(verify),
) -> dict[str, bool]:
    if event.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
        print(event.transcription_id, event.data)
    return {"accepted": True}
```

The dependency returns HTTP 401 for missing or invalid signature headers and
HTTP 400 for an invalid event envelope.

## Node.js

This minimal example accepts a single `v1` signature. Production code should
also tolerate multiple space-delimited signatures during secret rotation.

```javascript
const crypto = require('node:crypto');
const express = require('express');

const app = express();
const secret = process.env.DALSTON_WEBHOOK_SECRET;

app.use('/webhooks/dalston', express.raw({ type: 'application/json' }));

function secretBytes(value) {
  if (!value.startsWith('whsec_')) return Buffer.from(value, 'utf8');
  const encoded = value.slice(6).replace(/-/g, '+').replace(/_/g, '/');
  return Buffer.from(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '='), 'base64');
}

function verify(body, messageId, timestamp, signature) {
  const signed = Buffer.from(`${messageId}.${timestamp}.${body.toString('utf8')}`);
  const expected = crypto.createHmac('sha256', secretBytes(secret)).update(signed).digest();
  const supplied = Buffer.from(signature.replace(/^v1,/, ''), 'base64');
  return supplied.length === expected.length &&
    crypto.timingSafeEqual(supplied, expected);
}

app.post('/webhooks/dalston', (req, res) => {
  const messageId = req.get('webhook-id');
  const timestamp = req.get('webhook-timestamp');
  const signature = req.get('webhook-signature');

  if (!messageId || !timestamp || !signature) return res.sendStatus(401);
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) {
    return res.sendStatus(401);
  }
  if (!verify(req.body, messageId, timestamp, signature)) {
    return res.sendStatus(401);
  }

  const event = JSON.parse(req.body.toString('utf8'));
  console.log(event.type, event.data.transcription_id);
  res.json({ accepted: true });
});
```

## Operational guidance

- Store the signing secret outside source control.
- Use `webhook-id` as an idempotency key; deliveries may be retried.
- Return a 2xx response quickly and move expensive work to a queue.
- Reject stale timestamps and verify before JSON parsing.
- During secret rotation, allow all currently active signing secrets.

See [Webhooks](../batch/WEBHOOKS.md) for delivery and retry behavior.
