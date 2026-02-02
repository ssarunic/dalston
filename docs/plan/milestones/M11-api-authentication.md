# M11: API Authentication

| | |
|---|---|
| **Goal** | Secure all API endpoints with API key authentication |
| **Duration** | 2-3 days |
| **Dependencies** | M1 complete (gateway working) |
| **Deliverable** | All endpoints require valid API key, tenant-scoped data isolation |

## User Story

> *"As a self-hoster, I can secure my Dalston instance with API keys and ensure only authorized clients can access it."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTHENTICATION FLOW                               │
│                                                                      │
│  ┌──────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │  Client  │───▶│  Auth Middleware │───▶│  Gateway Endpoints    │  │
│  │          │    │  ────────────────│    │  ───────────────────  │  │
│  │  Header: │    │  1. Extract key  │    │  Jobs scoped to       │  │
│  │  Bearer  │    │  2. Validate     │    │  tenant_id from key   │  │
│  │  dk_xxx  │    │  3. Check scopes │    │                       │  │
│  └──────────┘    └──────────────────┘    └───────────────────────┘  │
│                           │                                          │
│                           ▼                                          │
│                  ┌──────────────────┐                               │
│                  │  Redis           │                               │
│                  │  ──────────────  │                               │
│                  │  API Keys (hash) │                               │
│                  │  Rate Limits     │                               │
│                  └──────────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘

WebSocket Authentication:
┌──────────────────────────────────────────────────────────────────────┐
│  ws://host/v1/audio/transcriptions/stream?api_key=dk_xxx&lang=en    │
│                                            ▲                         │
│                            Query param for WebSocket (no headers)    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### API Key

| Field | Type | Description |
|-------|------|-------------|
| `id` | uuid | Unique identifier |
| `key_hash` | string | SHA256 hash (never store plaintext) |
| `prefix` | string | First 10 chars for display ("dk_abc1234") |
| `name` | string | Human-readable name |
| `tenant_id` | string | Tenant scope ("default" initially) |
| `scopes` | list[string] | Permission scopes |
| `rate_limit` | int \| null | Requests/minute, null = unlimited |
| `created_at` | datetime | Creation timestamp |
| `last_used_at` | datetime \| null | Last usage timestamp |
| `revoked_at` | datetime \| null | Revocation timestamp |

### Scopes

| Scope | Permissions |
|-------|-------------|
| `jobs:read` | GET transcription jobs |
| `jobs:write` | POST/DELETE transcription jobs |
| `realtime` | WebSocket streaming access |
| `webhooks` | Manage webhooks |
| `admin` | All permissions + system management |

---

## Steps

### 11.1: API Key Storage

**Deliverables:**

- `generate_api_key()` - Create key with 256 bits entropy, `dk_` prefix
- `hash_api_key(key)` - SHA256 hash for storage/lookup
- Store keys in Redis by hash for O(1) lookup
- Index by ID for management, by tenant for listing
- Key format: `dk_{32 random urlsafe bytes}`

---

### 11.2: Authentication Middleware

**Deliverables:**

- Extract token from `Authorization: Bearer` header
- Fall back to `api_key` query param for WebSocket
- Validate key against Redis
- Check if revoked
- Enforce rate limit using Redis INCR with 60s expiry
- Attach `api_key` and `tenant_id` to request state

---

### 11.3: Scope Enforcement

**Deliverables:**

- `require_auth` dependency - Validates any API key
- `require_scope(scope)` dependency factory - Requires specific scope
- Admin scope grants all permissions
- Return 403 for missing scope

---

### 11.4: Apply Auth to Routes

**Deliverables:**

- `POST /v1/audio/transcriptions` - Requires `jobs:write`
- `GET /v1/audio/transcriptions` - Requires `jobs:read`
- `GET /v1/audio/transcriptions/{id}` - Requires `jobs:read`, verify tenant
- `DELETE /v1/audio/transcriptions/{id}` - Requires `jobs:write`, verify tenant
- All operations scoped to `api_key.tenant_id`

---

### 11.5: WebSocket Authentication

**Deliverables:**

- Validate `api_key` query param before accepting connection
- Check for `realtime` scope
- Check rate limit
- Close with code 4001 (invalid key), 4003 (missing scope), or 4029 (rate limit)
- Create session scoped to tenant

---

### 11.6: Auth Management Endpoints

