# Webhook Verification Examples

Code examples for verifying Dalston webhook signatures.

See [API Reference](../batch/API.md) for webhook configuration.

---

## Overview

Dalston signs webhook payloads using HMAC-SHA256. Each webhook request includes:

| Header | Description |
| --- | --- |
| `X-Dalston-Signature` | HMAC-SHA256 signature: `sha256={hex_digest}` |
| `X-Dalston-Timestamp` | Unix timestamp when the webhook was sent |

The signature is computed over: `{timestamp}.{payload}`

---

## Python

```python
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

WEBHOOK_SECRET = "whsec_your_secret_here"

def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    secret: str
) -> bool:
    """Verify Dalston webhook signature."""
    signed_payload = f"{timestamp}.{payload.decode()}"
    expected = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


@app.post("/webhooks/dalston")
async def handle_webhook(request: Request):
    # Get headers
    signature = request.headers.get("X-Dalston-Signature")
    timestamp = request.headers.get("X-Dalston-Timestamp")

    if not signature or not timestamp:
        raise HTTPException(status_code=400, detail="Missing signature headers")

    # Verify timestamp is recent (prevent replay attacks)
    import time
    if abs(time.time() - int(timestamp)) > 300:  # 5 minute tolerance
        raise HTTPException(status_code=400, detail="Timestamp too old")

    # Verify signature
    payload = await request.body()
    if not verify_webhook_signature(payload, signature, timestamp, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Process webhook
    data = await request.json()
    event = data.get("event")

    if event == "transcription.completed":
        transcription_id = data["transcription_id"]
        text = data["text"]
        print(f"Transcription {transcription_id} completed: {text[:100]}...")

    elif event == "transcription.failed":
        transcription_id = data["transcription_id"]
        error = data["error"]
        print(f"Transcription {transcription_id} failed: {error}")

    return {"status": "ok"}
```

---

## Node.js / Express

```javascript
const express = require('express');
const crypto = require('crypto');

const app = express();
const WEBHOOK_SECRET = 'whsec_your_secret_here';

// Must use raw body for signature verification
app.use('/webhooks/dalston', express.raw({ type: 'application/json' }));

function verifyWebhookSignature(payload, signature, timestamp, secret) {
  const signedPayload = `${timestamp}.${payload.toString()}`;
  const expected = crypto
    .createHmac('sha256', secret)
    .update(signedPayload)
    .digest('hex');

  const expectedSignature = `sha256=${expected}`;

  // Constant-time comparison
  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expectedSignature)
  );
}

app.post('/webhooks/dalston', (req, res) => {
  const signature = req.headers['x-dalston-signature'];
  const timestamp = req.headers['x-dalston-timestamp'];

  if (!signature || !timestamp) {
    return res.status(400).json({ error: 'Missing signature headers' });
  }

  // Verify timestamp is recent
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - parseInt(timestamp)) > 300) {
    return res.status(400).json({ error: 'Timestamp too old' });
  }

  // Verify signature
  if (!verifyWebhookSignature(req.body, signature, timestamp, WEBHOOK_SECRET)) {
    return res.status(401).json({ error: 'Invalid signature' });
  }

  // Process webhook
  const data = JSON.parse(req.body.toString());

  switch (data.event) {
    case 'transcription.completed':
      console.log(`Completed: ${data.transcription_id}`);
      console.log(`Text: ${data.text.substring(0, 100)}...`);
      break;

    case 'transcription.failed':
      console.error(`Failed: ${data.transcription_id}`, data.error);
      break;
  }

  res.json({ status: 'ok' });
});

app.listen(3000);
```

---

## Go

```go
package main

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "io"
    "math"
    "net/http"
    "strconv"
    "time"
)

const webhookSecret = "whsec_your_secret_here"

func verifyWebhookSignature(payload []byte, signature, timestamp, secret string) bool {
    signedPayload := fmt.Sprintf("%s.%s", timestamp, string(payload))

    mac := hmac.New(sha256.New, []byte(secret))
    mac.Write([]byte(signedPayload))
    expected := "sha256=" + hex.EncodeToString(mac.Sum(nil))

    return hmac.Equal([]byte(signature), []byte(expected))
}

func webhookHandler(w http.ResponseWriter, r *http.Request) {
    signature := r.Header.Get("X-Dalston-Signature")
    timestamp := r.Header.Get("X-Dalston-Timestamp")

    if signature == "" || timestamp == "" {
        http.Error(w, "Missing signature headers", http.StatusBadRequest)
        return
    }

    // Verify timestamp
    ts, _ := strconv.ParseInt(timestamp, 10, 64)
    if math.Abs(float64(time.Now().Unix()-ts)) > 300 {
        http.Error(w, "Timestamp too old", http.StatusBadRequest)
        return
    }

    // Read body
    payload, err := io.ReadAll(r.Body)
    if err != nil {
        http.Error(w, "Failed to read body", http.StatusBadRequest)
        return
    }

    // Verify signature
    if !verifyWebhookSignature(payload, signature, timestamp, webhookSecret) {
        http.Error(w, "Invalid signature", http.StatusUnauthorized)
        return
    }

    // Process webhook
    var data map[string]interface{}
    json.Unmarshal(payload, &data)

    event := data["event"].(string)
    switch event {
    case "transcription.completed":
        fmt.Printf("Completed: %s\n", data["transcription_id"])
    case "transcription.failed":
        fmt.Printf("Failed: %s - %v\n", data["transcription_id"], data["error"])
    }

    w.Header().Set("Content-Type", "application/json")
    w.Write([]byte(`{"status": "ok"}`))
}

func main() {
    http.HandleFunc("/webhooks/dalston", webhookHandler)
    http.ListenAndServe(":3000", nil)
}
```

---

## Ruby / Sinatra

```ruby
require 'sinatra'
require 'openssl'
require 'json'

WEBHOOK_SECRET = 'whsec_your_secret_here'

def verify_webhook_signature(payload, signature, timestamp, secret)
  signed_payload = "#{timestamp}.#{payload}"
  expected = OpenSSL::HMAC.hexdigest('sha256', secret, signed_payload)
  expected_signature = "sha256=#{expected}"

  Rack::Utils.secure_compare(signature, expected_signature)
end

post '/webhooks/dalston' do
  signature = request.env['HTTP_X_DALSTON_SIGNATURE']
  timestamp = request.env['HTTP_X_DALSTON_TIMESTAMP']

  halt 400, { error: 'Missing signature headers' }.to_json unless signature && timestamp

  # Verify timestamp
  halt 400, { error: 'Timestamp too old' }.to_json if (Time.now.to_i - timestamp.to_i).abs > 300

  # Verify signature
  payload = request.body.read
  halt 401, { error: 'Invalid signature' }.to_json unless verify_webhook_signature(
    payload, signature, timestamp, WEBHOOK_SECRET
  )

  # Process webhook
  data = JSON.parse(payload)

  case data['event']
  when 'transcription.completed'
    puts "Completed: #{data['transcription_id']}"
  when 'transcription.failed'
    puts "Failed: #{data['transcription_id']} - #{data['error']}"
  end

  { status: 'ok' }.to_json
end
```

---

## Security Best Practices

1. **Always verify signatures** — Never process webhooks without verification
2. **Check timestamps** — Reject webhooks older than 5 minutes to prevent replay attacks
3. **Use constant-time comparison** — Prevents timing attacks on signature verification
4. **Store secrets securely** — Use environment variables, not hardcoded values
5. **Return quickly** — Process webhooks asynchronously; return 200 within 30 seconds
6. **Handle duplicates** — Webhooks may be retried; ensure idempotent processing
