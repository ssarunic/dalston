# M17: API Key Management Console

|  |  |
|---|---|
| **Goal** | Add web console page for administrators to create, view, and revoke API keys |
| **Duration** | 2-3 days |
| **Dependencies** | M10 (web console), M11 (API authentication), M15 (console auth) |
| **Deliverable** | Full CRUD UI for API keys with scope selection and security safeguards |
| **Status** | Completed (February 2026) |

## User Story

> *"As a Dalston administrator, I can manage API keys from the web console instead of the CLI, making it easier to onboard new team members and services."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       API KEY MANAGEMENT CONSOLE                             │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  API Keys Page                                      [Show Revoked ○]   │ │
│  │                                                          [+ Create Key] │ │
│  │  ┌──────────────────────────────────────────────────────────────────┐  │ │
│  │  │ Prefix      Name           Scopes              Created    Actions │  │ │
│  │  ├──────────────────────────────────────────────────────────────────┤  │ │
│  │  │ dk_abc123.. Production API jobs:read,write    2 days ago  [Revoke]│  │ │
│  │  │ dk_xyz789.. CI Pipeline    jobs:write         1 week ago  [Revoke]│  │ │
│  │  │ dk_def456.. Admin Console  admin              1 month ago [Revoke]│  │ │
│  │  └──────────────────────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  Create Key Flow:                                                            │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐   │
│  │ Click Create │───▶│ Select Name  │───▶│ Show Key Once (Copy Button)  │   │
│  │              │    │ + Scopes     │    │ "dk_aBc123...full-key-here"  │   │
│  └──────────────┘    └──────────────┘    └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

Security Model:
┌──────────────────────────────────────────────────────────────────────────────┐
│  • Raw key shown ONCE at creation (never stored/retrievable)                 │
│  • Keys displayed as masked prefix: "dk_abc1234..."                          │
│  • Server prevents revoking the key used in current request (400 error)      │
│  • Scopes are immutable (create new key for different scopes)                │
│  • Admin scope required for all key management operations                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### 1. Use Existing `/auth/keys` Endpoints

The web console uses the same `/auth/keys` endpoints as CLI and SDK clients. No separate `/api/console/keys` endpoints - the console is just another API client.

### 2. Single CLI Command

One `create-key` command with `--scopes` option instead of separate `create-admin-key`:

```bash
# Regular key (default scopes: jobs:read, jobs:write, realtime)
python -m dalston.gateway.cli create-key --name "My Key"

# Admin key
python -m dalston.gateway.cli create-key --name "Admin" --scopes admin

# Custom scopes
python -m dalston.gateway.cli create-key --name "Read Only" --scopes jobs:read
```

### 3. Server-Side Self-Revocation Check

- Revoke button always enabled in UI
- Server returns 400 error if user tries to revoke their current key
- No frontend detection needed - just show error toast on failure

### 4. No Key Rename Feature

If users want to "rename" a key:

1. Create new key with desired name
2. Update systems to use new key
3. Revoke old key

This provides better audit trail and ensures systems are updated.

### 5. Primitive Endpoints Over Aggregation

Dashboard uses primitive endpoints instead of aggregated `/api/console/dashboard`:

- `GET /health` - System status
- `GET /v1/realtime/status` - Realtime capacity
- `GET /v1/audio/transcriptions?limit=5` - Recent jobs
- `GET /v1/jobs/stats` - Batch statistics (new)

---

## Data Model

### APIKey (updated from M11)

| Field | Type | Description |
|-------|------|-------------|
| `id` | uuid | Unique identifier |
| `key_hash` | string | SHA256 hash (never store raw) |
| `prefix` | string | First 10 chars for display |
| `name` | string | Human-readable label |
| `tenant_id` | uuid | Tenant ownership |
| `scopes` | list[Scope] | Permission scopes |
| `rate_limit` | int \| null | Requests/minute (null = unlimited) |
| `created_at` | datetime | Creation timestamp |
| `last_used_at` | datetime \| null | Last API call |
| `expires_at` | datetime | Expiration (default: 2099-12-31T23:59:59Z) |
| `revoked_at` | datetime \| null | Revocation timestamp |

### API Response Models (existing in auth.py)

```python
class APIKeyResponse(BaseModel):
    """API key info for listing (no sensitive data)."""
    id: UUID
    prefix: str
    name: str
    scopes: list[str]
    rate_limit: int | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    is_current: bool  # True if this is the requesting key
```

---

## Steps

### 17.1: Backend - Add Job Stats Endpoint

**Deliverables:**

- `GET /v1/jobs/stats` - Returns job counts for dashboard
- Counts: running, queued, completed_today, failed_today

**Files:**

- `dalston/gateway/api/transcriptions.py` (modify)

**API:**

```
GET /v1/jobs/stats
Authorization: Bearer dk_...

Response 200:
{
  "running": 3,
  "queued": 12,
  "completed_today": 47,
  "failed_today": 2
}
```

