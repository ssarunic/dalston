# M35: Settings Page

|  |  |
|---|---|
| **Goal** | Admin console page for viewing and editing system configuration without redeploying |
| **Duration** | 4-5 days |
| **Dependencies** | M10 (Web Console), M11 (API Authentication), M15 (Console Auth) |
| **Deliverable** | Settings page with namespaced sections, database-backed overrides, audit-logged changes |
| **Status** | Complete |

## User Story

> *"As a Dalston administrator, I can view and adjust system settings — rate limits, retention policies, engine behavior, and audio constraints — from the web console without editing environment variables or restarting services."*

---

## Overview

The Settings page provides a tabbed interface with namespaces (Rate Limits, Engines, Audio, Retention, Webhooks, System). Editable namespaces show form fields with current values, defaults, and override indicators. A sticky "Unsaved changes" footer with Save/Cancel appears on dirty forms. The System tab is read-only, displaying infrastructure config (Redis URL, Database, S3, version) sourced from environment variables.

---

## Design Decisions

### 1. Database-Backed Overrides, Not Config File Replacement

Settings are stored in a `settings` table as namespace/key/value rows. On startup, the application reads environment variables (via `config.py` `Settings` class) as defaults. Database rows override those defaults at runtime.

**Rationale:** Environment variables remain the source of truth for infrastructure (DB URLs, S3 buckets, secrets). The Settings page only manages operational parameters that admins tune in production — rate limits, retention, engine behavior. This avoids the complexity of bidirectional config sync and keeps secrets out of the database.

### 2. Namespaced Sections

Settings are grouped into namespaces that map to logical concerns:

| Namespace | Editable | Settings |
|-----------|----------|----------|
| `rate_limits` | Yes | `requests_per_minute`, `concurrent_jobs`, `concurrent_sessions` |
| `engines` | Yes | `unavailable_behavior`, `wait_timeout_seconds` |
| `audio` | Yes | `url_max_size_gb`, `url_timeout_seconds` |
| `retention` | Yes | `cleanup_interval_seconds`, `cleanup_batch_size`, `min_hours` |
| `system` | No | `redis_url`, `database_url`, `s3_bucket`, `s3_region`, `version` |

The `system` namespace is read-only — it displays infrastructure config for diagnostic purposes but cannot be changed from the console.

### 3. Admin Scope Only — No New Scope

All settings endpoints require `RequireAdmin`. Settings affect the entire system and sit alongside API key management, webhooks, and other admin-only operations. Adding a `settings:write` scope would force existing admin keys to be recreated. See the auth analysis in the discussion above.

### 4. Optimistic Locking via `updated_at`

When saving changes, the frontend sends the `updated_at` timestamp it last read. If another admin changed the same setting since, the server returns `409 Conflict`. This prevents silent overwrites without requiring pessimistic locks.

### 5. Immediate Effect, No Restart Required

Changed settings take effect on the next request. The `SettingsService` reads from the database with a short TTL cache (5 seconds) so there is no need to restart the gateway or orchestrator.

---

## Data Model

### `settings` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID FK (nullable) | Tenant scope. NULL = system-wide default |
| `namespace` | VARCHAR(50) | Setting group (e.g., `rate_limits`) |
| `key` | VARCHAR(100) | Setting name within namespace |
| `value` | JSONB | Setting value (number, string, boolean) |
| `updated_by` | UUID FK (nullable) | API key ID that last changed this setting |
| `created_at` | TIMESTAMP | Row creation time |
| `updated_at` | TIMESTAMP | Last modification time |

**Constraints:**

- `UNIQUE (tenant_id, namespace, key)` — one value per setting per tenant
- Index on `(namespace)` for section queries
- Index on `(tenant_id, namespace)` for tenant-scoped lookups

### Setting Resolution Order

```text
1. Database row WHERE tenant_id = <current_tenant> AND namespace/key match
2. Database row WHERE tenant_id IS NULL AND namespace/key match  (system default override)
3. Environment variable (config.py Settings class)
4. Hardcoded default in field definition
```

This allows per-tenant overrides in the future without schema changes.

---

## Steps

### 35.1: Database Model & Migration

**Deliverables:**

- `SettingModel` in `dalston/db/models.py`
- Alembic migration creating `settings` table
- Unique constraint on `(tenant_id, namespace, key)`
- Indexes on `namespace` and `(tenant_id, namespace)`

**Files:**

- `dalston/db/models.py` (modify)
- `alembic/versions/xxxx_add_settings_table.py` (new)

---

### 35.2: Settings Service

**Deliverables:**