| Endpoint | Scope | Description |
|----------|-------|-------------|
| `POST /auth/keys` | admin | Create new API key |
| `GET /auth/keys` | admin | List tenant's API keys |
| `GET /auth/keys/{id}` | admin | Get key details (no secret) |
| `DELETE /auth/keys/{id}` | admin | Revoke API key |
| `GET /auth/me` | any | Get current key info |

---

### 11.7: Bootstrap Admin Key

**Deliverables:**

- CLI command: `dalston create-admin-key --name "Admin"`
- Creates key with `admin` scope in `default` tenant
- Displays full key once (cannot be retrieved later)
- Shows usage example with curl

---

### 11.8: Tenant Scoping

**Deliverables:**

- Add `tenant_id` field to Job model
- Store jobs indexed by tenant in Redis
- `get_job()` verifies tenant ownership
- `list_jobs()` only returns tenant's jobs

---

## Verification

### Create Admin Key

```bash
python -m dalston.gateway.cli create-admin-key --name "My Admin Key"
```

### Test Authentication

```bash
export DALSTON_API_KEY="dk_..."

# ✅ Authenticated request
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@audio.mp3"

# ❌ Missing key → 401
# ❌ Invalid key → 401
```

### Test Key Management

```bash
# Create limited key
curl -X POST http://localhost:8000/auth/keys \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -d '{"name": "Read Only", "scopes": ["jobs:read"]}'

# List keys
curl http://localhost:8000/auth/keys \
  -H "Authorization: Bearer $DALSTON_API_KEY"

# Revoke key
curl -X DELETE http://localhost:8000/auth/keys/{key_id} \
  -H "Authorization: Bearer $DALSTON_API_KEY"
```

### Test WebSocket

```bash
# ✅ With API key
websocat "ws://localhost:8000/v1/audio/transcriptions/stream?api_key=$DALSTON_API_KEY"

# ❌ Without key → 4001 Invalid API key
```

---

## Checkpoint

- [x] **API keys** stored as SHA256 hashes in Redis
- [x] **Auth middleware** validates all REST endpoints
- [x] **WebSocket auth** via query parameter
- [x] **Scopes** control access to specific operations
- [x] **Rate limiting** per API key
- [x] **Tenant isolation** - jobs scoped by tenant_id
- [x] **Key management** endpoints at `/auth/*`
- [x] **CLI tool** for bootstrapping admin key

---

## Implementation Summary

**Completed**: All M11 deliverables implemented.

### Files Created

| File | Description |
| ---- | ----------- |
| `dalston/gateway/services/auth.py` | Core auth service: key generation, hashing, validation, rate limiting |
| `dalston/gateway/middleware/auth.py` | Request/WebSocket authentication middleware |
| `dalston/gateway/api/auth.py` | `/auth/*` API endpoints for key management |
| `dalston/gateway/cli.py` | CLI tool for `create-admin-key` command |
| `tests/unit/test_auth.py` | 800+ lines of unit tests for auth system |

### Key Features

- **API Keys** (`dk_` prefix): Long-lived credentials with SHA256 hashing
- **Session Tokens** (`tk_` prefix): Ephemeral tokens for browser WebSocket auth (10 min default, 1 hour max)
- **Scopes**: `jobs:read`, `jobs:write`, `realtime`, `webhooks`, `admin`
- **Rate Limiting**: Redis sliding window, configurable per key
- **ElevenLabs Compatibility**: Supports `xi-api-key` header alongside Bearer tokens
- **Auto-Bootstrap**: Creates admin key on first run and prints to console

### SDK Updates

- `Dalston.create_session_token()` / `AsyncDalston.create_session_token()` for browser auth flow
- `SessionToken` type added to SDK exports
- Realtime client handles auth errors with custom WebSocket close codes

### Auth Endpoints

| Endpoint | Scope | Description |
| -------- | ----- | ----------- |
| `POST /auth/keys` | admin | Create new API key |
| `GET /auth/keys` | admin | List tenant's API keys |
| `GET /auth/keys/{id}` | admin | Get key details |
| `DELETE /auth/keys/{id}` | admin | Revoke API key |
| `GET /auth/me` | any | Get current key info |
| `POST /auth/tokens` | realtime | Create ephemeral session token |

---

## Future: Phase 2 (Multi-tenancy & Users)

The `tenant_id` field on jobs and API keys enables future expansion:

- User accounts with login/signup
- Multiple tenants with separate billing
- Role-based access within tenants
- Usage quotas per tenant
- Audit logging

No data migration required - foundation is in place.
