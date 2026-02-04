# M17: API Key Management Console

|  |  |
|---|---|
| **Goal** | Add web console page for administrators to create, view, and revoke API keys |
| **Duration** | 2-3 days |
| **Dependencies** | M10 (web console), M11 (API authentication), M15 (console auth) |
| **Deliverable** | Full CRUD UI for API keys with scope selection and security safeguards |

## User Story

> *"As a Dalston administrator, I can manage API keys from the web console instead of the CLI, making it easier to onboard new team members and services."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       API KEY MANAGEMENT CONSOLE                             │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  API Keys Page                                                         │ │
│  │                                                          [+ Create Key] │ │
│  │  ┌──────────────────────────────────────────────────────────────────┐  │ │
│  │  │ Prefix      Name           Scopes              Created    Actions │  │ │
│  │  ├──────────────────────────────────────────────────────────────────┤  │ │
│  │  │ dk_abc123.. Production API jobs:read,write    2 days ago  [Revoke]│  │ │
│  │  │ dk_xyz789.. CI Pipeline    jobs:write         1 week ago  [Revoke]│  │ │
│  │  │ dk_def456.. Admin Console  admin              1 month ago    -    │  │ │
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
│  • Cannot revoke the key currently authenticating the request                │
│  • Admin scope required for all key management operations                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Existing: APIKey (from M11)

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
| `revoked_at` | datetime \| null | Revocation timestamp |

### New: API Response Models

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

class APIKeyCreateRequest(BaseModel):
    """Request to create a new API key."""
    name: str
    scopes: list[str]
    rate_limit: int | None = None

class APIKeyCreateResponse(BaseModel):
    """Response after creating key (includes raw key ONCE)."""
    id: UUID
    key: str  # Full raw key - only returned here!
    prefix: str
    name: str
    scopes: list[str]

class APIKeyUpdateRequest(BaseModel):
    """Request to update key metadata."""
    name: str
```

---

## Steps

### 17.1: Backend - List Keys Endpoint

**Deliverables:**

- `GET /api/console/keys` - List all API keys for tenant
- Returns masked keys (prefix only)
- Excludes revoked keys by default
- Optional `?include_revoked=true` parameter

**Files:**

- `dalston/gateway/api/console.py` (modify)

**API:**

```
GET /api/console/keys
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
      "last_used_at": "2024-01-20T15:30:00Z"
    }
  ]
}
```

---

### 17.2: Backend - Create Key Endpoint

**Deliverables:**

- `POST /api/console/keys` - Create new API key
- Validate scopes against allowed values
- Return raw key ONCE in response
- Log key creation for audit

**Files:**

- `dalston/gateway/api/console.py` (modify)

**API:**

```
POST /api/console/keys
Authorization: Bearer dk_...
Content-Type: application/json

{
  "name": "CI Pipeline Key",
  "scopes": ["jobs:read", "jobs:write"],
  "rate_limit": 100
}

Response 201:
{
  "id": "uuid",
  "key": "dk_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456789abcdef",
  "prefix": "dk_aBcDeFg...",
  "name": "CI Pipeline Key",
  "scopes": ["jobs:read", "jobs:write"]
}
```

---

### 17.3: Backend - Revoke Key Endpoint

**Deliverables:**

- `DELETE /api/console/keys/{id}` - Revoke (soft-delete) a key
- Prevent revoking the key used in current request
- Return 404 if key not found or already revoked

**Files:**

- `dalston/gateway/api/console.py` (modify)

**API:**

```
DELETE /api/console/keys/{id}
Authorization: Bearer dk_...

Response 204: (no content)

Response 400:
{"detail": "Cannot revoke the key you are currently using"}

Response 404:
{"detail": "API key not found"}
```

---

### 17.4: Backend - Update Key Endpoint

**Deliverables:**

- `PATCH /api/console/keys/{id}` - Update key name
- Only name is updatable (scopes are immutable for security)
- Add `update_api_key_name()` to AuthService

**Files:**

- `dalston/gateway/api/console.py` (modify)
- `dalston/gateway/services/auth.py` (modify)

**API:**

```
PATCH /api/console/keys/{id}
Authorization: Bearer dk_...
Content-Type: application/json

{
  "name": "Renamed Key"
}

