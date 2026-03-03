# ADR-011: Security Architecture — Deny-by-Default with Centralized Policy

## Status

Accepted (Implemented 2026-03-03, M45 Security Hardening)

---

## 1. Strategic — Goals and Outcomes

### Problem

Before M45, the security implementation had several critical gaps:

1. **Unauthenticated mutation endpoints**: Model management endpoints (`pull`, `delete`, `sync`) were accessible without authentication, allowing unauthenticated users to download models from HuggingFace or delete local models.

2. **No centralized policy engine**: Authorization logic was scattered across `dependencies.py`, `middleware/auth.py`, and individual handlers. Each endpoint implemented its own auth logic, making security audits difficult and increasing the risk of inconsistent enforcement.

3. **No ownership tracking**: The jobs and sessions tables had `tenant_id` for multi-tenant isolation but no `created_by_key_id`. This made it impossible to implement "API key sees only its own resources" — a requirement for enterprise customers who issue limited-scope keys to third parties.

4. **No security mode configuration**: No way to toggle between development (permissive) and production (strict) modes without code changes.

### Goals

1. **Deny-by-default posture**: Every endpoint requires authentication unless explicitly allowlisted. New endpoints are protected by default.

2. **Centralized policy engine**: All authorization decisions go through `SecurityManager`. One place to audit, one place to extend.

3. **Ownership enforcement**: Resources track `created_by_key_id`. Non-admin keys can only access resources they created.

4. **Typed domain exceptions**: Security failures raise domain exceptions (`AuthenticationError`, `AuthorizationError`, `ResourceNotFoundError`), which are mapped once to HTTP responses in middleware.

5. **Anti-enumeration**: Return 404 (not 403) when access is denied due to ownership, preventing attackers from discovering valid resource IDs.

6. **Configurable security modes**: Support `none` (dev), `api_key` (production), and future `user` modes via environment variable.

### Non-goals

- Replacing the existing scope model (jobs:read, jobs:write, admin, etc.)
- Implementing user-based authentication (future M46+)
- Adding OAuth or OIDC support
- Implementing API key rotation

### Success criteria

- All mutation endpoints require authentication (verified by deny-by-default tests)
- SecurityManager handles all permission checks
- Resources track creator for ownership enforcement
- Security audit logging for denied attempts
- All existing tests pass

---

## 2. Tactical — Implementation Choices

### 2.1 Security Module Structure

```
dalston/gateway/security/
├── __init__.py           # Public exports
├── principal.py          # Principal abstraction
├── permissions.py        # Permission enum
├── manager.py            # SecurityManager (policy engine)
├── exceptions.py         # Typed security exceptions
└── public_endpoints.py   # Public allowlist
```

### 2.2 Principal Abstraction

The `Principal` dataclass unifies API keys and session tokens:

```python
@dataclass
class Principal:
    type: PrincipalType      # api_key, session_token, system
    id: UUID                  # Principal identifier
    tenant_id: UUID           # Tenant isolation
    scopes: list[Scope]       # Granted scopes
    key_prefix: str | None    # For audit logging
    parent_key_id: UUID | None  # For session tokens
```

Benefits:

- Consistent interface for authorization checks
- Decouples authorization from credential type
- Enables future identity providers (users, service accounts)

### 2.3 Two-Level Permission Model

**Scopes** (coarse): API key capabilities (jobs:read, jobs:write, admin)

**Permissions** (fine): Specific actions (job:create, job:delete:own)

The mapping is defined in `SecurityManager.SCOPE_PERMISSIONS`:

| Scope | Permissions |
|-------|-------------|
| jobs:read | job:read, job:read:own, session:read, model:read |
| jobs:write | job:create, job:delete:own, job:cancel:own |
| realtime | session:create |
| webhooks | webhook:create, webhook:read, webhook:update, webhook:delete |
| admin | All permissions |

**Why two levels?** Scopes are user-facing (shown in API key creation, documented in API). Permissions are internal (used for ownership-based access control). This separation allows fine-grained internal checks without complicating the external API.

### 2.4 Ownership Enforcement

Resources track creator via `created_by_key_id`:

```sql
ALTER TABLE jobs ADD COLUMN created_by_key_id UUID REFERENCES api_keys(id);
ALTER TABLE realtime_sessions ADD COLUMN created_by_key_id UUID REFERENCES api_keys(id);
ALTER TABLE webhook_endpoints ADD COLUMN created_by_key_id UUID REFERENCES api_keys(id);
```

Access rules:

1. **Admin keys**: Can access all resources in their tenant
2. **Non-admin keys**: Can only access resources they created
3. **NULL created_by_key_id**: Backward compatibility — any key in tenant can access

### 2.5 Anti-Enumeration

When a principal cannot access a resource due to ownership (not permission), return 404 instead of 403:

```python
# BAD: Reveals resource exists
raise AuthorizationError("You don't have permission to access this job")

# GOOD: Anti-enumeration
raise ResourceNotFoundError("job", job_id)
```

This prevents attackers from:

- Discovering valid job IDs by checking 403 vs 404
- Enumerating resources belonging to other API keys

### 2.6 Exception to HTTP Mapping

`SecurityErrorHandlerMiddleware` maps domain exceptions to HTTP responses:

| Exception | HTTP Status | Response |
|-----------|-------------|----------|
| AuthenticationError | 401 | `WWW-Authenticate: Bearer` |
| AuthorizationError | 403 | `required_permission` in body |
| ResourceNotFoundError | 404 | `resource_type` in body |
| RateLimitExceededError | 429 | `Retry-After` header |

### 2.7 Security Modes

Configured via `DALSTON_SECURITY_MODE`:

