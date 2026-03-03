# Endpoint Security Classification (M45)

This document provides a comprehensive classification of all API endpoints with their authentication and authorization requirements.

## Security Modes

The `DALSTON_SECURITY_MODE` environment variable controls authentication enforcement:

| Mode | Description | Use Case |
|------|-------------|----------|
| `none` | No auth checks | Development only |
| `api_key` | API key validation (default) | Production |
| `user` | Future user-based auth | Not yet implemented |

## Scope Definitions

| Scope | Granted Permissions |
|-------|---------------------|
| `admin` | All permissions |
| `jobs:read` | Read jobs, sessions, models |
| `jobs:write` | Create jobs, delete own jobs |
| `realtime` | Create realtime sessions |
| `webhooks` | Manage webhooks |

---

## Public Endpoints (No Auth Required)

These endpoints are accessible without authentication.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check for load balancers |
| `/healthz` | GET | Kubernetes health probe |
| `/ready` | GET | Readiness probe |
| `/metrics` | GET | Prometheus metrics |
| `/docs` | GET | OpenAPI documentation |
| `/redoc` | GET | ReDoc documentation |
| `/openapi.json` | GET | OpenAPI schema |
| `/` | GET | Root endpoint with API info |

---

## Optional Auth Endpoints

These endpoints work without authentication but provide limited data. With authentication, they may provide more details or tenant-specific data.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/models` | GET | List available models |
| `/v1/models/{model_id}` | GET | Get model details |

---

## Protected Endpoints

### Transcription API (`/v1/audio/transcriptions`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/audio/transcriptions` | POST | `jobs:write` | Create transcription job (rate limited) |
| `/v1/audio/transcriptions` | GET | `jobs:read` | List transcription jobs |
| `/v1/audio/transcriptions/{job_id}` | GET | `jobs:read` | Get job details |
| `/v1/audio/transcriptions/{job_id}` | PATCH | `jobs:write` | Update job metadata |
| `/v1/audio/transcriptions/{job_id}` | DELETE | `jobs:write` | Delete job |
| `/v1/audio/transcriptions/{job_id}/transcript` | GET | `jobs:read` | Get transcript |
| `/v1/audio/transcriptions/{job_id}/transcript/{format}` | GET | `jobs:read` | Get transcript in format |
| `/v1/audio/transcriptions/{job_id}/tasks` | GET | `jobs:read` | List job tasks |
| `/v1/audio/transcriptions/{job_id}/cancel` | POST | `jobs:write` | Cancel job |
| `/v1/audio/transcriptions/{job_id}/retry` | POST | `jobs:write` | Retry failed job |
| `/v1/audio/transcriptions/batch` | DELETE | `jobs:write` | Bulk delete jobs |

### ElevenLabs Compatible API (`/v1/speech-to-text`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/speech-to-text` | POST | `jobs:write` | Create transcription (rate limited) |
| `/v1/speech-to-text/{job_id}` | GET | `jobs:read` | Get job status |
| `/v1/speech-to-text/{job_id}/download/{format}` | GET | `jobs:read` | Download transcript |

### OpenAI Compatible API (`/v1/audio/translations`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/audio/translations` | POST | `jobs:write` | Create translation job (rate limited) |

### Task Observability (`/v1/audio/transcriptions/{job_id}/tasks`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/audio/transcriptions/{job_id}/tasks` | GET | `jobs:read` | List tasks |
| `/v1/audio/transcriptions/{job_id}/tasks/{task_id}` | GET | `jobs:read` | Get task details |

### Job Statistics (`/v1/jobs`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/jobs/stats` | GET | `jobs:read` | Get job statistics |

### Realtime Transcription

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/audio/transcriptions/stream` | WebSocket | `realtime` | Dalston native realtime |
| `/v1/speech-to-text/realtime` | WebSocket | `realtime` | ElevenLabs compatible |
| `/v1/realtime` | WebSocket | `realtime` | OpenAI compatible |

### Realtime Status (`/v1/realtime`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/realtime/status` | GET | `jobs:read` | Pool status |
| `/v1/realtime/workers` | GET | `jobs:read` | Worker list |
| `/v1/realtime/workers/{worker_id}` | GET | `jobs:read` | Worker details |

### Realtime Sessions (`/v1/realtime/sessions`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/realtime/sessions` | GET | `jobs:read` | List sessions |
| `/v1/realtime/sessions/{session_id}` | GET | `jobs:read` | Get session |
| `/v1/realtime/sessions/{session_id}` | DELETE | `jobs:read` | Delete session |
| `/v1/realtime/sessions/{session_id}/transcript` | GET | `jobs:read` | Get transcript |
| `/v1/realtime/sessions/{session_id}/audio` | GET | `jobs:read` | Get audio URL |
| `/v1/realtime/sessions/{session_id}/messages` | GET | `jobs:read` | Get messages |

