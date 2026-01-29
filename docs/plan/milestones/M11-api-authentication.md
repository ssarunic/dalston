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

```
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
│                                            │                         │
│                            Query param for WebSocket (no headers)    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### API Key

```python
# dalston/gateway/models/auth.py

from pydantic import BaseModel
from datetime import datetime

class APIKey(BaseModel):
    """API key for authentication."""
    id: str                          # uuid
    key_hash: str                    # sha256 hash (never store plaintext)
    prefix: str                      # "dk_abc1234" first 10 chars for display
    name: str                        # "Production", "Development", etc.
    tenant_id: str                   # "default" initially, enables multi-tenancy later
    scopes: list[str]                # ["jobs:read", "jobs:write", "realtime", "admin"]
    rate_limit: int | None           # requests/minute, None = unlimited
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

class APIKeyCreate(BaseModel):
    """Request to create a new API key."""
    name: str
    scopes: list[str] = ["jobs:read", "jobs:write", "realtime"]
    rate_limit: int | None = None

class APIKeyResponse(BaseModel):
    """API key info (without secret)."""
    id: str
    prefix: str
    name: str
    scopes: list[str]
    rate_limit: int | None
    created_at: datetime
    last_used_at: datetime | None

class APIKeyCreated(APIKeyResponse):
    """Response when creating a key - includes full key ONCE."""
    key: str  # Full key, shown only at creation time
```

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

```python
# dalston/gateway/services/auth.py

import hashlib
import secrets
from datetime import datetime, UTC
from dalston.gateway.models.auth import APIKey, APIKeyCreate, APIKeyCreated

KEY_PREFIX = "dk_"

