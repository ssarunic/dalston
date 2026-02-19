"""Tests for rate limiting service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.gateway.services.rate_limiter import (
    CONCURRENT_COUNTER_TTL_SECONDS,
    KEY_PREFIX_JOB_DECREMENTED,
    KEY_PREFIX_JOBS,
    KEY_PREFIX_SESSIONS,
    RedisRateLimiter,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()

    # Mock register_script to return a callable that returns an async result
    mock_script = AsyncMock()
    redis.register_script = MagicMock(return_value=mock_script)

    # Pipeline is created sync, methods are sync, but execute() is async
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.decr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
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

        # Mock Lua script result: [allowed=1, current_count=51, remaining=49]
        mock_script = mock_redis.register_script.return_value
        mock_script.return_value = [1, 51, 49]

        result = await rate_limiter.check_request_rate(tenant_id)

        assert result.allowed is True
        assert result.limit == 100
        assert result.remaining == 49

    async def test_denies_request_over_limit(self, rate_limiter, mock_redis):
        """Should deny requests when at or over the limit."""
        tenant_id = uuid4()

        # Mock Lua script result: [allowed=0, current_count=100, remaining=0]
        mock_script = mock_redis.register_script.return_value
        mock_script.return_value = [0, 100, 0]

        result = await rate_limiter.check_request_rate(tenant_id)

        assert result.allowed is False
        assert result.limit == 100
        assert result.remaining == 0
        assert result.reset_seconds == 60

    async def test_lua_script_called_with_correct_args(self, rate_limiter, mock_redis):
        """Should call Lua script with correct keys and args."""
        tenant_id = uuid4()

        mock_script = mock_redis.register_script.return_value
        mock_script.return_value = [1, 1, 99]

        await rate_limiter.check_request_rate(tenant_id)

        # Verify script was called with correct key
        mock_script.assert_called_once()
        call_kwargs = mock_script.call_args.kwargs
        assert f"dalston:ratelimit:requests:{tenant_id}" in call_kwargs["keys"]
        # Args should include: now, window_start, limit, window_seconds
        assert len(call_kwargs["args"]) == 4
        assert call_kwargs["args"][2] == 100  # requests_per_minute
        assert call_kwargs["args"][3] == 60  # window_seconds


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
        """Should increment the concurrent job counter with TTL."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1, True]

        await rate_limiter.increment_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        pipe.incr.assert_called_once_with(expected_key)
        pipe.expire.assert_called_once_with(
            expected_key, CONCURRENT_COUNTER_TTL_SECONDS
        )
        pipe.execute.assert_called_once()

    async def test_decrement_concurrent_jobs(self, rate_limiter, mock_redis):
        """Should decrement the concurrent job counter with TTL."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [2, True]  # decr result, expire result

        await rate_limiter.decrement_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        pipe.decr.assert_called_once_with(expected_key)
        pipe.expire.assert_called_once_with(
            expected_key, CONCURRENT_COUNTER_TTL_SECONDS
        )

    async def test_decrement_prevents_negative(self, rate_limiter, mock_redis):
        """Should reset to 0 if decrement would go negative."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [-1, True]  # decr result went negative

        await rate_limiter.decrement_concurrent_jobs(tenant_id)

        expected_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        mock_redis.set.assert_called_once_with(
            expected_key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS
        )

    async def test_decrement_once_first_call_decrements(self, rate_limiter, mock_redis):
        """First call should decrement and return True."""
        job_id = uuid4()
        tenant_id = uuid4()

        # SET NX succeeds (key didn't exist)
        mock_redis.set.return_value = True
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [2, True]  # decr result, expire result

        result = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        assert result is True
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        mock_redis.set.assert_called_once_with(
            guard_key, "1", nx=True, ex=CONCURRENT_COUNTER_TTL_SECONDS
        )
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        pipe.decr.assert_called_once_with(counter_key)
        pipe.expire.assert_called_once_with(counter_key, CONCURRENT_COUNTER_TTL_SECONDS)

    async def test_decrement_once_second_call_skipped(self, rate_limiter, mock_redis):
        """Second call for same job should skip decrement and return False."""
        job_id = uuid4()
        tenant_id = uuid4()

        # SET NX fails (key already exists)
        mock_redis.set.return_value = False

        result = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        assert result is False
        # Pipeline should NOT have been created for decrement
        mock_redis.pipeline.assert_not_called()

    async def test_decrement_once_prevents_negative(self, rate_limiter, mock_redis):
        """Should reset to 0 if idempotent decrement would go negative."""
        job_id = uuid4()
        tenant_id = uuid4()

        # SET NX succeeds, but pipeline decr goes negative
        mock_redis.set.return_value = True
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [-1, True]  # decr result went negative

        await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        # Should reset to 0 (second set call after the guard key set)
        assert mock_redis.set.call_count == 2  # guard key + reset to 0
        mock_redis.set.assert_any_call(
            counter_key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS
        )

    async def test_decrement_once_different_jobs_both_decrement(
        self, rate_limiter, mock_redis
    ):
        """Different job IDs should both successfully decrement."""
        job_id_1 = uuid4()
        job_id_2 = uuid4()
        tenant_id = uuid4()

        # Both SET NX calls succeed
        mock_redis.set.return_value = True
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1, True]

        result1 = await rate_limiter.decrement_concurrent_jobs_once(job_id_1, tenant_id)
        result2 = await rate_limiter.decrement_concurrent_jobs_once(job_id_2, tenant_id)

        assert result1 is True
        assert result2 is True
        # Two calls to pipeline for decrement
        assert mock_redis.pipeline.call_count == 2


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
        """Should increment the concurrent session counter with TTL."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1, True]

        await rate_limiter.increment_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        pipe.incr.assert_called_once_with(expected_key)
        pipe.expire.assert_called_once_with(
            expected_key, CONCURRENT_COUNTER_TTL_SECONDS
        )
        pipe.execute.assert_called_once()

    async def test_decrement_concurrent_sessions(self, rate_limiter, mock_redis):
        """Should decrement the concurrent session counter with TTL."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1, True]

        await rate_limiter.decrement_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        pipe.decr.assert_called_once_with(expected_key)
        pipe.expire.assert_called_once_with(
            expected_key, CONCURRENT_COUNTER_TTL_SECONDS
        )

    async def test_decrement_prevents_negative(self, rate_limiter, mock_redis):
        """Should reset to 0 if decrement would go negative."""
        tenant_id = uuid4()
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [-1, True]

        await rate_limiter.decrement_concurrent_sessions(tenant_id)

        expected_key = f"{KEY_PREFIX_SESSIONS}:{tenant_id}"
        mock_redis.set.assert_called_once_with(
            expected_key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS
        )
