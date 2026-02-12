"""Rate limiting service for API requests and concurrent operations.

Provides three types of rate limiting:
1. Requests per minute (sliding window)
2. Concurrent batch jobs
3. Concurrent realtime sessions
"""

import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger()

# Redis key prefixes
KEY_PREFIX_REQUESTS = "dalston:ratelimit:requests"
KEY_PREFIX_JOBS = "dalston:ratelimit:jobs"
KEY_PREFIX_SESSIONS = "dalston:ratelimit:sessions"

# TTL for concurrent counters (24 hours) - prevents zombie counters from crashed processes
CONCURRENT_COUNTER_TTL_SECONDS = 86400

# Lua script for atomic sliding window rate limiting
# Returns: [allowed (0/1), current_count, remaining]
SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local window_seconds = tonumber(ARGV[4])

-- Remove old entries outside the window
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

-- Count current requests in window
local current_count = redis.call('ZCARD', key)

-- Check if under limit
if current_count < limit then
    -- Add new request
    redis.call('ZADD', key, now, tostring(now))
    -- Set expiry
    redis.call('EXPIRE', key, window_seconds + 1)
    return {1, current_count + 1, limit - current_count - 1}
else
    return {0, current_count, 0}
end
"""


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int | None = None


class RateLimiter(Protocol):
    """Protocol for rate limiter implementations.

    Use this for type hints to allow dependency injection and testing.
    """

    async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant is within request rate limit."""
        ...

    async def check_concurrent_jobs(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new batch job."""
        ...

    async def increment_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Increment concurrent job count for tenant."""
        ...

    async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Decrement concurrent job count for tenant."""
        ...

    async def check_concurrent_sessions(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new realtime session."""
        ...

    async def increment_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Increment concurrent session count for tenant."""
        ...

    async def decrement_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Decrement concurrent session count for tenant."""
        ...


class RedisRateLimiter:
    """Redis-backed rate limiter implementation.

    Uses sliding window for request rate limiting and simple counters
    for concurrent job/session tracking.
    """

    def __init__(
        self,
        redis: Redis,
        requests_per_minute: int = 600,
        max_concurrent_jobs: int = 10,
        max_concurrent_sessions: int = 5,
    ) -> None:
        self._redis = redis
        self._requests_per_minute = requests_per_minute
        self._max_concurrent_jobs = max_concurrent_jobs
        self._max_concurrent_sessions = max_concurrent_sessions
        self._window_seconds = 60
        self._sliding_window_script = self._redis.register_script(SLIDING_WINDOW_SCRIPT)

    async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult:
        """Check request rate using atomic sliding window counter.

        Uses Lua script for atomic check-and-increment to prevent race conditions.
        """
        key = f"{KEY_PREFIX_REQUESTS}:{tenant_id}"
        now = time.time()
        window_start = now - self._window_seconds

        # Execute atomic Lua script
        result = await self._sliding_window_script(
            keys=[key],
            args=[now, window_start, self._requests_per_minute, self._window_seconds],
        )

        allowed = bool(result[0])
        remaining = int(result[2])

        return RateLimitResult(
            allowed=allowed,
            limit=self._requests_per_minute,
            remaining=remaining,
            reset_seconds=self._window_seconds,
        )

    async def check_concurrent_jobs(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new batch job."""
        key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        current = await self._redis.get(key)
        current_count = int(current) if current else 0

        allowed = current_count < self._max_concurrent_jobs
        return RateLimitResult(
            allowed=allowed,
            limit=self._max_concurrent_jobs,
            remaining=max(0, self._max_concurrent_jobs - current_count),
        )

    async def increment_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Increment concurrent job count for tenant.

        Sets TTL to prevent zombie counters from crashed processes.
        """
        key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, CONCURRENT_COUNTER_TTL_SECONDS)
        await pipe.execute()
        logger.debug("incremented_concurrent_jobs", tenant_id=str(tenant_id))

    async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Decrement concurrent job count for tenant.

        Refreshes TTL on decrement to keep active counters alive.
        """
        key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        pipe = self._redis.pipeline()
        pipe.decr(key)
        pipe.expire(key, CONCURRENT_COUNTER_TTL_SECONDS)
        results = await pipe.execute()
        # Ensure we don't go negative
        if results[0] < 0:
            await self._redis.set(key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS)
        logger.debug("decremented_concurrent_jobs", tenant_id=str(tenant_id))

    async def check_concurrent_sessions(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new realtime session."""
        key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        current = await self._redis.get(key)
        current_count = int(current) if current else 0

        allowed = current_count < self._max_concurrent_sessions
        return RateLimitResult(
            allowed=allowed,
            limit=self._max_concurrent_sessions,
            remaining=max(0, self._max_concurrent_sessions - current_count),
        )

    async def increment_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Increment concurrent session count for tenant.

        Sets TTL to prevent zombie counters from crashed processes.
        """
        key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, CONCURRENT_COUNTER_TTL_SECONDS)
        await pipe.execute()
        logger.debug("incremented_concurrent_sessions", tenant_id=str(tenant_id))

    async def decrement_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Decrement concurrent session count for tenant.

        Refreshes TTL on decrement to keep active counters alive.
        """
        key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        pipe = self._redis.pipeline()
        pipe.decr(key)
        pipe.expire(key, CONCURRENT_COUNTER_TTL_SECONDS)
        results = await pipe.execute()
        # Ensure we don't go negative
        if results[0] < 0:
            await self._redis.set(key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS)
        logger.debug("decremented_concurrent_sessions", tenant_id=str(tenant_id))