def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its hash."""
    # Generate 32 random bytes = 256 bits of entropy
    random_part = secrets.token_urlsafe(32)
    full_key = f"{KEY_PREFIX}{random_part}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_hash

def hash_api_key(key: str) -> str:
    """Hash an API key for lookup."""
    return hashlib.sha256(key.encode()).hexdigest()

class AuthService:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def create_api_key(
        self,
        request: APIKeyCreate,
        tenant_id: str = "default"
    ) -> APIKeyCreated:
        """Create a new API key."""
        full_key, key_hash = generate_api_key()

        api_key = APIKey(
            id=str(uuid.uuid4()),
            key_hash=key_hash,
            prefix=full_key[:10],  # "dk_abc1234"
            name=request.name,
            tenant_id=tenant_id,
            scopes=request.scopes,
            rate_limit=request.rate_limit,
            created_at=datetime.now(UTC),
            last_used_at=None,
            revoked_at=None,
        )

        # Store by hash for O(1) lookup
        await self.redis.set(
            f"dalston:apikeys:{key_hash}",
            api_key.model_dump_json()
        )

        # Index by id for management
        await self.redis.set(
            f"dalston:apikeys:id:{api_key.id}",
            key_hash
        )

        # Index by tenant for listing
        await self.redis.sadd(
            f"dalston:apikeys:tenant:{tenant_id}",
            api_key.id
        )

        return APIKeyCreated(
            id=api_key.id,
            prefix=api_key.prefix,
            name=api_key.name,
            scopes=api_key.scopes,
            rate_limit=api_key.rate_limit,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            key=full_key,  # Only returned at creation!
        )

    async def validate_api_key(self, key: str) -> APIKey | None:
        """Validate an API key and return its data."""
        if not key.startswith(KEY_PREFIX):
            return None

        key_hash = hash_api_key(key)
        data = await self.redis.get(f"dalston:apikeys:{key_hash}")

        if not data:
            return None

        api_key = APIKey.model_validate_json(data)

        # Check if revoked
        if api_key.revoked_at:
            return None

        # Update last_used_at (async, don't block)
        api_key.last_used_at = datetime.now(UTC)
        await self.redis.set(
            f"dalston:apikeys:{key_hash}",
            api_key.model_dump_json()
        )

        return api_key

    async def list_api_keys(self, tenant_id: str) -> list[APIKeyResponse]:
        """List all API keys for a tenant."""
        key_ids = await self.redis.smembers(f"dalston:apikeys:tenant:{tenant_id}")

        keys = []
        for key_id in key_ids:
            key_hash = await self.redis.get(f"dalston:apikeys:id:{key_id}")
            if key_hash:
                data = await self.redis.get(f"dalston:apikeys:{key_hash}")
                if data:
                    api_key = APIKey.model_validate_json(data)
                    if not api_key.revoked_at:
                        keys.append(APIKeyResponse(
                            id=api_key.id,
                            prefix=api_key.prefix,
                            name=api_key.name,
                            scopes=api_key.scopes,
                            rate_limit=api_key.rate_limit,
                            created_at=api_key.created_at,
                            last_used_at=api_key.last_used_at,
                        ))

        return keys

    async def revoke_api_key(self, key_id: str, tenant_id: str) -> bool:
        """Revoke an API key."""
        key_hash = await self.redis.get(f"dalston:apikeys:id:{key_id}")
        if not key_hash:
            return False

        data = await self.redis.get(f"dalston:apikeys:{key_hash}")
        if not data:
            return False

        api_key = APIKey.model_validate_json(data)

        # Verify tenant ownership
        if api_key.tenant_id != tenant_id:
            return False

        api_key.revoked_at = datetime.now(UTC)
        await self.redis.set(
            f"dalston:apikeys:{key_hash}",
            api_key.model_dump_json()
        )

        return True
```

---

### 11.2: Authentication Middleware

```python
# dalston/gateway/middleware/auth.py

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dalston.gateway.models.auth import APIKey
from dalston.gateway.services.auth import AuthService

security = HTTPBearer(auto_error=False)

async def get_auth_service(request: Request) -> AuthService:
    """Get auth service from app state."""
    return request.app.state.auth_service

async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKey:
    """Require valid API key authentication."""

    token = None

    # Try Authorization header first
    if credentials:
        token = credentials.credentials

    # Fall back to query param (for WebSocket)
    if not token:
        token = request.query_params.get("api_key")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide via 'Authorization: Bearer dk_xxx' header or 'api_key' query param.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = await auth_service.validate_api_key(token)

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check rate limit
    if api_key.rate_limit:
        is_allowed = await check_rate_limit(
            auth_service.redis,
            api_key.id,
            api_key.rate_limit
        )
        if not is_allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({api_key.rate_limit}/min)",
            )

    # Attach to request for use in handlers
    request.state.api_key = api_key
    request.state.tenant_id = api_key.tenant_id

    return api_key


def require_scope(scope: str):
    """Dependency that requires a specific scope."""
    async def checker(api_key: APIKey = Depends(require_auth)) -> APIKey:
        if "admin" in api_key.scopes:
            return api_key  # Admin has all permissions

        if scope not in api_key.scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: {scope}",
            )
        return api_key
    return checker


async def check_rate_limit(redis, key_id: str, limit: int) -> bool:
    """Check and update rate limit counter."""
    key = f"dalston:ratelimit:{key_id}"

    # Use Redis pipeline for atomic increment + expire
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)  # 1 minute window
    results = await pipe.execute()

    current_count = results[0]
    return current_count <= limit
```

---

### 11.3: Apply Auth to Routes

```python
# dalston/gateway/api/v1/transcriptions.py

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from dalston.gateway.middleware.auth import require_scope
from dalston.gateway.models.auth import APIKey

router = APIRouter(tags=["Transcriptions"])

@router.post("/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    api_key: APIKey = Depends(require_scope("jobs:write")),
):
    """Create a new transcription job."""
    job = await jobs_service.create_job(
        file=file,
        tenant_id=api_key.tenant_id,  # Scope to tenant
    )
    return job


@router.get("/audio/transcriptions")
async def list_transcriptions(
    limit: int = 20,
    offset: int = 0,
    api_key: APIKey = Depends(require_scope("jobs:read")),
):
    """List transcription jobs for this tenant."""
    return await jobs_service.list_jobs(
        tenant_id=api_key.tenant_id,  # Only show tenant's jobs
        limit=limit,
        offset=offset,
    )


@router.get("/audio/transcriptions/{job_id}")
async def get_transcription(
    job_id: str,
    api_key: APIKey = Depends(require_scope("jobs:read")),
):
    """Get a transcription job."""
    job = await jobs_service.get_job(
        job_id=job_id,
        tenant_id=api_key.tenant_id,  # Verify ownership
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.delete("/audio/transcriptions/{job_id}")
async def cancel_transcription(
    job_id: str,
    api_key: APIKey = Depends(require_scope("jobs:write")),
):
    """Cancel a transcription job."""
    success = await jobs_service.cancel_job(
        job_id=job_id,
        tenant_id=api_key.tenant_id,
    )
    if not success:
        raise HTTPException(404, "Job not found")
    return {"status": "cancelled"}
```

---

### 11.4: WebSocket Authentication

```python
# dalston/gateway/api/v1/realtime.py

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from dalston.gateway.services.auth import AuthService

router = APIRouter(tags=["Realtime"])

@router.websocket("/audio/transcriptions/stream")
async def stream_transcription(
    websocket: WebSocket,
    api_key: str = Query(..., description="API key for authentication"),
    language: str = Query("auto"),
    model: str = Query("fast"),
):
    """Real-time transcription via WebSocket."""

    # Validate API key before accepting connection
    auth_service: AuthService = websocket.app.state.auth_service
    key = await auth_service.validate_api_key(api_key)

    if not key:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    if "realtime" not in key.scopes and "admin" not in key.scopes:
        await websocket.close(code=4003, reason="Missing 'realtime' scope")
        return

    # Check rate limit
    if key.rate_limit:
        is_allowed = await check_rate_limit(
            auth_service.redis, key.id, key.rate_limit
        )
        if not is_allowed:
            await websocket.close(code=4029, reason="Rate limit exceeded")
            return

    await websocket.accept()

    # Create session scoped to tenant
    session = await session_router.acquire_worker(
        tenant_id=key.tenant_id,
        language=language,
        model=model,
        client_ip=websocket.client.host,
    )

    if not session:
        await websocket.send_json({
            "type": "error",
            "code": "no_capacity",
            "message": "No workers available"
        })
        await websocket.close()
        return

    try:
        # ... existing streaming logic ...
        pass
    finally:
        await session_router.release_worker(session.session_id)
```

---

### 11.5: Auth Management Endpoints

```python
# dalston/gateway/api/auth.py

from fastapi import APIRouter, Depends, HTTPException
from dalston.gateway.middleware.auth import require_auth, require_scope
from dalston.gateway.models.auth import APIKey, APIKeyCreate, APIKeyResponse, APIKeyCreated
from dalston.gateway.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/keys", response_model=APIKeyCreated)
async def create_api_key(
    request: APIKeyCreate,
    api_key: APIKey = Depends(require_scope("admin")),
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    Create a new API key.

    **Important**: The full key is only returned once at creation time.
    Store it securely - it cannot be retrieved later.
    """
    return await auth_service.create_api_key(
        request=request,
        tenant_id=api_key.tenant_id,
    )


