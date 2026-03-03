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

| Endpoint | Line | Change |
|----------|------|--------|
| `POST /v1/models/{model_id}/pull` | 381 | Add `api_key: RequireAdmin` |
| `DELETE /v1/models/{model_id}` | 458 | Add `api_key: RequireAdmin` |
| `POST /v1/models/sync` | ~497 | Add `api_key: RequireAdmin` |
| `POST /v1/models/hf/resolve` | ~556 | Add `api_key: RequireAdmin` |
| `GET /v1/models/hf/mappings` | ~640 | Add `api_key: RequireJobsRead` |

**Implementation:**

```python
# Before (line 381)
async def pull_model(
    model_id: str,
    request: PullModelRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> PullModelResponse:

# After
async def pull_model(
    model_id: str,
    api_key: RequireAdmin,  # ADD THIS
    request: PullModelRequest | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    service: ModelRegistryService = Depends(get_model_registry_service),
) -> PullModelResponse:
```

### 0.2: Add Auth to PII Entity Types

**File:** `dalston/gateway/api/v1/pii.py`

Line 25: Add `api_key: RequireJobsRead` to `list_entity_types()`.

### 0.3: Regression Tests

**New file:** `tests/integration/test_auth_required.py`

```python
PROTECTED_ENDPOINTS = [
    ("POST", "/v1/models/test-model/pull"),
    ("DELETE", "/v1/models/test-model"),
    ("POST", "/v1/models/sync"),
    ("POST", "/v1/models/hf/resolve"),
    ("GET", "/v1/pii/entity-types"),
]

@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
def test_endpoint_requires_auth(client, method, path):
    """Verify endpoint returns 401 without authentication."""
    response = getattr(client, method.lower())(path)
    assert response.status_code == 401, f"{method} {path} is unprotected!"


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/models/test-model/pull"),
    ("DELETE", "/v1/models/test-model"),
    ("POST", "/v1/models/sync"),
])
def test_model_mutation_requires_admin(client, non_admin_api_key):
    """Verify model mutations require admin scope."""
    headers = {"Authorization": f"Bearer {non_admin_api_key}"}
    response = getattr(client, method.lower())(path, headers=headers)
    assert response.status_code == 403
```

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

```python
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from dalston.gateway.services.auth import APIKey, Scope, SessionToken


class PrincipalType(str, Enum):
    API_KEY = "api_key"
    SESSION_TOKEN = "session_token"
    SYSTEM = "system"


@dataclass
class Principal:
    """Represents an authenticated entity making a request."""

    type: PrincipalType
    id: UUID
    tenant_id: UUID
    scopes: list[Scope]
    key_prefix: str | None = None
    parent_key_id: UUID | None = None  # For session tokens

    @classmethod
    def from_api_key(cls, api_key: APIKey) -> "Principal":
        return cls(
            type=PrincipalType.API_KEY,
            id=api_key.id,
            tenant_id=api_key.tenant_id,
            scopes=api_key.scopes,
            key_prefix=api_key.prefix,
        )

    @classmethod
    def from_session_token(cls, token: SessionToken) -> "Principal":
        return cls(
            type=PrincipalType.SESSION_TOKEN,
            id=token.parent_key_id,
            tenant_id=token.tenant_id,
            scopes=token.scopes,
            parent_key_id=token.parent_key_id,
        )

    @classmethod
    def system(cls, tenant_id: UUID) -> "Principal":
        """System principal for background operations."""
        return cls(
            type=PrincipalType.SYSTEM,
            id=UUID("00000000-0000-0000-0000-000000000000"),
            tenant_id=tenant_id,
            scopes=[Scope.ADMIN],
        )

    def has_scope(self, scope: Scope) -> bool:
        return Scope.ADMIN in self.scopes or scope in self.scopes

    @property
    def actor_id(self) -> str:
        return self.key_prefix or str(self.id)

    @property
    def actor_type(self) -> str:
        return self.type.value
```

### 1.3: Permission Enum

**New file:** `dalston/gateway/security/permissions.py`

