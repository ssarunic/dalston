# Authentication Patterns

## API Key Format and Storage

```python
KEY_PREFIX = "dk_"

def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its hash."""
    # 32 random bytes = 256 bits of entropy
    random_part = secrets.token_urlsafe(32)
    full_key = f"{KEY_PREFIX}{random_part}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_hash

def hash_api_key(key: str) -> str:
    """Hash an API key for lookup."""
    return hashlib.sha256(key.encode()).hexdigest()
```

**Storage in Redis:**

- `dalston:apikeys:{hash}` → Full API key JSON
- `dalston:apikeys:id:{id}` → Hash (for management lookups)
- `dalston:apikeys:tenant:{tenant_id}` → Set of key IDs

---

## Auth Middleware Pattern

```python
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

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
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = await auth_service.validate_api_key(token)

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    # Check rate limit
    if api_key.rate_limit:
        if not await check_rate_limit(auth_service.redis, api_key.id, api_key.rate_limit):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Attach to request for handlers
    request.state.api_key = api_key
    request.state.tenant_id = api_key.tenant_id

    return api_key
```

---

## Scope Enforcement Pattern

```python
def require_scope(scope: str):
    """Dependency factory that requires a specific scope."""
    async def checker(api_key: APIKey = Depends(require_auth)) -> APIKey:
        if "admin" in api_key.scopes:
            return api_key  # Admin bypasses all checks

        if scope not in api_key.scopes:
            raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
        return api_key
    return checker

# Usage in routes:
@router.post("/audio/transcriptions")
async def create_transcription(
    file: UploadFile,
    api_key: APIKey = Depends(require_scope("jobs:write")),
):
    job = await jobs_service.create_job(
        file=file,
        tenant_id=api_key.tenant_id,  # Always scope to tenant
    )
    return job
```

---

## Rate Limiting with Redis

```python
async def check_rate_limit(redis, key_id: str, limit: int) -> bool:
    """Sliding window rate limit using Redis INCR + EXPIRE."""
    key = f"dalston:ratelimit:{key_id}"

    # Atomic increment + expire
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)  # 1 minute window
    results = await pipe.execute()

    current_count = results[0]
    return current_count <= limit
```

---

## WebSocket Authentication

WebSocket connections cannot use headers, so authentication uses query parameters:

```python
@router.websocket("/audio/transcriptions/stream")
async def stream_transcription(
    websocket: WebSocket,
    api_key: str = Query(...),  # Required query param
    language: str = Query("auto"),
):
    # Validate BEFORE accepting connection
    auth_service: AuthService = websocket.app.state.auth_service
    key = await auth_service.validate_api_key(api_key)

    if not key:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    if "realtime" not in key.scopes and "admin" not in key.scopes:
        await websocket.close(code=4003, reason="Missing 'realtime' scope")
        return

    if key.rate_limit and not await check_rate_limit(...):
        await websocket.close(code=4029, reason="Rate limit exceeded")
        return

    # Only accept after validation passes
    await websocket.accept()

    # Create session scoped to tenant
    session = await session_router.acquire_worker(tenant_id=key.tenant_id, ...)
```

**WebSocket close codes:**

- `4001` - Invalid API key
- `4003` - Missing required scope
- `4029` - Rate limit exceeded

---

## Tenant Isolation Pattern

All data operations must be scoped by tenant:

```python
async def get_job(self, job_id: str, tenant_id: str) -> Job | None:
    """Get a job, verifying tenant ownership."""
    data = await self.redis.get(f"dalston:job:{job_id}")
    if not data:
        return None

    job = Job.model_validate_json(data)

    # Critical: verify ownership
    if job.tenant_id != tenant_id:
        return None  # Return None, not 403, to avoid leaking existence

    return job

async def list_jobs(self, tenant_id: str, limit: int = 20) -> list[Job]:
    """List jobs for a tenant only."""
    # Use tenant-scoped index
    job_ids = await self.redis.smembers(f"dalston:jobs:tenant:{tenant_id}")
    # ... fetch and return
```