- `SettingsService` class in `dalston/gateway/services/settings.py`
- `get_namespace(namespace, tenant_id)` — returns all settings in a namespace with resolved values (DB override or env default)
- `update_namespace(namespace, tenant_id, updates, expected_updated_at)` — partial update with optimistic locking
- `reset_namespace(namespace, tenant_id)` — delete DB overrides, revert to env defaults
- `get_system_info()` — read-only infrastructure info (Redis URL, DB, S3, version)
- Short-lived TTL cache (5 seconds) to avoid per-request DB queries

A `SettingDefinition` dataclass in `dalston/gateway/services/settings.py` defines each setting's namespace, key, label, description, value type (`int`/`float`/`bool`/`string`/`select`), default, env var mapping, and optional min/max/options constraints. A `SETTING_DEFINITIONS` list registers all known settings.

**Files:**

- `dalston/gateway/services/settings.py` (new)

---

### 35.3: Settings API Endpoints

**Deliverables:**

- `GET /api/console/settings` — list all namespaces with metadata
- `GET /api/console/settings/{namespace}` — get all settings in a namespace with current values, defaults, and definitions
- `PATCH /api/console/settings/{namespace}` — update settings in a namespace
- `POST /api/console/settings/{namespace}/reset` — reset namespace to defaults

All endpoints require `RequireAdmin`.

**Files:**

- `dalston/gateway/api/console.py` (modify)

**Example (PATCH — update with optimistic locking):**

```
PATCH /api/console/settings/rate_limits
Authorization: Bearer dk_...
Content-Type: application/json

{
  "settings": {
    "requests_per_minute": 1200,
    "concurrent_jobs": 20
  },
  "expected_updated_at": "2026-02-20T10:30:00Z"
}

Response 200:
{
  "namespace": "rate_limits",
  "settings": [ ... ],
  "updated_at": "2026-02-24T14:00:00Z"
}

Response 409 (conflict):
{
  "detail": "Settings were modified by another admin. Please refresh and try again."
}
```

The other endpoints (`GET /api/console/settings`, `GET .../settings/{namespace}`, `POST .../settings/{namespace}/reset`) follow the same auth and response patterns. See `dalston/gateway/api/console.py`.

---

### 35.4: Wire Settings into Runtime

**Deliverables:**

- Modify `get_settings()` or create `get_effective_setting()` helper that checks DB overrides before falling back to env defaults
- `RedisRateLimiter` reads limits from `SettingsService` instead of hardcoded `Settings` fields
- Engine availability behavior reads from `SettingsService`
- Audio URL limits read from `SettingsService`

**Design:**

The `SettingsService` exposes a `get_value(namespace, key)` method with a 5-second TTL cache. Existing code that reads `settings.rate_limit_requests_per_minute` is updated to call `settings_service.get_value("rate_limits", "requests_per_minute")` instead, falling back to the env var default if no DB override exists.

**Files:**

- `dalston/gateway/services/settings.py` (modify)
- `dalston/gateway/services/rate_limiter.py` (modify)
- `dalston/gateway/dependencies.py` (modify — add `get_settings_service` dependency)

---

### 35.5: Audit Logging for Setting Changes

**Deliverables:**

- Every `PATCH` and `reset` writes an audit log entry via the existing `AuditService`
- Audit action: `settings.updated` or `settings.reset`
- Audit payload includes: namespace, changed keys, old values, new values, API key ID

**Files:**

- `dalston/gateway/api/console.py` (modify — add audit calls to settings endpoints)

Audit entries use action `settings.updated` or `settings.reset`, with `resource_type: "settings"` and `resource_id` set to the namespace. The `details` field records old and new values for each changed key.

---

### 35.6: Frontend — Settings Page Shell

**Deliverables:**

- New page at `/settings`
- Tab navigation for namespaces (Rate Limits, Engines, Audio, Retention, Webhooks, System)
- URL-synced active tab (`/settings?tab=rate_limits`)
- Loading skeleton while fetching
- Add "Settings" to sidebar navigation with `Settings` (gear) icon

**Files:**

- `web/src/pages/Settings.tsx` (new)
- `web/src/components/Sidebar.tsx` (modify)
- `web/src/App.tsx` (modify — add route)
- `web/src/api/client.ts` (modify — add settings API calls)
- `web/src/api/types.ts` (modify — add settings types)

---

### 35.7: Frontend — Editable Setting Fields

**Deliverables:**

- `SettingField` component that renders the correct input based on `value_type`:
  - `int` / `float`: Number input with min/max validation
  - `bool`: Toggle switch
  - `string`: Text input
  - `select`: Dropdown
- Override indicator (dot or badge) when value differs from default
- "Default: X" hint text below each field
- `SettingsNamespaceForm` component wrapping fields with dirty tracking

**Files:**

