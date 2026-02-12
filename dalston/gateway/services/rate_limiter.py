"""Rate limiting service for API requests and concurrent operations.

Provides three types of rate limiting:
1. Requests per minute (sliding window)
2. Concurrent batch jobs
3. Concurrent realtime sessions
"""

from abc import ABC, abstractmethod
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


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int | None = None


class RateLimiterProtocol(Protocol):
    """Protocol for rate limiter implementations."""

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


class RateLimiter(ABC):
    """Abstract base class for rate limiters."""

    @abstractmethod
    async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant is within request rate limit."""
        pass

    @abstractmethod
    async def check_concurrent_jobs(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new batch job."""
        pass

    @abstractmethod
    async def increment_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Increment concurrent job count for tenant."""
        pass

    @abstractmethod
    async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Decrement concurrent job count for tenant."""
        pass

    @abstractmethod
    async def check_concurrent_sessions(self, tenant_id: UUID) -> RateLimitResult:
        """Check if tenant can start a new realtime session."""
        pass

    @abstractmethod
    async def increment_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Increment concurrent session count for tenant."""
        pass

    @abstractmethod
    async def decrement_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Decrement concurrent session count for tenant."""
        pass


class RedisRateLimiter(RateLimiter):
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

    async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult:
        """Check request rate using sliding window counter.

        Uses Redis sorted set with timestamps as scores for sliding window.
        """
        import time

        key = f"{KEY_PREFIX_REQUESTS}:{tenant_id}"
        now = time.time()
        window_start = now - self._window_seconds

        # Use pipeline for atomic operations
        pipe = self._redis.pipeline()
        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start)
        # Count requests in current window
        pipe.zcard(key)
        # Add current request (will be rolled back if over limit)
        pipe.zadd(key, {str(now): now})
        # Set expiry on the key
        pipe.expire(key, self._window_seconds + 1)

        results = await pipe.execute()
        current_count = results[1]

        if current_count >= self._requests_per_minute:
            # Over limit - remove the request we just added
            await self._redis.zrem(key, str(now))
            return RateLimitResult(
                allowed=False,
                limit=self._requests_per_minute,
                remaining=0,
                reset_seconds=self._window_seconds,
            )

        remaining = self._requests_per_minute - current_count - 1
        return RateLimitResult(
            allowed=True,
            limit=self._requests_per_minute,
            remaining=max(0, remaining),
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
        """Increment concurrent job count for tenant."""
        key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        await self._redis.incr(key)
        logger.debug("incremented_concurrent_jobs", tenant_id=str(tenant_id))

    async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None:
        """Decrement concurrent job count for tenant."""
        key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        result = await self._redis.decr(key)
        # Ensure we don't go negative
        if result < 0:
            await self._redis.set(key, 0)
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
        """Increment concurrent session count for tenant."""
        key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        await self._redis.incr(key)
        logger.debug("incremented_concurrent_sessions", tenant_id=str(tenant_id))

    async def decrement_concurrent_sessions(self, tenant_id: UUID) -> None:
        """Decrement concurrent session count for tenant."""
        key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        result = await self._redis.decr(key)
        # Ensure we don't go negative
        if result < 0:
            await self._redis.set(key, 0)
        logger.debug("decremented_concurrent_sessions", tenant_id=str(tenant_id))
