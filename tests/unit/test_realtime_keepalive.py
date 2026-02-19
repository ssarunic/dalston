"""Tests for realtime session keepalive behavior."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from dalston.gateway.api.v1.realtime import _keep_session_alive


class TestKeepSessionAlive:
    """Tests for _keep_session_alive helper."""

    @pytest.mark.asyncio
    async def test_extends_ttl_periodically(self):
        """Keepalive calls extend_session_ttl at specified interval."""
        mock_router = AsyncMock()
        session_id = "sess_test123"

        task = asyncio.create_task(
            _keep_session_alive(mock_router, session_id, interval=0.1)
        )

        await asyncio.sleep(0.35)  # Allow 3 extensions
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert mock_router.extend_session_ttl.call_count >= 3
        mock_router.extend_session_ttl.assert_called_with(session_id)

    @pytest.mark.asyncio
    async def test_continues_on_error(self):
        """Keepalive continues running even if extend fails."""
        mock_router = AsyncMock()
        mock_router.extend_session_ttl.side_effect = Exception("Redis error")
        session_id = "sess_test123"

        task = asyncio.create_task(
            _keep_session_alive(mock_router, session_id, interval=0.1)
        )

        await asyncio.sleep(0.25)  # Allow 2 attempts
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have attempted multiple times despite errors
        assert mock_router.extend_session_ttl.call_count >= 2

    @pytest.mark.asyncio
    async def test_cancellation(self):
        """Keepalive task cancels cleanly."""
        mock_router = AsyncMock()
        session_id = "sess_test123"

        task = asyncio.create_task(
            _keep_session_alive(mock_router, session_id, interval=10)
        )

        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_sleeps_before_first_extend(self):
        """Keepalive sleeps before the first TTL extension."""
        mock_router = AsyncMock()
        session_id = "sess_test123"

        task = asyncio.create_task(
            _keep_session_alive(mock_router, session_id, interval=0.2)
        )

        # Check immediately - should not have called extend yet
        await asyncio.sleep(0.05)
        assert mock_router.extend_session_ttl.call_count == 0

        # Wait past the interval
        await asyncio.sleep(0.2)
        assert mock_router.extend_session_ttl.call_count >= 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