@router.get("/keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    api_key: APIKey = Depends(require_scope("admin")),
    auth_service: AuthService = Depends(get_auth_service),
):
    """List all API keys for this tenant."""
    return await auth_service.list_api_keys(api_key.tenant_id)


@router.get("/keys/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: str,
    api_key: APIKey = Depends(require_scope("admin")),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Get API key details (without the secret)."""
    keys = await auth_service.list_api_keys(api_key.tenant_id)
    for key in keys:
        if key.id == key_id:
            return key
    raise HTTPException(404, "API key not found")


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    api_key: APIKey = Depends(require_scope("admin")),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Revoke an API key. This action cannot be undone."""
    success = await auth_service.revoke_api_key(key_id, api_key.tenant_id)
    if not success:
        raise HTTPException(404, "API key not found")
    return {"status": "revoked"}


@router.get("/me", response_model=APIKeyResponse)
async def get_current_key(
    api_key: APIKey = Depends(require_auth),
):
    """Get information about the current API key."""
    return APIKeyResponse(
        id=api_key.id,
        prefix=api_key.prefix,
        name=api_key.name,
        scopes=api_key.scopes,
        rate_limit=api_key.rate_limit,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
    )
```

---

### 11.6: Bootstrap Admin Key

```python
# dalston/gateway/cli.py

import asyncio
import click
from dalston.gateway.services.auth import AuthService
from dalston.gateway.models.auth import APIKeyCreate

@click.group()
def cli():
    """Dalston CLI utilities."""
    pass

@cli.command()
@click.option("--name", default="Admin", help="Name for the API key")
@click.option("--redis-url", default="redis://localhost:6379", help="Redis URL")
def create_admin_key(name: str, redis_url: str):
    """Create an admin API key for initial setup."""

    async def _create():
        import redis.asyncio as redis
        client = redis.from_url(redis_url)
        auth_service = AuthService(client)

        key = await auth_service.create_api_key(
            request=APIKeyCreate(
                name=name,
                scopes=["admin"],
            ),
            tenant_id="default",
        )

        await client.close()
        return key

    key = asyncio.run(_create())

    click.echo("")
    click.echo("=" * 60)
    click.echo("  ADMIN API KEY CREATED")
    click.echo("=" * 60)
    click.echo("")
    click.echo(f"  Key ID:   {key.id}")
    click.echo(f"  Name:     {key.name}")
    click.echo(f"  Scopes:   {', '.join(key.scopes)}")
    click.echo("")
    click.echo("  🔑 Your API Key (save this - shown only once!):")
    click.echo("")
    click.echo(f"     {key.key}")
    click.echo("")
    click.echo("  Usage:")
    click.echo(f'     curl -H "Authorization: Bearer {key.key}" \\')
    click.echo("          http://localhost:8000/v1/audio/transcriptions")
    click.echo("")
    click.echo("=" * 60)


if __name__ == "__main__":
    cli()
```

---

### 11.7: Update Main App

```python
# dalston/gateway/main.py

from fastapi import FastAPI
from dalston.gateway.api import auth, transcriptions, realtime, console
from dalston.gateway.services.auth import AuthService
import redis.asyncio as redis

app = FastAPI(title="Dalston", version="1.0.0")

@app.on_event("startup")
async def startup():
    # Initialize Redis
    app.state.redis = redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379")
    )

    # Initialize auth service
    app.state.auth_service = AuthService(app.state.redis)