---

### 17.2: Backend - Update AuthService

**Deliverables:**

- Add `expires_at` field to APIKey with default `2099-12-31T23:59:59Z`
- Add `include_revoked` parameter to `list_api_keys()`
- Update Redis storage/retrieval for new field

**Files:**

- `dalston/gateway/services/auth.py` (modify)

---

### 17.3: Backend - Update Auth Endpoints

**Deliverables:**

- Add `include_revoked` query param to `GET /auth/keys`
- Add `is_current` field to `APIKeyResponse`
- Existing self-revocation check already returns 400

**Files:**

- `dalston/gateway/api/auth.py` (modify)

**API:**

```
GET /auth/keys?include_revoked=true
Authorization: Bearer dk_...

Response 200:
{
  "keys": [
    {
      "id": "uuid",
      "prefix": "dk_abc123...",
      "name": "Production API",
      "scopes": ["jobs:read", "jobs:write"],
      "rate_limit": null,
      "created_at": "2024-01-15T10:00:00Z",
      "last_used_at": "2024-01-20T15:30:00Z",
      "expires_at": "2099-12-31T23:59:59Z",
      "is_current": false
    }
  ],
  "total": 1
}
```

---

### 17.4: Backend - Consolidate CLI

**Deliverables:**

- Modify `create-key` to accept `--scopes` option
- Remove `create-admin-key` command
- Default scopes: `jobs:read,jobs:write,realtime`

**Files:**

- `dalston/gateway/cli.py` (modify)

**CLI:**

```bash
python -m dalston.gateway.cli create-key --name "My Key" --scopes admin
```

---

### 17.5: Frontend - Refactor Dashboard

**Deliverables:**

- Remove dependency on `/api/console/dashboard`
- Each widget fetches its own data:
  - System status from `/health`
  - Batch stats from `/v1/jobs/stats`
  - Realtime capacity from `/v1/realtime/status`
  - Recent jobs from existing jobs endpoint

**Files:**

- `web/src/pages/Dashboard.tsx` (modify)
- `web/src/hooks/useDashboard.ts` (modify or remove)
- `web/src/api/client.ts` (modify)
- `web/src/api/types.ts` (modify)

---

### 17.6: Frontend - API Keys Page

**Deliverables:**

- New page at `/keys` listing all API keys
- Table with columns: Prefix, Name, Scopes, Created, Last Used, Actions
- Scope badges with color coding
- "Revoke" button on all keys (server prevents self-revoke)
- Toggle switch: "Show revoked" (hidden by default)
- Empty state when no keys

**Files:**

- `web/src/pages/ApiKeys.tsx` (new)
- `web/src/api/client.ts` (modify)
- `web/src/api/types.ts` (modify)

---

### 17.7: Frontend - Create Key Dialog

**Deliverables:**

- Modal dialog with:
  - Name input field
  - Scope checkboxes (jobs:read, jobs:write, realtime, webhooks, admin)
  - Optional rate limit input
  - Warning text for admin scope
- Submit creates key and shows result modal

**Files:**

- `web/src/components/CreateKeyDialog.tsx` (new)

**Scope Options:**

| Scope | Label | Description |
|-------|-------|-------------|
| `jobs:read` | Read Jobs | View job status and results |
| `jobs:write` | Create Jobs | Submit transcription jobs |
| `realtime` | Real-time | Connect to WebSocket streams |
| `webhooks` | Webhooks | Manage webhook configurations |
| `admin` | Admin Access | Full console access (grants all permissions) |

---

### 17.8: Frontend - Key Created Modal

**Deliverables:**

- Success modal shown after key creation
- Display full key with copy button
- Warning: "This key will only be shown once"
- Key hidden until user clicks "Show"

**Files:**

- `web/src/components/KeyCreatedModal.tsx` (new)

---

### 17.9: Frontend - Navigation Integration

**Deliverables:**

- Add "API Keys" item to sidebar navigation
- Use `Key` icon from lucide-react
- Position after "Engines" in nav order

**Files:**

- `web/src/components/Sidebar.tsx` (modify)
- `web/src/App.tsx` (modify)

---

### 17.10: Backend - Remove Aggregated Dashboard Endpoint

**Deliverables:**

- Remove or deprecate `GET /api/console/dashboard`
- Keep other console endpoints that provide admin-specific views

**Files:**

- `dalston/gateway/api/console.py` (modify)

---

### 17.11: Tests

**Deliverables:**

- Unit tests for updated AuthService methods
- Integration tests for `/v1/jobs/stats`
- Test `include_revoked` parameter
- Test `is_current` flag in response

**Files:**

- `tests/unit/test_auth.py` (modify)
- `tests/integration/test_api.py` (modify)

---

## Configuration

No new configuration required. Uses existing:

```bash
# Redis (for key storage)
REDIS_URL=redis://localhost:6379
```

---

## Verification

### List Keys

