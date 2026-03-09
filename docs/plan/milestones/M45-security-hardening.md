# M45: Security Hardening

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Deny-by-default security posture with centralized SecurityManager, typed exceptions, and resource ownership |
| **Duration**  | 5 weeks (phased rollout)                                                                  |
| **Dependencies** | M11 (API Authentication), M17 (API Key Management)                                     |
| **Deliverable** | SecurityManager, Principal abstraction, ownership tracking, deny-by-default tests       |
| **Status**    | Not Started                                                                               |

## User Story

> *"As a platform operator, I want all API endpoints protected by default with clear permission requirements, so that I can be confident no sensitive operations are exposed without authentication."*

> *"As a multi-tenant user with multiple API keys, I want my jobs to only be visible to the API key that created them, so that I can safely issue limited-scope keys to third parties without exposing all my data."*

---

## Problem

The current security implementation has several critical gaps:

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           CURRENT SECURITY GAPS                                  │
│                                                                                  │
│  P0: UNAUTHENTICATED ENDPOINTS                                                   │
│  ├── POST /v1/models/{model_id}/pull    (downloads models from HuggingFace)      │
│  ├── DELETE /v1/models/{model_id}       (removes model files)                    │
│  ├── POST /v1/models/sync               (synchronizes registry)                  │
│  └── POST /v1/models/hf/resolve         (resolves HF model names)                │
│                                                                                  │
│  P1: NO CENTRALIZED POLICY ENGINE                                                │
│  ├── Auth logic scattered across dependencies.py, middleware/auth.py            │
│  ├── No unified has_permission() check                                           │
│  └── Each endpoint implements its own auth logic                                 │
│                                                                                  │
│  P1: NO OWNERSHIP TRACKING                                                       │
│  ├── Jobs table has tenant_id but no created_by_key_id                          │
│  ├── RealtimeSession has tenant_id but no created_by_key_id                     │
│  └── Cannot implement "key sees only its own resources"                          │
│                                                                                  │
│  P1: NO SECURITY MODE CONFIG                                                     │
│  └── No way to toggle between development (permissive) and production modes     │
│                                                                                  │
│  P2: CONSOLE HARDCODED TENANT                                                    │
│  └── Admin console uses DEFAULT_TENANT_ID, ignoring authenticated tenant         │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Impact:**

- Model management endpoints allow unauthenticated state changes and expensive operations
- No clear security boundary - protected/unprotected endpoints mixed without policy
- Cannot implement per-API-key isolation required by enterprise customers
- Authorization scattered across codebase makes security audit difficult

---

## Solution Overview

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           TARGET SECURITY ARCHITECTURE                           │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         SECURITY MANAGER                                  │   │
│  │                                                                           │   │
│  │   Principal          Permission           Policy                         │   │
│  │   ┌─────────┐       ┌────────────┐       ┌────────────┐                  │   │
│  │   │ API Key │       │ JOB_CREATE │       │ ADMIN: all │                  │   │
│  │   │ Session │  ───► │ JOB_READ   │  ───► │ SCOPE: map │                  │   │
│  │   │ System  │       │ MODEL_PULL │       │ OWNER:self │                  │   │
│  │   └─────────┘       └────────────┘       └────────────┘                  │   │
│  │                                                                           │   │
│  │   has_permission(principal, permission) -> bool                          │   │
│  │   require_permission(principal, permission) -> raises AuthorizationError │   │
│  │   can_access_resource(principal, resource) -> bool                       │   │
│  │                                                                           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                             │
│                        Exception Mapping Layer                                   │
│                                    ▼                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                         HTTP RESPONSES                                    │   │
│  │                                                                           │   │
│  │   AuthenticationError   ────►  401 Unauthorized                          │   │
│  │   AuthorizationError    ────►  403 Forbidden                             │   │
│  │   ResourceNotFoundError ────►  404 Not Found (anti-enumeration)          │   │
│  │   RateLimitExceededError────►  429 Too Many Requests                     │   │
│  │                                                                           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Design Principles:**