```python
from enum import Enum


class Permission(str, Enum):
    """Fine-grained permissions for resource operations."""

    # Job permissions
    JOB_CREATE = "job:create"
    JOB_READ = "job:read"
    JOB_READ_OWN = "job:read:own"
    JOB_DELETE = "job:delete"
    JOB_DELETE_OWN = "job:delete:own"
    JOB_CANCEL = "job:cancel"
    JOB_CANCEL_OWN = "job:cancel:own"

    # Realtime session permissions
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_READ_OWN = "session:read:own"

    # Webhook permissions
    WEBHOOK_CREATE = "webhook:create"
    WEBHOOK_READ = "webhook:read"
    WEBHOOK_UPDATE = "webhook:update"
    WEBHOOK_DELETE = "webhook:delete"

    # Model permissions
    MODEL_READ = "model:read"
    MODEL_PULL = "model:pull"
    MODEL_DELETE = "model:delete"
    MODEL_SYNC = "model:sync"

    # Admin permissions
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    API_KEY_LIST = "api_key:list"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    AUDIT_READ = "audit:read"
```

### 1.4: Typed Security Exceptions

**New file:** `dalston/gateway/security/exceptions.py`

```python
from uuid import UUID


class SecurityError(Exception):
    """Base class for security exceptions."""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


class AuthenticationError(SecurityError):
    """Authentication failed - invalid or missing credentials."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="authentication_failed")


class AuthorizationError(SecurityError):
    """Authorization failed - insufficient permissions."""

    def __init__(
        self,
        message: str = "Permission denied",
        *,
        required_permission: str | None = None,
    ):
        super().__init__(message, code="authorization_failed")
        self.required_permission = required_permission


class ResourceNotFoundError(SecurityError):
    """Resource not found or not accessible to principal.

    Returns 404 instead of 403 to prevent information leakage
    about resource existence (anti-enumeration).
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: str | UUID,
        *,
        message: str | None = None,
    ):
        msg = message or f"{resource_type} not found: {resource_id}"
        super().__init__(msg, code="resource_not_found")
        self.resource_type = resource_type
        self.resource_id = str(resource_id)


class RateLimitExceededError(SecurityError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: int = 60,
    ):
        super().__init__(message, code="rate_limit_exceeded")
        self.retry_after = retry_after
```

### 1.5: SecurityManager

**New file:** `dalston/gateway/security/manager.py`

```python
from typing import Literal
from uuid import UUID

import structlog

from dalston.gateway.security.exceptions import AuthorizationError, ResourceNotFoundError
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import Scope

logger = structlog.get_logger()


# Scope to permission mapping
SCOPE_PERMISSIONS: dict[Scope, set[Permission]] = {
    Scope.JOBS_READ: {
        Permission.JOB_READ,
        Permission.JOB_READ_OWN,
        Permission.SESSION_READ,
        Permission.SESSION_READ_OWN,
        Permission.MODEL_READ,
    },
    Scope.JOBS_WRITE: {
        Permission.JOB_CREATE,
        Permission.JOB_DELETE_OWN,
        Permission.JOB_CANCEL_OWN,
    },
    Scope.REALTIME: {
        Permission.SESSION_CREATE,
    },
    Scope.WEBHOOKS: {
        Permission.WEBHOOK_CREATE,
        Permission.WEBHOOK_READ,
        Permission.WEBHOOK_UPDATE,
        Permission.WEBHOOK_DELETE,
    },
    Scope.ADMIN: set(Permission),  # All permissions
}


class SecurityManager:
    """Centralized authorization policy engine."""

    def __init__(self, mode: Literal["none", "api_key", "user"] = "api_key"):
        self.mode = mode

    def has_permission(self, principal: Principal, permission: Permission) -> bool:
        """Check if principal has the required permission."""
        if self.mode == "none":
            return True

        if Scope.ADMIN in principal.scopes:
            return True

        for scope in principal.scopes:
            if permission in SCOPE_PERMISSIONS.get(scope, set()):
                return True

        return False

    def require_permission(self, principal: Principal, permission: Permission) -> None:
        """Require principal has permission, raise if not."""
        if not self.has_permission(principal, permission):
            logger.warning(
                "permission_denied",
                principal_id=str(principal.id),
                permission=permission.value,
            )
            raise AuthorizationError(
                f"Missing required permission: {permission.value}",
                required_permission=permission.value,
            )

    def can_access_resource(
        self,
        principal: Principal,
        resource_tenant_id: UUID,
        resource_created_by: UUID | None = None,
    ) -> bool:
        """Check if principal can access a resource."""
        if self.mode == "none":
            return True

        # Must be same tenant
        if principal.tenant_id != resource_tenant_id:
            return False

        # Admin can access all resources in tenant
        if Scope.ADMIN in principal.scopes:
            return True

        # If ownership enforcement enabled and resource has creator,
        # only owner can access
        if resource_created_by is not None:
            return principal.id == resource_created_by

        # No ownership info = tenant-wide access (backward compat)
        return True

    def require_resource_access(
        self,
        principal: Principal,
        resource_tenant_id: UUID,
        resource_type: str,
        resource_id: str | UUID,
        resource_created_by: UUID | None = None,
    ) -> None:
        """Require principal can access resource, raise if not."""
        if not self.can_access_resource(principal, resource_tenant_id, resource_created_by):
            raise ResourceNotFoundError(resource_type, resource_id)


# Singleton
_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    """Get the global security manager instance."""
    global _security_manager
    if _security_manager is None:
        from dalston.config import get_settings
        settings = get_settings()
        mode = getattr(settings, "security_mode", "api_key")
        _security_manager = SecurityManager(mode=mode)
    return _security_manager
```