```bash
# List active keys
curl -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/auth/keys

# Include revoked keys
curl -H "Authorization: Bearer $ADMIN_KEY" \
  "http://localhost:8000/auth/keys?include_revoked=true"
```

### Create Key via CLI

```bash
# Regular key
python -m dalston.gateway.cli create-key --name "Test Key"

# Admin key
python -m dalston.gateway.cli create-key --name "Admin" --scopes admin
```

### Create Key via API

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "New Key", "scopes": ["jobs:read"]}' \
  http://localhost:8000/auth/keys
```

### Revoke Key

```bash
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/auth/keys/$KEY_ID

# Expected: 204 No Content
```

### Self-Revocation Prevention

```bash
# Try to revoke the key you're using
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/auth/keys/$ADMIN_KEY_ID

# Expected: 400 Bad Request
# {"detail": "Cannot revoke your own API key"}
```

### Job Stats

```bash
curl -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/v1/jobs/stats

# Expected: {"running": 0, "queued": 0, "completed_today": 5, "failed_today": 0}
```

---

## Checkpoint

- [x] `GET /v1/jobs/stats` returns batch statistics
- [x] `GET /auth/keys` supports `include_revoked` parameter
- [x] `GET /auth/keys` returns `is_current` flag on each key
- [x] `APIKey` model includes `expires_at` field
- [x] CLI `create-key` accepts `--scopes` option
- [x] `create-admin-key` command removed
- [x] Dashboard uses primitive endpoints
- [x] API Keys page shows table of keys
- [x] "Show revoked" toggle works
- [x] Create dialog with scope checkboxes works
- [x] Key shown once in modal with copy button
- [x] Revoke shows error toast for current key
- [x] Navigation includes API Keys link
- [x] Tests pass

---

## Implementation Summary

### Backend Changes

| File | Changes |
| ---- | ------- |
| `dalston/gateway/services/auth.py` | Added `DEFAULT_EXPIRES_AT` (2099-12-31), `expires_at` field to `APIKey`, `is_expired` property, `include_revoked` param to `list_api_keys()` |
| `dalston/gateway/services/jobs.py` | Added `JobStats` dataclass and `get_stats()` method |
| `dalston/gateway/api/v1/jobs.py` | New file with `GET /v1/jobs/stats` endpoint |
| `dalston/gateway/api/auth.py` | Added `include_revoked` query param, `is_current` and `expires_at` to responses |
| `dalston/gateway/cli.py` | Consolidated to single `create-key` command with `--scopes` option |

### Frontend Changes

| File | Changes |
| ---- | ------- |
| `web/src/pages/ApiKeys.tsx` | New API Keys management page with table, revoke dialog |
| `web/src/components/CreateKeyDialog.tsx` | Modal for creating keys with scope selection |
| `web/src/components/KeyCreatedModal.tsx` | Shows key once with copy button and clipboard fallback |
| `web/src/components/ui/dialog.tsx` | Accessible dialog component (focus trap, escape key, ARIA) |
| `web/src/hooks/useDashboard.ts` | Refactored to use `useQueries` with primitive endpoints |
| `web/src/hooks/useApiKeys.ts` | Hooks for API key CRUD operations |
| `web/src/api/client.ts` | Added `getJobStats`, `getApiKeys`, `createApiKey`, `revokeApiKey` |
| `web/src/api/types.ts` | Added `APIKey`, `APIKeyCreatedResponse`, `JobStatsResponse` types |

### Test Coverage

| File | Tests Added |
| ---- | ----------- |
| `tests/unit/test_auth.py` | `is_expired`, `expires_at` serialization, backward compatibility, `include_revoked` filter |
| `tests/unit/test_jobs_service.py` | New file testing `JobStats` and `get_stats()` |
| `tests/integration/test_batch_api.py` | New file testing `/v1/jobs/stats` endpoint |
| `tests/integration/test_auth_api.py` | New file testing `/auth/keys` CRUD, `is_current`, self-revocation prevention |

Total: 289 tests passing

### Code Quality Improvements (from review)

- Fixed import ordering in `services/jobs.py` (stdlib before third-party)
- Added clipboard fallback for older browsers in `KeyCreatedModal`
- Created accessible `Dialog` component with focus management and keyboard support
- Removed unused `APIKey` import from `api/client.ts`

---

## Security Considerations

1. **Key Exposure**: Raw keys only returned at creation, never retrievable after
2. **Self-Revocation**: Server prevents revoking current key (400 error)
3. **Scope Immutability**: Cannot modify scopes after creation (create new key instead)
4. **Admin Required**: All key management endpoints require admin scope
5. **Expiration**: Keys have `expires_at` field for future expiration support

---

## Future Enhancements

- **Key Expiration**: Allow setting custom expiration dates
- **Usage Analytics**: Show request counts per key
- **Audit Log**: Track who created/revoked keys and when
- **Key Rotation**: Generate new key with same scopes, revoke old
- **IP Allowlist**: Restrict key usage to specific IPs