1. **Deny-by-default** + explicit public allowlist
2. **One policy engine**, multiple identity providers
3. **Permission checks close to domain actions** (service layer), not only at HTTP boundary
4. **Typed domain security exceptions**, then map once to HTTP in middleware
5. **Add ownership at data model level early**; retrofitting later is expensive

---

## Phase 0: Emergency Fixes (Day 1-2)

Lock down unauthenticated mutation endpoints immediately.

### 0.1: Add Auth to Model Endpoints

**File:** `dalston/gateway/api/v1/models.py`

Add `api_key: RequireAdmin` parameter to the following endpoint handlers:

- `pull_model()` — POST `/v1/models/{model_id}/pull`
- `delete_model()` — DELETE `/v1/models/{model_id}`
- `sync_models()` — POST `/v1/models/sync`
- `resolve_hf_model()` — POST `/v1/models/hf/resolve`

Add `api_key: RequireJobsRead` to:

- `get_hf_mappings()` — GET `/v1/models/hf/mappings`

### 0.2: Add Auth to PII Entity Types

**File:** `dalston/gateway/api/v1/pii.py`

Add `api_key: RequireJobsRead` parameter to `list_entity_types()`.

### 0.3: Regression Tests

**New file:** `tests/integration/test_auth_required.py`

Test scenarios:

- All model mutation endpoints return 401 without auth header
- Model mutation endpoints return 403 with non-admin API key
- PII entity-types endpoint returns 401 without auth header

---

## Phase 1: Security Core (Week 1)

Introduce centralized security abstractions and exception handling.

### 1.1: Create Security Module

**New directory:** `dalston/gateway/security/`

```
dalston/gateway/security/
├── __init__.py
├── principal.py      # Principal abstraction
├── permissions.py    # Permission enum
├── manager.py        # SecurityManager
├── exceptions.py     # Typed security exceptions
└── public_endpoints.py  # Public allowlist
```

### 1.2: Principal Abstraction

**New file:** `dalston/gateway/security/principal.py`

`PrincipalType` enum: `API_KEY`, `SESSION_TOKEN`, `SYSTEM`.

`Principal` dataclass with fields: `type`, `id`, `tenant_id`, `scopes`, `key_prefix`, `parent_key_id`. Factory methods: `from_api_key(APIKey)`, `from_session_token(SessionToken)`, `system(tenant_id)`. Helper: `has_scope(Scope) -> bool` (admin scope implies all). Properties: `actor_id`, `actor_type` for audit logging.

### 1.3: Permission Enum

**New file:** `dalston/gateway/security/permissions.py`

Define scopes covering: job CRUD (with own-resource variants), realtime session create/read, webhook CRUD, model read/pull/delete/sync, and admin operations (API key management, settings, audit). Use hierarchical inheritance where `admin` scope grants all permissions.

### 1.4: Typed Security Exceptions

**New file:** `dalston/gateway/security/exceptions.py`

Base class `SecurityError(Exception)` with `code` field. Subclasses:

- `AuthenticationError` — 401, missing/invalid credentials
- `AuthorizationError` — 403, has `required_permission` field
- `ResourceNotFoundError` — 404 (anti-enumeration: hides 403), has `resource_type` and `resource_id` fields
- `RateLimitExceededError` — 429, has `retry_after` field

### 1.5: SecurityManager

**New file:** `dalston/gateway/security/manager.py`

`SecurityManager` — centralized authorization policy engine. Constructed with `mode: Literal["none", "api_key", "user"]`. Key methods:

- `has_permission(principal, permission) -> bool` — checks scope-to-permission mapping; `mode="none"` always returns True
- `require_permission(principal, permission) -> None` — raises `AuthorizationError` if denied
- `can_access_resource(principal, resource_tenant_id, resource_created_by) -> bool` — tenant check + ownership check for non-admin
- `require_resource_access(...)` — raises `ResourceNotFoundError` if denied

Module-level `get_security_manager()` singleton factory reads `security_mode` from settings.

**Permission model:** `SCOPE_PERMISSIONS` maps each `Scope` to a set of `Permission` values. `jobs:read` grants job/session/model read permissions. `jobs:write` grants job create/delete-own/cancel-own. `realtime` grants session create. `webhooks` grants full webhook CRUD. `admin` grants all permissions.

