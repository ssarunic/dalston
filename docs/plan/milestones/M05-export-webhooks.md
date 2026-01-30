# M5: Export Formats & Webhooks

| | |
|---|---|
| **Goal** | Export transcripts in various formats, support async webhooks |
| **Duration** | 2 days |
| **Dependencies** | M4 complete |
| **Deliverable** | SRT/VTT downloads, webhook notifications |

## User Story

> *"As a user, I can download my transcript as an SRT file for subtitles."*

> *"As a developer, I receive a webhook when my transcription is complete."*

---

## Steps

### 5.1: Export Endpoints

**New endpoint:**

```
GET /v1/audio/transcriptions/{job_id}/export/{format}
```

**Parameters:**

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `format` | string | required | `srt`, `vtt`, `txt`, `json` |
| `include_speakers` | bool | true | Include speaker labels in output |
| `max_line_length` | int | 42 | Word wrap for subtitles |

**Deliverables:**

- Return 400 if job not completed
- Set appropriate Content-Type and Content-Disposition headers
- Support all four export formats

---

### 5.2: Export Generators

**Deliverables:**

- **SRT format**: Sequential numbering, `00:00:01,500` timestamps, optional `[SPEAKER_00]` prefix
- **VTT format**: `WEBVTT` header, `00:00:01.500` timestamps, `<v SPEAKER_00>` voice tags
- **TXT format**: Plain text with speaker labels on change, word-wrapped
- **JSON format**: Pass through transcript object

---

### 5.3: Webhook Support

**Job creation changes:**

| Parameter | Type | Description |
| --- | --- | --- |
| `webhook_url` | string | URL to POST on completion/failure |
| `webhook_metadata` | JSON | Custom data echoed back in webhook |

**Deliverables:**

- Store webhook_url and webhook_metadata in job record
- Trigger webhook on job completion or failure

---

### 5.4: Webhook Delivery

**Webhook payload:**

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

**Headers:**

| Header | Description |
| --- | --- |
| `X-Dalston-Signature` | `sha256={hmac_hex}` |
| `X-Dalston-Timestamp` | Unix timestamp |

**Deliverables:**

- Sign payload with HMAC-SHA256: `{timestamp}.{json_payload}`
- Retry 3 times with exponential backoff (1s, 2s, 4s)
- Log delivery status

See [Webhook Verification Examples](../../specs/examples/webhook-verification.md) for client-side verification code.

---

## Verification

```bash
# Export as SRT
curl http://localhost:8000/v1/audio/transcriptions/job_xyz/export/srt \
  --output transcript.srt

cat transcript.srt
# 1
# 00:00:00,000 --> 00:00:02,500
# [SPEAKER_00] Welcome to the show.

# Export as VTT
curl http://localhost:8000/v1/audio/transcriptions/job_xyz/export/vtt
# WEBVTT
#
# 00:00:00.000 --> 00:00:02.500
# <v SPEAKER_00>Welcome to the show.

# Submit with webhook
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@audio.mp3" \
  -F "webhook_url=https://my-server.com/webhooks/dalston" \
  -F 'webhook_metadata={"user_id": "123"}'
```

---

## Checkpoint

- [x] **SRT export** with proper timestamp format
- [x] **VTT export** with speaker voice tags
- [x] **TXT export** with speaker labels
- [x] **Webhooks** with HMAC signature
- [x] **Retry logic** for failed deliveries

**Next**: [M6: Real-Time MVP](M06-realtime-mvp.md) — Stream audio, get live transcripts

---

## Implementation Notes

**Completed**: January 2026

### Files Changed

| File | Description |
| ---- | ----------- |
| `dalston/gateway/services/export.py` | Export service with SRT, VTT, TXT, JSON generators |
| `dalston/gateway/services/webhook.py` | Webhook service with HMAC signing, retry logic, SSRF protection |
| `dalston/gateway/api/v1/transcription.py` | Export endpoint, webhook_url/metadata parameters |
| `dalston/gateway/api/v1/speech_to_text.py` | ElevenLabs-compatible export endpoint |
| `dalston/gateway/dependencies.py` | FastAPI dependency injection for services |
| `dalston/orchestrator/handlers.py` | Publishes job.completed/failed events for webhooks |
| `dalston/orchestrator/main.py` | Handles webhook delivery on job events |
| `dalston/common/events.py` | Event publishers for job completion/failure |
| `dalston/config.py` | Webhook constants (WEBHOOK_METADATA_MAX_SIZE, WEBHOOK_SECRET_DEFAULT) |
| `dalston/db/models.py` | Added webhook_metadata JSONB column |
| `alembic/versions/20260130_0001_add_webhook_metadata.py` | Migration for webhook_metadata |

### Test Coverage

- **118 tests total** for M05 features
- `tests/unit/test_export.py` — 66 tests for export formats
- `tests/unit/test_webhook.py` — 42 tests for webhook signing, delivery, retry, URL validation
- `tests/integration/test_webhook_e2e.py` — 10 E2E tests

### Security Features

- **SSRF Protection**: Webhook URLs are validated to block private/internal IPs
- **HMAC-SHA256 Signing**: Signature format `sha256={hmac_hex}` over `{timestamp}.{payload}`
- **Size Limits**: webhook_metadata limited to 16KB

### Future Improvements

1. **Webhook retry persistence**: Current retries are in-memory. If orchestrator crashes mid-retry, webhook is lost. Consider Redis-based persistent retry queue.
2. **Move WebhookService to shared location**: Currently creates orchestrator → gateway dependency. Consider `dalston/services/` for shared services.
3. **API endpoint integration tests**: Add tests for export endpoints themselves (not just service layer).
