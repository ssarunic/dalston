"""End-to-end tests for concurrent job counter leak fix.

Tests the fix for Finding 7: concurrent job counter leak on immediate cancellation.
These tests verify that the counter is correctly decremented in all terminal paths.
"""

import asyncio
from uuid import uuid4

import pytest

from dalston.gateway.services.rate_limiter import (
    CONCURRENT_COUNTER_TTL_SECONDS,
    KEY_PREFIX_JOB_DECREMENTED,
    KEY_PREFIX_JOBS,
    RedisRateLimiter,
)

# Skip all tests if redis is not available
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
async def redis_client():
    """Create a real Redis client for testing."""
    try:
        from redis.asyncio import Redis

        client = Redis.from_url("redis://localhost:6379", decode_responses=True)
        # Test connection
        await client.ping()
        yield client
        await client.aclose()
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")


@pytest.fixture
async def rate_limiter(redis_client):
    """Create a rate limiter with real Redis."""
    return RedisRateLimiter(
        redis=redis_client,
        requests_per_minute=100,
        max_concurrent_jobs=10,
        max_concurrent_sessions=5,
    )


@pytest.fixture
async def clean_keys(redis_client):
    """Clean up test keys after each test."""
    keys_to_clean = []
    yield keys_to_clean
    for key in keys_to_clean:
        await redis_client.delete(key)


class TestConcurrentJobCounterE2E:
    """End-to-end tests for concurrent job counter behavior."""

    async def test_immediate_cancel_returns_counter_to_original(
        self, redis_client, rate_limiter, clean_keys
    ):
        """
        Test Finding 7 fix: Submit → immediate cancel → counter returns to original.

        This is the core bug scenario: when a job is cancelled before any tasks
        start running, the counter should still be decremented.
        """
        tenant_id = uuid4()
        job_id = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        clean_keys.extend([counter_key, guard_key])

        # Get initial counter value
        initial = await redis_client.get(counter_key)
        initial_count = int(initial) if initial else 0

        # Simulate job submission (increment)
        await rate_limiter.increment_concurrent_jobs(tenant_id)

        # Verify counter incremented
        after_submit = await redis_client.get(counter_key)
        assert int(after_submit) == initial_count + 1

        # Simulate immediate cancellation (decrement via idempotent helper)
        result = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        assert result is True
        after_cancel = await redis_client.get(counter_key)
        assert int(after_cancel) == initial_count

    async def test_replaying_decrement_does_not_double_decrement(
        self, redis_client, rate_limiter, clean_keys
    ):
        """
        Test that replaying cancellation/completion handlers does not double-decrement.

        This tests the idempotency: calling decrement_concurrent_jobs_once multiple
        times for the same job should only decrement once.
        """
        tenant_id = uuid4()
        job_id = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        clean_keys.extend([counter_key, guard_key])

        # Set initial counter to 5
        await redis_client.set(counter_key, 5, ex=CONCURRENT_COUNTER_TTL_SECONDS)

        # First decrement should succeed
        result1 = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)
        assert result1 is True
        count_after_first = await redis_client.get(counter_key)
        assert int(count_after_first) == 4

        # Second decrement (replay) should be skipped
        result2 = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)
        assert result2 is False
        count_after_second = await redis_client.get(counter_key)
        assert int(count_after_second) == 4  # Still 4, not 3

        # Third decrement (another replay) should also be skipped
        result3 = await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)
        assert result3 is False
        count_after_third = await redis_client.get(counter_key)
        assert int(count_after_third) == 4  # Still 4

    async def test_different_jobs_decrement_independently(
        self, redis_client, rate_limiter, clean_keys
    ):
        """Test that different jobs can each decrement the counter."""
        tenant_id = uuid4()
        job_id_1 = uuid4()
        job_id_2 = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key_1 = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id_1}"
        guard_key_2 = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id_2}"
        clean_keys.extend([counter_key, guard_key_1, guard_key_2])

        # Set initial counter to 5
        await redis_client.set(counter_key, 5, ex=CONCURRENT_COUNTER_TTL_SECONDS)

        # First job decrements
        result1 = await rate_limiter.decrement_concurrent_jobs_once(job_id_1, tenant_id)
        assert result1 is True
        assert int(await redis_client.get(counter_key)) == 4

        # Second job also decrements
        result2 = await rate_limiter.decrement_concurrent_jobs_once(job_id_2, tenant_id)
        assert result2 is True
        assert int(await redis_client.get(counter_key)) == 3

        # Replaying either job should not decrement further
        assert (
            await rate_limiter.decrement_concurrent_jobs_once(job_id_1, tenant_id)
            is False
        )
        assert (
            await rate_limiter.decrement_concurrent_jobs_once(job_id_2, tenant_id)
            is False
        )
        assert int(await redis_client.get(counter_key)) == 3

    async def test_concurrent_decrements_only_one_succeeds(
        self, redis_client, rate_limiter, clean_keys
    ):
        """
        Test race condition: concurrent decrements for the same job.

        Only one should succeed due to SET NX atomicity.
        """
        tenant_id = uuid4()
        job_id = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        clean_keys.extend([counter_key, guard_key])

        # Set initial counter to 10
        await redis_client.set(counter_key, 10, ex=CONCURRENT_COUNTER_TTL_SECONDS)

        # Run 5 concurrent decrement attempts for the same job
        results = await asyncio.gather(
            rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id),
            rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id),
            rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id),
            rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id),
            rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id),
        )

        # Exactly one should have succeeded
        assert sum(results) == 1

        # Counter should be 9 (decremented exactly once)
        final_count = await redis_client.get(counter_key)
        assert int(final_count) == 9

    async def test_guard_key_has_ttl(self, redis_client, rate_limiter, clean_keys):
        """Test that the guard key has a TTL to prevent infinite accumulation."""
        tenant_id = uuid4()
        job_id = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        clean_keys.extend([counter_key, guard_key])

        await redis_client.set(counter_key, 5, ex=CONCURRENT_COUNTER_TTL_SECONDS)

        await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        # Guard key should exist with a TTL
        ttl = await redis_client.ttl(guard_key)
        assert ttl > 0
        assert ttl <= CONCURRENT_COUNTER_TTL_SECONDS

    async def test_counter_does_not_go_negative(
        self, redis_client, rate_limiter, clean_keys
    ):
        """Test that counter is reset to 0 if it would go negative."""
        tenant_id = uuid4()
        job_id = uuid4()
        counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
        guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"
        clean_keys.extend([counter_key, guard_key])

        # Set counter to 0
        await redis_client.set(counter_key, 0, ex=CONCURRENT_COUNTER_TTL_SECONDS)

        # Decrement should not go negative
        await rate_limiter.decrement_concurrent_jobs_once(job_id, tenant_id)

        final_count = await redis_client.get(counter_key)
        assert int(final_count) == 0  # Reset to 0, not -1