### 1.6: Exception Handler Middleware

**New file:** `dalston/gateway/middleware/security_error_handler.py`

`SecurityErrorHandlerMiddleware(BaseHTTPMiddleware)` — catches domain security exceptions in `dispatch()` and maps them to HTTP responses: `AuthenticationError` → 401 (with `WWW-Authenticate: Bearer`), `AuthorizationError` → 403, `ResourceNotFoundError` → 404, `RateLimitExceededError` → 429 (with `Retry-After` header), `SecurityError` (fallback) → 403.

### 1.7: Register Middleware

**File:** `dalston/gateway/main.py`

Add `SecurityErrorHandlerMiddleware` in `create_app()` alongside existing middleware registrations.

### 1.8: Add Dependencies

**File:** `dalston/gateway/dependencies.py`

Add two new dependency functions alongside existing auth dependencies:

- `get_security_manager() -> SecurityManager` — returns singleton
- `get_principal(api_key: APIKey) -> Principal` — converts authenticated APIKey/SessionToken to Principal

---

## Phase 2: Security Mode Configuration (Week 2)

### 2.1: Add Config Setting

**File:** `dalston/config.py`

Add `security_mode` field to the settings class:

- Type: `Literal["none", "api_key", "user"]`, default `"api_key"`
- Env var: `DALSTON_SECURITY_MODE`
- `"none"` disables all auth checks (development only), `"api_key"` enforces API key validation, `"user"` reserved for future user auth

### 2.2: Public Endpoints Allowlist

**New file:** `dalston/gateway/security/public_endpoints.py`

Define `PUBLIC_ENDPOINTS` set (health, metrics, docs, OpenAPI) and `OPTIONAL_AUTH_ENDPOINTS` set (model listing). Provide `is_public_endpoint(path: str) -> bool` helper.

### 2.3: Document Endpoint Classification

**New file:** `docs/specs/implementations/endpoint-security.md`

Table documenting all endpoints with: method, path, auth required (yes/no), required scope, and notes.

---

## Phase 3: Ownership Enforcement (Week 3)

### 3.1: Database Migration

**New file:** `alembic/versions/YYYYMMDD_add_created_by_key_id.py`

Schema changes:

- Add `created_by_key_id` (UUID, nullable, indexed) column to `jobs` table with FK to `api_keys.id` (ON DELETE SET NULL)
- Add `created_by_key_id` (UUID, nullable, indexed) column to `realtime_sessions` table with same FK
- Add `created_by_key_id` (UUID, nullable, indexed) column to `webhook_endpoints` table with same FK
- Add `created_by_key_id` (UUID, nullable, indexed) column to `api_keys` table with self-referencing FK (tracks who created the key)

### 3.2: Update ORM Models

**File:** `dalston/db/models.py`

Add `created_by_key_id` mapped column (with FK and index) to:

- `JobModel`
- `RealtimeSessionModel`
- `WebhookEndpointModel`
- `APIKeyModel`

### 3.3: Update Services

**File:** `dalston/gateway/services/jobs.py`

Add `created_by_key_id: UUID | None = None` parameter to `create_job()` and persist it on the model.

Apply the same pattern to session and webhook creation services.

### 3.4: Update API Handlers

**File:** `dalston/gateway/api/v1/transcription.py`

Pass `api_key.id` as `created_by_key_id` when calling `create_job()` in the `create_transcription()` handler.

---

## Phase 4: Service-Layer Authority (Week 4)

### 4.1: Authorized Service Methods

**File:** `dalston/gateway/services/jobs.py`

Add authorized wrappers (`get_job_authorized`, `delete_job_authorized`) that combine permission checks with ownership enforcement. Logic: call `security_manager.require_permission()`, then verify ownership (non-admin keys only see resources they created). Return `None` / raise `ResourceNotFoundError` for inaccessible resources (anti-enumeration).

### 4.2: Update Handlers

**File:** `dalston/gateway/api/v1/transcription.py`

Update handlers (e.g., `delete_job`) to inject `Principal` and `SecurityManager` via `Depends()` and delegate to the authorized service methods.