| Mode | Behavior |
|------|----------|
| `none` | All permissions granted (development only) |
| `api_key` | Check against API key scopes (production default) |
| `user` | Future user-based authentication |

### 2.8 Public Endpoints Allowlist

Endpoints that require NO authentication:

```python
PUBLIC_ENDPOINTS = {
    "/health", "/healthz", "/ready",  # Health checks
    "/metrics",                        # Prometheus
    "/docs", "/redoc", "/openapi.json" # Documentation
}
```

All other endpoints require authentication by default.

---

## 3. Security Audit Logging

Two new methods in `AuditService` for security monitoring:

### log_permission_denied

Records failed authorization attempts:

```python
await audit.log_permission_denied(
    principal_id=principal.id,
    permission="job:delete:own",
    resource_type="job",
    resource_id=str(job_id),
    tenant_id=tenant_id,
    ip_address=request.client.host,
)
```

Use cases:

- Detect privilege escalation attempts
- Identify misconfigured API keys
- Anomaly detection (spike in denials)

### log_auth_failure

Records failed authentication attempts:

```python
await audit.log_auth_failure(
    reason="invalid_key",
    key_prefix="dk_abc123",
    ip_address=request.client.host,
)
```

Use cases:

- Detect brute-force attacks
- Identify compromised key prefixes
- Geographic anomaly detection

---

## 4. Migration Strategy

M45 was implemented in five phases to minimize risk:

### Phase 0: Emergency Fixes (Day 1-2)

Added auth to previously unprotected model endpoints.

### Phase 1: Security Core (Week 1)

Created `dalston/gateway/security/` module with Principal, Permissions, SecurityManager, and exceptions.

### Phase 2: Security Mode Configuration (Week 2)

Added `DALSTON_SECURITY_MODE` setting and public endpoints allowlist.

### Phase 3: Ownership Enforcement (Week 3)

Database migration for `created_by_key_id`. Updated services to track ownership on creation.

### Phase 4: Service-Layer Authorization (Week 4)

Added `*_authorized` methods to services that perform permission and ownership checks.

### Phase 5: Hardening and Audit (Week 5)

Deny-by-default test suite, security audit logging, this ADR.

---

## 5. Verification

### Deny-by-Default Tests

`tests/integration/test_deny_by_default.py` verifies all protected endpoints return 401 without authentication. This test runs in CI to catch security regressions.

```bash
make test -- tests/integration/test_deny_by_default.py
```

### Manual Verification

```bash
# Should return 401
curl -X POST http://localhost:8000/v1/models/test/pull
curl -X DELETE http://localhost:8000/v1/models/test

# Should return 401 with invalid key
curl -H "Authorization: Bearer dk_invalid" http://localhost:8000/v1/audio/transcriptions

# Check audit logs for denied attempts
docker compose exec postgres psql -U dalston -c \
  "SELECT * FROM audit_logs WHERE action = 'permission.denied' ORDER BY created_at DESC LIMIT 5"
```

---

## 6. Backward Compatibility

1. **Existing API keys continue to work**: No changes to key format or validation
2. **Existing resources accessible**: NULL `created_by_key_id` means any tenant key can access
3. **Default security mode**: `api_key` matches previous behavior
4. **Migration is additive**: New nullable columns, no data loss

**Breaking change**: Clients calling model mutation endpoints without auth now get 401.

---

## 7. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking existing integrations | Phase 0 only affects previously unprotected endpoints; announced in changelog |
| Performance overhead | SecurityManager checks are O(1) scope lookups, negligible |
| Migration failures | All new columns are nullable; existing rows unaffected |
| Ownership lockout | Admin keys bypass ownership checks; NULL means tenant-wide access |
| Complex rollback | Each phase is independently deployable and reversible |

---

## 8. Future Considerations

1. **User authentication (M46+)**: The Principal abstraction is ready for user principals
2. **API key rotation**: Not in scope, but ownership model supports it
3. **Attribute-based access control**: Permission model can be extended with resource attributes
4. **Rate limiting per scope**: RateLimitExceededError is implemented but not fully used

---

## 9. Files Changed (M45)

| File | Description |
|------|-------------|
| `dalston/gateway/security/__init__.py` | Security module exports |
| `dalston/gateway/security/principal.py` | Principal abstraction |
| `dalston/gateway/security/permissions.py` | Permission enum |
| `dalston/gateway/security/manager.py` | SecurityManager implementation |
| `dalston/gateway/security/exceptions.py` | Typed security exceptions |
| `dalston/gateway/security/public_endpoints.py` | Public allowlist |
| `dalston/gateway/middleware/security_error_handler.py` | Exception-to-HTTP middleware |
| `dalston/gateway/main.py` | Register middleware |
| `dalston/gateway/dependencies.py` | get_principal, get_security_manager |
| `dalston/config.py` | security_mode setting |
| `dalston/db/models.py` | created_by_key_id columns |
| `dalston/gateway/services/jobs.py` | Ownership tracking and authorized methods |
| `dalston/gateway/services/realtime_sessions.py` | Ownership tracking |
| `dalston/gateway/services/webhook_endpoints.py` | Ownership tracking |
| `dalston/common/audit.py` | log_permission_denied, log_auth_failure |
| `dalston/gateway/api/v1/models.py` | Auth requirements on mutation endpoints |
| `dalston/gateway/api/v1/pii.py` | Auth requirement on entity-types |
| `alembic/versions/*_add_created_by_key_id.py` | Migration for ownership columns |
| `tests/integration/test_deny_by_default.py` | Deny-by-default security tests |

---

## 10. References

- M45 Security Hardening milestone: `docs/plan/milestones/M45-security-hardening.md`
- OWASP API Security Top 10: <https://owasp.org/API-Security/>
- ADR-006: API Key Storage Migration (PostgreSQL as source of truth)