### 1.6: Exception Handler Middleware

**New file:** `dalston/gateway/middleware/security_error_handler.py`

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from dalston.gateway.security.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitExceededError,
    ResourceNotFoundError,
    SecurityError,
)


class SecurityErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Convert domain security exceptions to HTTP responses."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except AuthenticationError as e:
            return JSONResponse(
                status_code=401,
                content={"detail": str(e), "code": e.code},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except AuthorizationError as e:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": str(e),
                    "code": e.code,
                    "required_permission": e.required_permission,
                },
            )
        except ResourceNotFoundError as e:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": str(e),
                    "code": e.code,
                    "resource_type": e.resource_type,
                },
            )
        except RateLimitExceededError as e:
            return JSONResponse(
                status_code=429,
                content={"detail": str(e), "code": e.code},
                headers={"Retry-After": str(e.retry_after)},
            )
        except SecurityError as e:
            return JSONResponse(
                status_code=403,
                content={"detail": str(e), "code": e.code},
            )
```

### 1.7: Register Middleware

**File:** `dalston/gateway/main.py`

Add after existing middleware registrations:

```python
from dalston.gateway.middleware.security_error_handler import (
    SecurityErrorHandlerMiddleware,
)

# In create_app() or lifespan:
app.add_middleware(SecurityErrorHandlerMiddleware)
```

### 1.8: Add Dependencies

**File:** `dalston/gateway/dependencies.py`

Add after line 128:

```python
from dalston.gateway.security.manager import (
    SecurityManager,
    get_security_manager as _get_security_manager,
)
from dalston.gateway.security.principal import Principal


def get_security_manager() -> SecurityManager:
    """Get SecurityManager instance."""
    return _get_security_manager()


async def get_principal(
    api_key: APIKey = Depends(require_auth),
) -> Principal:
    """Get authenticated Principal from request."""
    from dalston.gateway.services.auth import SessionToken

    if isinstance(api_key, SessionToken):
        return Principal.from_session_token(api_key)
    return Principal.from_api_key(api_key)
```

---

## Phase 2: Security Mode Configuration (Week 2)

### 2.1: Add Config Setting

**File:** `dalston/config.py`

Add after line 140:

```python
    # Security Mode (M45)
    security_mode: Literal["none", "api_key", "user"] = Field(
        default="api_key",
        alias="DALSTON_SECURITY_MODE",
        description=(
            "Security mode: 'none' (no auth checks, dev only), "
            "'api_key' (API key validation), "
            "'user' (future user auth)"
        ),
    )
```

### 2.2: Public Endpoints Allowlist

**New file:** `dalston/gateway/security/public_endpoints.py`

```python
"""Public endpoints allowlist for deny-by-default enforcement."""