---

## Phase 5: Hardening and Audit (Week 5)

### 5.1: Deny-by-Default Test Suite

**New file:** `tests/integration/test_deny_by_default.py`

Test scenarios (parametrized over all protected endpoints):

- All job endpoints (POST/GET/DELETE transcriptions) return 401 without auth
- All model mutation endpoints (pull/delete/sync/resolve) return 401 without auth
- All webhook endpoints return 401 without auth
- All auth/key management endpoints return 401 without auth
- All console admin endpoints return 401 without auth
- UUID path params substituted with dummy values for routing

### 5.2: Security Audit Logging

**File:** `dalston/common/audit.py`

Add two methods to the `AuditService` class:

- `log_permission_denied(principal_id, permission, resource_type, resource_id, ...) -> None` — logs `permission.denied` events
- `log_auth_failure(reason, *, key_prefix, ...) -> None` — logs `auth.failed` events

### 5.3: Architecture Decision Record

**New file:** `docs/decisions/ADR-011-security-architecture.md`

Document: deny-by-default rationale, SecurityManager design, scope-to-permission mapping, ownership enforcement policy, migration strategy.

---

## Verification

- [ ] Model mutation endpoints return 401 without auth and 403 without admin scope
- [ ] `SecurityManager` correctly maps scopes to permissions (unit tests)
- [ ] Exception middleware maps security exceptions to correct HTTP status codes
- [ ] `DALSTON_SECURITY_MODE=none` disables auth checks; `api_key` enforces them
- [ ] Migration adds `created_by_key_id` columns; existing rows unaffected (NULL = tenant-wide access)
- [ ] Ownership enforcement: non-admin key cannot see resources created by another key
- [ ] Deny-by-default test suite passes in CI

---

## Checkpoint

- [ ] `POST /v1/models/{model_id}/pull` requires admin scope
- [ ] `DELETE /v1/models/{model_id}` requires admin scope
- [ ] `POST /v1/models/sync` requires admin scope
- [ ] `POST /v1/models/hf/resolve` requires admin scope
- [ ] `GET /v1/pii/entity-types` requires jobs:read scope
- [ ] Regression tests added and passing
- [ ] SecurityManager class implemented
- [ ] Principal abstraction implemented
- [ ] Permission enum defined
- [ ] Typed security exceptions implemented
- [ ] SecurityErrorHandlerMiddleware registered
- [ ] `DALSTON_SECURITY_MODE` config setting added
- [ ] Public endpoints allowlist documented
- [ ] Database migration for `created_by_key_id` created
- [ ] JobModel has `created_by_key_id` column
- [ ] RealtimeSessionModel has `created_by_key_id` column
- [ ] WebhookEndpointModel has `created_by_key_id` column
- [ ] Services track `created_by_key_id` on creation
- [ ] Authorized service methods implemented
- [ ] Handlers use authorized service methods
- [ ] Deny-by-default test suite passing
- [ ] Security audit logging for permission denials
- [ ] ADR-011 documenting security architecture

---

## Backward Compatibility

1. **Existing API keys continue to work** - No changes to key format or validation
2. **Existing resources accessible** - NULL `created_by_key_id` means "any key in tenant can access"
3. **Default security mode** - `api_key` matches current behavior
4. **Migration is additive** - New nullable columns, no data loss
5. **Phase 0 may break clients** - Clients calling model mutation endpoints without auth will get 401

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking existing clients | Phase 0 only adds auth to previously open endpoints; announce in changelog |
| Performance overhead | SecurityManager checks are O(1) scope lookups |
| Migration failures | Columns are nullable; existing rows unaffected |
| Ownership lockout | Admin keys always bypass ownership checks |
| Complex rollback | Each phase is independently deployable and reversible |

---

## Unblocked

This milestone enables:

- **Enterprise adoption**: Per-API-key isolation for multi-tenant deployments
- **Security compliance**: Clear audit trail of all security decisions
- **Future user auth**: Foundation for M46+ user authentication mode
- **Regulatory compliance**: Demonstrable access control for SOC2/ISO27001