@app.on_event("shutdown")
async def shutdown():
    await app.state.redis.close()


# Auth endpoints (no /v1 prefix - not versioned API)
app.include_router(auth.router)

# Transcription API
app.include_router(transcriptions.router, prefix="/v1")

# Realtime API
app.include_router(realtime.router, prefix="/v1")

# Console API (also requires auth)
app.include_router(console.router)


# Health check - no auth required
@app.get("/health")
async def health():
    return {"status": "healthy"}
```

---

### 11.8: Update Job Model for Tenant Scoping

```python
# dalston/gateway/models/job.py

class Job(BaseModel):
    id: str
    tenant_id: str  # NEW: Required for multi-tenancy
    status: JobStatus
    # ... rest of fields ...


# dalston/gateway/services/jobs.py

async def create_job(self, file: UploadFile, tenant_id: str) -> Job:
    """Create a job scoped to a tenant."""
    job = Job(
        id=f"job_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,  # Store tenant
        status=JobStatus.PENDING,
        # ...
    )
    await self.redis.set(f"dalston:job:{job.id}", job.model_dump_json())

    # Index by tenant for listing
    await self.redis.sadd(f"dalston:jobs:tenant:{tenant_id}", job.id)

    return job


async def get_job(self, job_id: str, tenant_id: str) -> Job | None:
    """Get a job, verifying tenant ownership."""
    data = await self.redis.get(f"dalston:job:{job_id}")
    if not data:
        return None

    job = Job.model_validate_json(data)

    # Verify tenant ownership
    if job.tenant_id != tenant_id:
        return None

    return job