# Endpoints that require NO authentication
PUBLIC_ENDPOINTS: set[str] = {
    "/health",
    "/healthz",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Endpoints with optional authentication
OPTIONAL_AUTH_ENDPOINTS: set[str] = {
    "/v1/models",
    "/v1/models/{model_id}",
}


def is_public_endpoint(path: str) -> bool:
    """Check if endpoint is in public allowlist."""
    if path in PUBLIC_ENDPOINTS:
        return True
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True
    return False
```

### 2.3: Document Endpoint Classification

**New file:** `docs/specs/implementations/endpoint-security.md`

Document all endpoints with their auth requirements:

| Endpoint | Method | Auth Required | Scope | Notes |
|----------|--------|---------------|-------|-------|
| `/health` | GET | No | - | Health check |
| `/v1/audio/transcriptions` | POST | Yes | jobs:write | Create job |
| `/v1/models/{id}/pull` | POST | Yes | admin | Download model |
| ... | ... | ... | ... | ... |

---

## Phase 3: Ownership Enforcement (Week 3)

### 3.1: Database Migration

**New file:** `alembic/versions/YYYYMMDD_add_created_by_key_id.py`

```python
"""Add created_by_key_id to jobs, sessions, webhooks, api_keys.

Revision ID: XXXX
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "XXXX"
down_revision = "PREVIOUS"


def upgrade() -> None:
    # Jobs table
    op.add_column(
        "jobs",
        sa.Column(
            "created_by_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # Realtime sessions table
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "created_by_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # Webhook endpoints table
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "created_by_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # API keys table (who created this key)
    op.add_column(
        "api_keys",
        sa.Column(
            "created_by_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "created_by_key_id")
    op.drop_column("webhook_endpoints", "created_by_key_id")
    op.drop_column("realtime_sessions", "created_by_key_id")
    op.drop_column("jobs", "created_by_key_id")
```

### 3.2: Update ORM Models

**File:** `dalston/db/models.py`

Add to `JobModel` after line 243:

```python
    # Ownership tracking (M45)
    created_by_key_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
```

Apply same pattern to:

- `RealtimeSessionModel` (after line ~680)
- `WebhookEndpointModel` (after line ~395)
- `APIKeyModel` (after line ~345)

### 3.3: Update Services

**File:** `dalston/gateway/services/jobs.py`

Update `create_job` signature (line 47):

```python
async def create_job(
    self,
    db: AsyncSession,
    job_id: UUID,
    tenant_id: UUID,
    audio_uri: str,
    parameters: dict[str, Any],
    *,
    created_by_key_id: UUID | None = None,  # ADD
    # ... existing params
) -> JobModel:
    job = JobModel(
        # ... existing fields
        created_by_key_id=created_by_key_id,  # ADD
    )
```

### 3.4: Update API Handlers

**File:** `dalston/gateway/api/v1/transcription.py`

Pass `api_key.id` to service:

```python
job = await jobs_service.create_job(
    db,
    job_id=job_id,
    tenant_id=api_key.tenant_id,
    created_by_key_id=api_key.id,  # ADD
    # ... other params
)
```

---

## Phase 4: Service-Layer Authority (Week 4)

### 4.1: Authorized Service Methods

**File:** `dalston/gateway/services/jobs.py`

Add new authorized methods:

```python
async def get_job_authorized(
    self,
    db: AsyncSession,
    job_id: UUID,
    principal: Principal,
    security_manager: SecurityManager,
) -> JobModel | None:
    """Get job with authorization check."""
    security_manager.require_permission(principal, Permission.JOB_READ_OWN)

    job = await self.get_job(db, job_id, tenant_id=principal.tenant_id)
    if job is None:
        return None

    # Check ownership for non-admin
    if Scope.ADMIN not in principal.scopes:
        if job.created_by_key_id and job.created_by_key_id != principal.id:
            return None  # Return None to map to 404

    return job


async def delete_job_authorized(
    self,
    db: AsyncSession,
    job_id: UUID,
    principal: Principal,
    security_manager: SecurityManager,
    *,
    audit_service: AuditService | None = None,
) -> JobModel | None:
    """Delete job with authorization check."""
    security_manager.require_permission(principal, Permission.JOB_DELETE_OWN)

    job = await self.get_job(db, job_id, tenant_id=principal.tenant_id)
    if job is None:
        raise ResourceNotFoundError("job", job_id)

    if Scope.ADMIN not in principal.scopes:
        if job.created_by_key_id and job.created_by_key_id != principal.id:
            raise ResourceNotFoundError("job", job_id)

    return await self.delete_job(
        db,
        job_id,
        tenant_id=principal.tenant_id,
        audit_service=audit_service,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
    )
```

### 4.2: Update Handlers

**File:** `dalston/gateway/api/v1/transcription.py`

```python
@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    """Delete a completed/failed job."""
    await jobs_service.delete_job_authorized(
        db,
        job_id,
        principal,
        security_manager,
        audit_service=audit_service,
    )
    return Response(status_code=204)
```

---

## Phase 5: Hardening and Audit (Week 5)

### 5.1: Deny-by-Default Test Suite

**New file:** `tests/integration/test_deny_by_default.py`

```python
"""Deny-by-default security tests.

These tests FAIL if any protected endpoint is reachable without auth.
Run as part of CI to prevent security regressions.
"""

import pytest
from fastapi.testclient import TestClient

PROTECTED_ENDPOINTS = [
    # Jobs
    ("POST", "/v1/audio/transcriptions"),
    ("GET", "/v1/audio/transcriptions"),
    ("GET", "/v1/audio/transcriptions/{job_id}"),
    ("DELETE", "/v1/audio/transcriptions/{job_id}"),

    # Models (mutation)
    ("POST", "/v1/models/{model_id}/pull"),
    ("DELETE", "/v1/models/{model_id}"),
    ("POST", "/v1/models/sync"),
    ("POST", "/v1/models/hf/resolve"),

    # Webhooks
    ("POST", "/v1/webhooks"),
    ("GET", "/v1/webhooks"),
    ("DELETE", "/v1/webhooks/{endpoint_id}"),

    # Auth
    ("GET", "/auth/keys"),
    ("POST", "/auth/keys"),
    ("DELETE", "/auth/keys/{key_id}"),

    # Console (admin)
    ("GET", "/api/console/dashboard"),
    ("DELETE", "/api/console/jobs/{job_id}"),
    ("PATCH", "/api/console/settings/{namespace}"),
]


@pytest.fixture
def client():
    from dalston.gateway.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
def test_endpoint_requires_auth(client, method, path):
    """Verify endpoint returns 401 without authentication."""
    test_path = path.replace("{job_id}", "00000000-0000-0000-0000-000000000001")
    test_path = test_path.replace("{model_id}", "test-model")
    test_path = test_path.replace("{endpoint_id}", "00000000-0000-0000-0000-000000000001")
    test_path = test_path.replace("{key_id}", "00000000-0000-0000-0000-000000000001")
    test_path = test_path.replace("{namespace}", "rate_limits")

    response = getattr(client, method.lower())(test_path)

    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code}, expected 401. "
        "Endpoint may be missing authentication requirement."
    )
```

### 5.2: Security Audit Logging

**File:** `dalston/common/audit.py`

Add after line ~499:

```python
async def log_permission_denied(
    self,
    principal_id: UUID,
    permission: str,
    resource_type: str,
    resource_id: str,
    *,
    tenant_id: UUID | None = None,
    correlation_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Log permission denied event for security monitoring."""
    await self.log(
        action="permission.denied",
        resource_type=resource_type,
        resource_id=resource_id,
        tenant_id=tenant_id,
        actor_type="api_key",
        actor_id=str(principal_id),
        detail={"required_permission": permission},
        correlation_id=correlation_id,
        ip_address=ip_address,
    )


async def log_auth_failure(
    self,
    reason: str,
    *,
    key_prefix: str | None = None,
    correlation_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Log authentication failure for security monitoring."""
    await self.log(
        action="auth.failed",
        resource_type="api_key",
        resource_id=key_prefix or "unknown",
        actor_type="anonymous",
        actor_id="unknown",
        detail={"reason": reason},
        correlation_id=correlation_id,
        ip_address=ip_address,
    )
```

### 5.3: Architecture Decision Record

**New file:** `docs/decisions/ADR-011-security-architecture.md`

Document:

- Deny-by-default rationale
- SecurityManager design
- Scope-to-permission mapping
- Ownership enforcement policy
- Migration strategy

---

## Verification

### Phase 0

```bash
# Verify model endpoints require auth
curl -X POST http://localhost:8000/v1/models/test/pull
# Expected: {"detail":"Authentication required","code":"authentication_failed"}

curl -X DELETE http://localhost:8000/v1/models/test
# Expected: 401 Unauthorized

# Run regression tests
make test -- tests/integration/test_auth_required.py
```

### Phase 1

```bash
# Unit tests for security module
make test -- tests/unit/test_security_manager.py

# Verify exception mapping
curl -H "Authorization: Bearer dk_invalid" http://localhost:8000/v1/audio/transcriptions
# Expected: 401 with {"code": "authentication_failed"}
```

### Phase 2

```bash
# Test security_mode=none (development)
DALSTON_SECURITY_MODE=none make dev
curl -X POST http://localhost:8000/v1/models/test/pull
# Expected: 200 (no auth check)

# Test security_mode=api_key (default)
DALSTON_SECURITY_MODE=api_key make dev
curl -X POST http://localhost:8000/v1/models/test/pull
# Expected: 401
```

### Phase 3

```bash
# Run migration
alembic upgrade head

# Verify schema
docker compose exec postgres psql -U dalston -c "\d jobs" | grep created_by_key_id
# Expected: created_by_key_id | uuid | ...

# Test ownership tracking
# 1. Create job with API key A
# 2. Try to access with API key B (non-admin, same tenant)
# Expected: 404 Not Found
```

### Phase 4-5

```bash
# Full test suite
make test

# Deny-by-default tests
make test -- tests/integration/test_deny_by_default.py

# Verify audit logs for denied attempts
docker compose exec redis redis-cli XRANGE dalston:audit:stream - + COUNT 10
# Look for action: "permission.denied" entries
```

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

## Files Changed

| File | Description |
|------|-------------|
| `dalston/gateway/api/v1/models.py` | Add RequireAdmin to model mutation endpoints |
| `dalston/gateway/api/v1/pii.py` | Add RequireJobsRead to entity-types endpoint |
| `dalston/gateway/security/__init__.py` | New: Security module exports |
| `dalston/gateway/security/principal.py` | New: Principal abstraction |
| `dalston/gateway/security/permissions.py` | New: Permission enum |
| `dalston/gateway/security/manager.py` | New: SecurityManager implementation |
| `dalston/gateway/security/exceptions.py` | New: Typed security exceptions |
| `dalston/gateway/security/public_endpoints.py` | New: Public allowlist |
| `dalston/gateway/middleware/security_error_handler.py` | New: Exception-to-HTTP middleware |
| `dalston/gateway/main.py` | Register SecurityErrorHandlerMiddleware |
| `dalston/gateway/dependencies.py` | Add get_principal, get_security_manager |
| `dalston/config.py` | Add security_mode setting |
| `dalston/db/models.py` | Add created_by_key_id to Job, Session, Webhook, APIKey |
| `dalston/gateway/services/jobs.py` | Add created_by_key_id param, authorized methods |
| `dalston/gateway/services/realtime_sessions.py` | Add created_by_key_id param |
| `dalston/gateway/services/webhook_endpoints.py` | Add created_by_key_id param |
| `dalston/gateway/api/v1/transcription.py` | Pass api_key.id, use authorized methods |
| `dalston/common/audit.py` | Add log_permission_denied, log_auth_failure |
| `alembic/versions/xxx_add_created_by_key_id.py` | Migration for ownership columns |
| `tests/integration/test_auth_required.py` | New: Auth requirement regression tests |
| `tests/integration/test_deny_by_default.py` | New: Deny-by-default security tests |
| `tests/unit/test_security_manager.py` | New: SecurityManager unit tests |
| `docs/decisions/ADR-011-security-architecture.md` | New: Security architecture ADR |
| `docs/specs/implementations/endpoint-security.md` | New: Endpoint classification doc |

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