- `web/src/components/SettingField.tsx` (new)
- `web/src/components/SettingsNamespaceForm.tsx` (new)
- `web/src/components/SettingsResetDialog.tsx` (new)

---

### 35.8: Frontend — Save, Reset, and Conflict Handling

**Deliverables:**

- Sticky footer bar appears when form is dirty: "Unsaved changes" + [Cancel] + [Save]
- Save button sends `PATCH` with `expected_updated_at`
- On `409 Conflict`: show toast "Settings were modified by another admin. Please refresh."
- Cancel button reverts form to last-saved values
- "Reset to defaults" button per namespace with confirmation dialog
- Success toast on save: "Rate limits updated"
- `useSettings` hook for data fetching and mutation

**Files:**

- `web/src/hooks/useSettings.ts` (new)
- `web/src/pages/Settings.tsx` (modify)

---

### 35.9: Frontend — System Info Tab

**Deliverables:**

- Read-only display of infrastructure settings
- Info banner: "System settings are read-only and controlled by environment variables"
- Display: Redis URL, Database URL (masked password), S3 bucket, S3 region, application version
- Copy-to-clipboard on each value

**Files:**

- `web/src/pages/Settings.tsx` (modify)

---

### 35.10: Tests

**Deliverables:**

- Unit tests for `SettingsService`: get, update, reset, optimistic locking, cache TTL
- Integration tests for all settings API endpoints
- Test permission enforcement (non-admin key gets 403)
- Test conflict detection (409 on stale `expected_updated_at`)
- Test setting resolution order (DB override > env default)
- Test audit log creation on settings changes

**Files:**

- `tests/unit/test_settings_service.py` (new)
- `tests/integration/test_settings_api.py` (new)

---

## Configuration

No new environment variables required. The Settings page reads existing env vars as defaults and stores overrides in the database.

The only new table is `settings` in PostgreSQL, created by migration.

---

## Verification

- `GET /api/console/settings` returns all namespaces with setting counts and override indicators
- `PATCH /api/console/settings/rate_limits` with valid `expected_updated_at` returns 200 with updated values
- `PATCH` with stale `expected_updated_at` returns 409 Conflict
- `POST /api/console/settings/rate_limits/reset` reverts to defaults (`updated_at` becomes null)
- Non-admin API key receives 403 on all settings endpoints
- Audit log entries are created for each update and reset (queryable via `GET /api/console/audit?action=settings.updated`)

---

## Checkpoint

- [ ] `settings` table created with migration
- [ ] `SettingsService` resolves DB overrides over env defaults
- [ ] `GET /api/console/settings` lists namespaces
- [ ] `GET /api/console/settings/{namespace}` returns settings with values and definitions
- [ ] `PATCH /api/console/settings/{namespace}` updates settings
- [ ] `POST /api/console/settings/{namespace}/reset` reverts to defaults
- [ ] 409 returned on stale `expected_updated_at`
- [ ] All settings endpoints require admin scope
- [ ] Audit log entries created for changes and resets
- [ ] Runtime behavior (rate limits, engine timeouts) uses DB overrides
- [ ] Settings page renders with tabbed namespaces
- [ ] Input validation enforces min/max/type constraints
- [ ] Override indicator shown when value differs from default
- [ ] Sticky "unsaved changes" footer with Save/Cancel
- [ ] Reset confirmation dialog works
- [ ] System tab shows read-only infrastructure info
- [ ] Sidebar includes Settings link
- [ ] Tests pass

---

## Security Considerations

1. **Admin Only**: All settings endpoints gated by `RequireAdmin` — no new scope needed
2. **System Tab Masks Secrets**: Database password masked in display (`postgresql+asyncpg://dalston:****@db:5432/dalston`)
3. **No Secret Storage**: Credentials (API keys, S3 secrets, webhook secrets) are never editable via the Settings page — environment variables only
4. **Audit Trail**: Every change logged with actor, old value, new value, and timestamp
5. **Optimistic Locking**: Prevents silent overwrites between concurrent admins
6. **Input Validation**: Server-side min/max/type checks on all setting values; client-side validation mirrors server constraints
7. **No Arbitrary Keys**: Only predefined settings in the registry can be created — the API rejects unknown namespace/key combinations

---

## Future Enhancements

- **Per-tenant overrides**: Allow different rate limits per tenant (data model already supports `tenant_id`)
- **Setting history**: Show change history per setting (queryable from audit log)
- **Import/Export**: Download settings as JSON, upload to another instance
- **Validation webhooks**: Notify external systems before applying critical changes
- **Grouped rollout**: Apply setting changes to a percentage of traffic before full rollout
- **Real-time preview**: Show estimated impact of rate limit changes before saving
