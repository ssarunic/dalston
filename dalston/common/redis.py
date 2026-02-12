"""Redis client management with DI-friendly provider pattern.

Supports dependency injection for testability and future cloud provider support
(ElastiCache, Memorystore, Azure Cache for Redis).
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from redis.asyncio.client import Pipeline, PubSub

from dalston.config import Settings, get_settings


@runtime_checkable
class RedisProtocol(Protocol):
    """Protocol defining the Redis client interface.

    This protocol captures the subset of Redis operations used by Dalston.
    Implementations can be standard Redis, ElastiCache, Memorystore, or mocks.
    """

    # Key-value operations
    async def get(self, name: str) -> str | None: ...
    async def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None: ...
    async def delete(self, *names: str) -> int: ...
    async def incr(self, name: str, amount: int = 1) -> int: ...
    async def decr(self, name: str, amount: int = 1) -> int: ...
    async def expire(self, name: str, time: int) -> bool: ...

    # Sorted sets (rate limiting)
    async def zadd(
        self, name: str, mapping: dict[str, float], nx: bool = False, xx: bool = False
    ) -> int: ...
    async def zrem(self, name: str, *values: str) -> int: ...
    async def zcard(self, name: str) -> int: ...
    async def zremrangebyscore(self, name: str, min: float, max: float) -> int: ...

    # Lists (queues)
    async def lpush(self, name: str, *values: str) -> int: ...
    async def rpush(self, name: str, *values: str) -> int: ...
    async def brpop(
        self, keys: list[str], timeout: int = 0
    ) -> tuple[str, str] | None: ...
    async def llen(self, name: str) -> int: ...
    async def lrem(self, name: str, count: int, value: str) -> int: ...
    async def lrange(self, name: str, start: int, end: int) -> list[str]: ...

    # Pub/Sub
    async def publish(self, channel: str, message: str) -> int: ...
    def pubsub(self) -> PubSub: ...

    # Pipeline
    def pipeline(self, transaction: bool = True) -> Pipeline: ...

    # Lifecycle
    async def close(self) -> None: ...


class RedisProvider(ABC):
    """Abstract base class for Redis providers.

    Providers manage Redis client lifecycle and configuration.
    Subclass this to support different Redis backends.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Redis | None = None
        self._lock = asyncio.Lock()

    @abstractmethod
    def _create_client(self) -> Redis:
        """Create and return a Redis client instance.

        Subclasses implement this to configure client for their backend.
        """
        pass

    async def get_client(self) -> Redis:
        """Get or create the Redis client.

        Uses double-checked locking to prevent race conditions.
        """
        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is None:
                self._client = self._create_client()
        return self._client

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    def reset(self) -> None:
        """Reset provider state (for testing).

        Note: This does NOT close the connection. Call close() first if needed.
        """
        self._client = None


class LocalRedisProvider(RedisProvider):
    """Redis provider for local/standard Redis connections.

    Uses redis:// URL from settings. Suitable for:
    - Local development
    - Self-hosted Redis
    - Docker Compose Redis
    """

    def _create_client(self) -> Redis:
        return redis_from_url(
            self._settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )


# Global provider instance for backward compatibility
_provider: RedisProvider | None = None
_provider_lock = asyncio.Lock()


async def get_provider(settings: Settings | None = None) -> RedisProvider:
    """Get the global Redis provider instance.

    Args:
        settings: Optional settings. If not provided, uses get_settings().

    Returns:
        The global RedisProvider instance.
    """
    global _provider
    if _provider is not None:
        return _provider

    async with _provider_lock:
        if _provider is None:
            _provider = LocalRedisProvider(settings or get_settings())
    return _provider


def set_provider(provider: RedisProvider) -> None:
    """Set the global Redis provider (for testing or custom providers).

    Args:
        provider: The provider instance to use globally.
    """
    global _provider
    _provider = provider


async def reset_provider() -> None:
    """Reset the global provider (for testing).

    Closes any existing connection and clears the provider.
    """
    global _provider
    if _provider is not None:
        await _provider.close()
        _provider = None


# Backward-compatible functions (delegate to provider)
async def get_redis() -> Redis:
    """Get Redis client (backward-compatible).

    Prefer using get_provider().get_client() for new code.
    """
    provider = await get_provider()
    return await provider.get_client()


async def close_redis() -> None:
    """Close Redis connection (backward-compatible).

    Prefer using reset_provider() for new code.
    """
    await reset_provider()