async def list_jobs(
    self,
    tenant_id: str,
    limit: int = 20,
    offset: int = 0
) -> list[Job]:
    """List jobs for a tenant."""
    job_ids = await self.redis.smembers(f"dalston:jobs:tenant:{tenant_id}")

    jobs = []
    for job_id in list(job_ids)[offset:offset + limit]:
        data = await self.redis.get(f"dalston:job:{job_id}")
        if data:
            jobs.append(Job.model_validate_json(data))

    return sorted(jobs, key=lambda j: j.created_at, reverse=True)
```

---

## Verification

### Create Admin Key

```bash
# First-time setup: create admin key
python -m dalston.gateway.cli create-admin-key --name "My Admin Key"

# Output:
# ============================================================
#   ADMIN API KEY CREATED
# ============================================================
#
#   Key ID:   550e8400-e29b-41d4-a716-446655440000
#   Name:     My Admin Key
#   Scopes:   admin
#
#   🔑 Your API Key (save this - shown only once!):
#
#      dk_a1b2c3d4e5f6g7h8i9j0...
#
# ============================================================
```

### Test Authentication

```bash
# Set your key
export DALSTON_API_KEY="dk_a1b2c3d4e5f6g7h8i9j0..."

# ✅ Authenticated request
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@audio.mp3"

# ❌ Missing key
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@audio.mp3"
# → 401 Unauthorized

# ❌ Invalid key
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_invalid" \
  -F "file=@audio.mp3"
# → 401 Invalid or revoked API key
```

### Test Key Management

```bash
# Create a limited key
curl -X POST http://localhost:8000/auth/keys \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Read Only", "scopes": ["jobs:read"]}'

# List keys
curl http://localhost:8000/auth/keys \
  -H "Authorization: Bearer $DALSTON_API_KEY"

# Revoke a key
curl -X DELETE http://localhost:8000/auth/keys/{key_id} \
  -H "Authorization: Bearer $DALSTON_API_KEY"
```

### Test WebSocket Authentication

```bash
# ✅ With API key
websocat "ws://localhost:8000/v1/audio/transcriptions/stream?api_key=$DALSTON_API_KEY&language=en"

# ❌ Without API key
websocat "ws://localhost:8000/v1/audio/transcriptions/stream?language=en"
# → Connection closed: 4001 Invalid API key
```

### Test Tenant Isolation

```bash
# Create second tenant key (as admin)
curl -X POST http://localhost:8000/auth/keys \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -d '{"name": "Other Tenant", "scopes": ["jobs:read", "jobs:write"]}'

# Jobs created with key A are not visible to key B
# Each key only sees its own tenant's jobs
```

---

## Checkpoint

✓ **API keys** stored as SHA256 hashes in Redis
✓ **Auth middleware** validates all REST endpoints
✓ **WebSocket auth** via query parameter
✓ **Scopes** control access to specific operations
✓ **Rate limiting** per API key
✓ **Tenant isolation** - jobs scoped by tenant_id
✓ **Key management** endpoints at `/auth/*`
✓ **CLI tool** for bootstrapping admin key

---

## Future: Phase 2 (Multi-tenancy & Users)

This milestone establishes the foundation. Phase 2 would add:

- [ ] User accounts with login/signup
- [ ] Multiple tenants with separate billing
- [ ] User → Tenant membership
- [ ] Role-based access within tenants (owner, admin, member)
- [ ] Usage quotas per tenant
- [ ] Audit logging

The `tenant_id` field on jobs and API keys makes this transition seamless - no data migration required.

**Next**: Return to feature development or proceed to Phase 2 when needed.
