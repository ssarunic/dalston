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

**Storage in PostgreSQL** (per [ADR-004](../../decisions/ADR-004-api-key-storage-migration.md)):

API keys are stored in the `api_keys` table with:

- `key_hash` (indexed, unique) — for validation lookups
- `tenant_id` (indexed, FK) — for tenant scoping
- `scopes`, `rate_limit`, `expires_at`, `revoked_at` — for authorization

Session tokens and rate limits remain in Redis (ephemeral data with TTLs).

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

Dalston implements three types of rate limits per tenant:

| Limit Type | Algorithm | Redis Structure |
|------------|-----------|-----------------|
| Requests/minute | Sliding window | Sorted set with timestamps |
| Concurrent jobs | Counter | Simple string (INCR/DECR) |
| Concurrent sessions | Counter | Simple string (INCR/DECR) |

### Configuration

```bash
RATE_LIMIT_REQUESTS_PER_MINUTE=600   # Default: 600
RATE_LIMIT_CONCURRENT_JOBS=10        # Default: 10
RATE_LIMIT_CONCURRENT_SESSIONS=5     # Default: 5
```

### Request Rate Limiting (Sliding Window)

```python
async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult:
    """Sliding window rate limit using sorted sets."""
    key = f"dalston:ratelimit:requests:{tenant_id}"
    now = time.time()
    window_start = now - 60  # 1 minute window

    pipe = self._redis.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)  # Remove old entries
    pipe.zcard(key)                               # Count current
    pipe.zadd(key, {str(now): now})              # Add this request
    pipe.expire(key, 61)                          # TTL for cleanup
    results = await pipe.execute()

    current_count = results[1]
    allowed = current_count < self._requests_per_minute

    if not allowed:
        await self._redis.zrem(key, str(now))  # Remove if over limit

    return RateLimitResult(
        allowed=allowed,
        limit=self._requests_per_minute,
        remaining=max(0, self._requests_per_minute - current_count - 1),
        reset_seconds=60 if not allowed else None,
    )
```

### Concurrent Job/Session Limiting

```python
async def check_concurrent_jobs(self, tenant_id: UUID) -> RateLimitResult:
    """Check concurrent job limit using simple counter."""
    key = f"dalston:ratelimit:jobs:{tenant_id}"
    current = await self._redis.get(key)
    current_count = int(current) if current else 0

    return RateLimitResult(
        allowed=current_count < self._max_concurrent_jobs,
        limit=self._max_concurrent_jobs,
        remaining=max(0, self._max_concurrent_jobs - current_count),
    )

async def increment_concurrent_jobs(self, tenant_id: UUID) -> None:
    """Increment when job starts."""
    key = f"dalston:ratelimit:jobs:{tenant_id}"
    await self._redis.incr(key)

async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None:
    """Decrement when job completes/fails."""
    key = f"dalston:ratelimit:jobs:{tenant_id}"
    result = await self._redis.decr(key)
    if result < 0:
        await self._redis.set(key, 0)  # Prevent negative
```

### Redis Key Patterns

```
dalston:ratelimit:requests:{tenant_id}   # Sorted set (timestamps)
dalston:ratelimit:jobs:{tenant_id}       # String (counter)
dalston:ratelimit:sessions:{tenant_id}   # String (counter)
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
