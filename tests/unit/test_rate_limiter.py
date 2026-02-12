"""Tests for rate limiting service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.gateway.services.rate_limiter import (
    KEY_PREFIX_JOBS,
    KEY_PREFIX_SESSIONS,
    RedisRateLimiter,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    # Pipeline is created sync, methods are sync, but execute() is async
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    # pipeline() is a sync method in redis-py
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


@pytest.fixture
def rate_limiter(mock_redis):
    """Create a rate limiter with mock Redis."""
    return RedisRateLimiter(
        redis=mock_redis,
        requests_per_minute=100,
        max_concurrent_jobs=5,
        max_concurrent_sessions=3,
    )


class TestRequestRateLimit:
    """Tests for request rate limiting."""

    async def test_allows_request_under_limit(self, rate_limiter, mock_redis):
        """Should allow requests when under the limit."""
        tenant_id = uuid4()

        # Mock pipeline results: [zremrangebyscore, zcard, zadd, expire]
        # Pipeline methods are sync, only execute() is async
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[None, 50, True, True])
        mock_redis.pipeline.return_value = pipe

        result = await rate_limiter.check_request_rate(tenant_id)

        assert result.allowed is True
        assert result.limit == 100
        assert result.remaining == 49  # 100 - 50 - 1

    async def test_denies_request_over_limit(self, rate_limiter, mock_redis):
        """Should deny requests when at or over the limit."""
        tenant_id = uuid4()

        # Mock pipeline results: at limit (100 requests)
        # Pipeline methods are sync, only execute() is async
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[None, 100, True, True])
        mock_redis.pipeline.return_value = pipe

        result = await rate_limiter.check_request_rate(tenant_id)

        assert result.allowed is False
        assert result.limit == 100
        assert result.remaining == 0
        assert result.reset_seconds == 60

    async def test_removes_request_when_over_limit(self, rate_limiter, mock_redis):
        """Should remove the added request when over limit."""
        tenant_id = uuid4()

        # Pipeline methods are sync, only execute() is async
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[None, 100, True, True])
        mock_redis.pipeline.return_value = pipe

        await rate_limiter.check_request_rate(tenant_id)

        # Should have called zrem to remove the request
        mock_redis.zrem.assert_called_once()


class TestConcurrentJobsLimit:
    """Tests for concurrent jobs limiting."""

    async def test_allows_job_under_limit(self, rate_limiter, mock_redis):
        """Should allow jobs when under the limit."""
        tenant_id = uuid4()
        mock_redis.get.return_value = "3"

        result = await rate_limiter.check_concurrent_jobs(tenant_id)

        assert result.allowed is True
        assert result.limit == 5
        assert result.remaining == 2

    async def test_denies_job_at_limit(self, rate_limiter, mock_redis):
        """Should deny jobs when at the limit."""
        tenant_id = uuid4()
        mock_redis.get.return_value = "5"

        result = await rate_limiter.check_concurrent_jobs(tenant_id)

        assert result.allowed is False
        assert result.limit == 5
        assert result.remaining == 0

    async def test_allows_job_when_no_existing(self, rate_limiter, mock_redis):
        """Should allow jobs when no existing jobs (key doesn't exist)."""
        tenant_id = uuid4()
        mock_redis.get.return_value = None

        result = await rate_limiter.check_concurrent_jobs(tenant_id)

        assert result.allowed is True
        assert result.remaining == 5

    async def test_increment_concurrent_jobs(self, rate_limiter, mock_redis):
        """Should increment the concurrent job counter."""
        tenant_id = uuid4()

        await rate_limiter.increment_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        mock_redis.incr.assert_called_once_with(expected_key)

    async def test_decrement_concurrent_jobs(self, rate_limiter, mock_redis):
        """Should decrement the concurrent job counter."""
        tenant_id = uuid4()
        mock_redis.decr.return_value = 2

        await rate_limiter.decrement_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        mock_redis.decr.assert_called_once_with(expected_key)

    async def test_decrement_prevents_negative(self, rate_limiter, mock_redis):
        """Should reset to 0 if decrement would go negative."""
        tenant_id = uuid4()
        mock_redis.decr.return_value = -1

        await rate_limiter.decrement_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        mock_redis.set.assert_called_once_with(expected_key, 0)


class TestConcurrentSessionsLimit:
    """Tests for concurrent sessions limiting."""

    async def test_allows_session_under_limit(self, rate_limiter, mock_redis):
        """Should allow sessions when under the limit."""
        tenant_id = uuid4()
        mock_redis.get.return_value = "1"

        result = await rate_limiter.check_concurrent_sessions(tenant_id)

        assert result.allowed is True
        assert result.limit == 3
        assert result.remaining == 2

    async def test_denies_session_at_limit(self, rate_limiter, mock_redis):
        """Should deny sessions when at the limit."""
        tenant_id = uuid4()
        mock_redis.get.return_value = "3"

        result = await rate_limiter.check_concurrent_sessions(tenant_id)

        assert result.allowed is False
        assert result.limit == 3
        assert result.remaining == 0

    async def test_increment_concurrent_sessions(self, rate_limiter, mock_redis):
        """Should increment the concurrent session counter."""
        tenant_id = uuid4()

        await rate_limiter.increment_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        mock_redis.incr.assert_called_once_with(expected_key)

    async def test_decrement_concurrent_sessions(self, rate_limiter, mock_redis):
        """Should decrement the concurrent session counter."""
        tenant_id = uuid4()
        mock_redis.decr.return_value = 1

        await rate_limiter.decrement_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        mock_redis.decr.assert_called_once_with(expected_key)

    async def test_decrement_prevents_negative(self, rate_limiter, mock_redis):
        """Should reset to 0 if decrement would go negative."""
        tenant_id = uuid4()
        mock_redis.decr.return_value = -1

        await rate_limiter.decrement_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        mock_redis.set.assert_called_once_with(expected_key, 0)