Response 200:
{
  "id": "uuid",
  "prefix": "dk_abc123...",
  "name": "Renamed Key",
  "scopes": ["jobs:read", "jobs:write"],
  ...
}
```

---

### 17.5: Frontend - API Keys Page

**Deliverables:**

- New page at `/keys` listing all API keys
- Table with columns: Prefix, Name, Scopes, Created, Last Used, Actions
- Scope badges with color coding
- "Revoke" button (disabled for current key)
- Empty state when no keys

**Files:**

- `web/src/pages/ApiKeys.tsx` (new)
- `web/src/api/client.ts` (modify)
- `web/src/api/types.ts` (modify)

---

### 17.6: Frontend - Create Key Dialog

**Deliverables:**

- Modal dialog with:
  - Name input field
  - Scope checkboxes (jobs:read, jobs:write, realtime, webhooks, admin)
  - Optional rate limit input
  - Warning text for admin scope
- Submit creates key and shows result modal

**Files:**

- `web/src/pages/ApiKeys.tsx` (modify)
- `web/src/components/CreateKeyDialog.tsx` (new)

**Scope Options:**

| Scope | Label | Description |
|-------|-------|-------------|
| `jobs:read` | Read Jobs | View job status and results |
| `jobs:write` | Create Jobs | Submit transcription jobs |
| `realtime` | Real-time | Connect to WebSocket streams |
| `webhooks` | Webhooks | Manage webhook configurations |
| `admin` | Admin Access | Full console access (⚠️ grants all permissions) |

---

### 17.7: Frontend - Key Created Modal

**Deliverables:**

- Success modal shown after key creation
- Display full key with copy button
- Warning: "This key will only be shown once"
- Key hidden until user clicks "Show"
- Close button disabled until key copied (optional UX)

**Files:**

- `web/src/components/KeyCreatedModal.tsx` (new)

---

### 17.8: Frontend - Revoke Confirmation

**Deliverables:**

- Confirmation dialog before revoke
- Show key name and prefix
- Warning about immediate effect
- Disable revoke for currently-used key with tooltip

**Files:**

- `web/src/pages/ApiKeys.tsx` (modify)

---

### 17.9: Frontend - Navigation Integration

**Deliverables:**

- Add "API Keys" item to sidebar navigation
- Use `Key` icon from lucide-react
- Position after "Engines" in nav order

**Files:**

- `web/src/components/Sidebar.tsx` (modify)

---

### 17.10: Tests

**Deliverables:**

- Unit tests for new AuthService methods
- Integration tests for API endpoints
- Test self-revocation prevention
- Test scope validation

**Files:**

- `tests/unit/test_auth_service.py` (modify)
- `tests/integration/test_console_api.py` (modify)

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
# Create test keys via CLI first
python -m dalston.gateway.cli create-key --name "Test Key 1"
python -m dalston.gateway.cli create-admin-key --name "Admin Key"

# List via API
curl -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/api/console/keys

# Expected: Array of key objects with masked prefixes
```

### Create Key via Console

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "New Key", "scopes": ["jobs:read"]}' \
  http://localhost:8000/api/console/keys

# Expected: Response includes full raw key (only time it's shown)
```

### Revoke Key

```bash
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/api/console/keys/$KEY_ID

# Expected: 204 No Content

# Verify revoked key no longer works
curl -H "Authorization: Bearer $REVOKED_KEY" \
  http://localhost:8000/v1/audio/transcriptions
# Expected: 401 Unauthorized
```

### Self-Revocation Prevention

```bash
# Try to revoke the key you're using
curl -X DELETE \
  -H "Authorization: Bearer $ADMIN_KEY" \
  http://localhost:8000/api/console/keys/$ADMIN_KEY_ID

# Expected: 400 Bad Request
# {"detail": "Cannot revoke the key you are currently using"}
```

---

## Checkpoint

- [ ] `GET /api/console/keys` returns list of masked keys
- [ ] `POST /api/console/keys` creates key and returns raw key once
- [ ] `DELETE /api/console/keys/{id}` revokes key
- [ ] `PATCH /api/console/keys/{id}` renames key
- [ ] Cannot revoke currently-used key
- [ ] API Keys page shows table of keys
- [ ] Create dialog with scope checkboxes works
- [ ] Key shown once in modal with copy button
- [ ] Revoke confirmation dialog works
- [ ] Navigation includes API Keys link
- [ ] Tests pass

---

## Security Considerations

1. **Key Exposure**: Raw keys only returned at creation, never retrievable after
2. **Self-Revocation**: Prevents accidental lockout by blocking revoke of current key
3. **Scope Immutability**: Cannot modify scopes after creation (create new key instead)
4. **Admin Required**: All key management endpoints require admin scope
5. **Audit Trail**: Consider logging key create/revoke events (future enhancement)

---

## Future Enhancements

- **Key Expiration**: Optional expiry date for temporary keys
- **Usage Analytics**: Show request counts per key
- **Audit Log**: Track who created/revoked keys and when
- **Key Rotation**: Generate new key with same scopes, revoke old
- **IP Allowlist**: Restrict key usage to specific IPs