### Engine Discovery (`/v1/engines`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/engines` | GET | `jobs:read` | List running engines |
| `/v1/engines/{engine_id}` | GET | `jobs:read` | Get engine details |

### Model Management (`/v1/models`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/models` | GET | (optional) | List models (public catalog) |
| `/v1/models/{model_id}` | GET | (optional) | Get model details |
| `/v1/models/{model_id}/capabilities` | GET | (optional) | Get capabilities |
| `/v1/models/{model_id}/pull` | POST | `admin` | Download model from HuggingFace |
| `/v1/models/{model_id}` | DELETE | `admin` | Remove model files |
| `/v1/models/sync` | POST | `admin` | Sync registry with disk |
| `/v1/models/hf/resolve` | POST | `admin` | Resolve HuggingFace model |
| `/v1/models/hf/mappings` | GET | `jobs:read` | Get HF mappings |
| `/v1/models/hf/capabilities` | GET | (optional) | Get HF model capabilities |

### Webhook Management (`/v1/webhooks`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/webhooks` | POST | `webhooks` | Create webhook endpoint |
| `/v1/webhooks` | GET | `webhooks` | List webhooks |
| `/v1/webhooks/{endpoint_id}` | GET | `webhooks` | Get webhook |
| `/v1/webhooks/{endpoint_id}` | PATCH | `webhooks` | Update webhook |
| `/v1/webhooks/{endpoint_id}` | DELETE | `webhooks` | Delete webhook |
| `/v1/webhooks/{endpoint_id}/enable` | POST | `webhooks` | Enable webhook |
| `/v1/webhooks/{endpoint_id}/deliveries` | GET | `webhooks` | List deliveries |
| `/v1/webhooks/{endpoint_id}/test` | POST | `webhooks` | Test webhook |

### Audit Logs (`/v1/audit`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/audit/logs` | GET | `admin` | List audit entries |
| `/v1/audit/logs/{entry_id}` | GET | `admin` | Get audit entry |

### PII Detection (`/v1/pii`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/v1/pii/entity-types` | GET | `jobs:read` | List entity types |

---

## Admin Console API (`/api/console`)

All console endpoints require `admin` scope.

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/api/console/dashboard` | GET | `admin` | Dashboard statistics |
| `/api/console/jobs` | GET | `admin` | List all jobs |
| `/api/console/jobs/{job_id}` | GET | `admin` | Get job details |
| `/api/console/jobs/stats` | GET | `admin` | Job statistics |
| `/api/console/realtime/sessions` | GET | `admin` | List sessions |
| `/api/console/realtime/sessions/{session_id}` | GET | `admin` | Get session |
| `/api/console/jobs/{job_id}` | DELETE | `admin` | Delete job |
| `/api/console/jobs/{job_id}/retry` | POST | `admin` | Retry job |
| `/api/console/settings/{namespace}` | GET | `admin` | Get settings |
| `/api/console/settings` | GET | `admin` | Get all settings |
| `/api/console/settings/{namespace}` | PATCH | `admin` | Update settings |
| `/api/console/settings/export` | POST | `admin` | Export settings |
| `/api/console/engines` | GET | `admin` | List engines |

---

## Auth API (`/auth`)

| Endpoint | Method | Required Scope | Notes |
|----------|--------|----------------|-------|
| `/auth/keys` | POST | `admin` | Create API key |
| `/auth/keys` | GET | `admin` | List API keys |
| `/auth/keys/{key_id}` | GET | `admin` | Get API key |
| `/auth/keys/{key_id}` | DELETE | `admin` | Revoke API key |
| `/auth/session` | GET | (any auth) | Get current session info |
| `/auth/session/token` | POST | (any auth) | Create session token |

---

## Error Responses

Security-related HTTP responses:

| Status | Error | Description |
|--------|-------|-------------|
| 401 | `authentication_failed` | Missing or invalid credentials |
| 403 | `authorization_failed` | Insufficient permissions |
| 404 | `resource_not_found` | Resource not found or not accessible (anti-enumeration) |
| 429 | `rate_limit_exceeded` | Rate limit exceeded |

### Response Format

```json
{
  "detail": "Human-readable error message",
  "code": "error_code",
  "required_permission": "permission:name"
}
```

---

## Implementation Notes

1. **Deny-by-default**: All endpoints require authentication unless explicitly listed in the public allowlist.

2. **Anti-enumeration**: Resource access failures return 404 (not 403) to prevent information leakage.

3. **Rate limiting**: Write operations are rate-limited per tenant. Limits are configurable via `DALSTON_RATE_LIMIT_*` settings.

4. **Ownership tracking**: Jobs, sessions, and webhooks track `created_by_key_id` for per-key isolation (Phase 3).

5. **Audit logging**: Permission denials and auth failures are logged to the audit trail.
