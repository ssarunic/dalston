"""Unit tests for DeliveryWorker auto-disable logic.

Tests the ElevenLabs-style auto-disable behavior:
- 10+ consecutive failures triggers auto-disable
- Recent success (within 7 days) prevents auto-disable
- Re-enabling clears disabled_reason
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.db.models import WebhookDeliveryModel, WebhookEndpointModel
from dalston.orchestrator.delivery import (
    AUTO_DISABLE_FAILURE_THRESHOLD,
    AUTO_DISABLE_SUCCESS_WINDOW_DAYS,
    DeliveryWorker,
    create_webhook_delivery,
)


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.webhook_secret = "test-secret"
    return settings


@pytest.fixture
def mock_session_factory():
    """Create mock session factory."""
    return MagicMock()


@pytest.fixture
def delivery_worker(mock_session_factory, mock_settings):
    """Create DeliveryWorker instance for testing."""
    return DeliveryWorker(mock_session_factory, mock_settings)


@pytest.fixture
def mock_endpoint():
    """Create a mock webhook endpoint."""
    return WebhookEndpointModel(
        id=uuid4(),
        tenant_id=uuid4(),
        url="https://example.com/webhook",
        events=["transcription.completed"],
        signing_secret="whsec_test123",
        is_active=True,
        consecutive_failures=0,
        last_success_at=None,
        disabled_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestAutoDisableThresholds:
    """Tests for auto-disable threshold constants."""

    def test_failure_threshold_is_10(self):
        """Test that auto-disable triggers after 10 consecutive failures."""
        assert AUTO_DISABLE_FAILURE_THRESHOLD == 10

    def test_success_window_is_7_days(self):
        """Test that success window is 7 days."""
        assert AUTO_DISABLE_SUCCESS_WINDOW_DAYS == 7


@pytest.mark.asyncio
class TestCheckAutoDisable:
    """Tests for _check_auto_disable method."""

    async def test_no_disable_below_threshold(self, delivery_worker, mock_endpoint):
        """Test that endpoint is not disabled when failures < threshold."""
        mock_endpoint.consecutive_failures = 9
        mock_endpoint.last_success_at = None

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should not have called execute to disable
        mock_db.execute.assert_not_called()
        assert mock_endpoint.is_active is True

    async def test_disable_at_threshold_no_success(
        self, delivery_worker, mock_endpoint
    ):
        """Test auto-disable triggers at exactly 10 failures with no success."""
        mock_endpoint.consecutive_failures = 10
        mock_endpoint.last_success_at = None

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should have called execute to disable
        mock_db.execute.assert_called_once()
        log.warning.assert_called_once()
        assert "webhook_endpoint_auto_disabled" in str(log.warning.call_args)

    async def test_disable_above_threshold_no_success(
        self, delivery_worker, mock_endpoint
    ):
        """Test auto-disable triggers above threshold with no success."""
        mock_endpoint.consecutive_failures = 15
        mock_endpoint.last_success_at = None

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should have called execute to disable
        mock_db.execute.assert_called_once()

    async def test_disable_with_old_success(self, delivery_worker, mock_endpoint):
        """Test auto-disable triggers when last success was > 7 days ago."""
        mock_endpoint.consecutive_failures = 10
        # Last success was 8 days ago
        mock_endpoint.last_success_at = datetime.now(UTC) - timedelta(days=8)

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should have called execute to disable
        mock_db.execute.assert_called_once()

    async def test_no_disable_with_recent_success(self, delivery_worker, mock_endpoint):
        """Test no auto-disable when last success is within 7 days."""
        mock_endpoint.consecutive_failures = 10
        # Last success was 6 days ago (within window)
        mock_endpoint.last_success_at = datetime.now(UTC) - timedelta(days=6)

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should NOT have called execute to disable
        mock_db.execute.assert_not_called()

    async def test_no_disable_success_exactly_7_days(
        self, delivery_worker, mock_endpoint
    ):
        """Test no auto-disable when last success is exactly at 7 day boundary."""
        mock_endpoint.consecutive_failures = 10
        # Last success was exactly 7 days ago (within window boundary)
        mock_endpoint.last_success_at = datetime.now(UTC) - timedelta(
            days=7,
            seconds=-1,  # Just under 7 days
        )

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should NOT have called execute to disable (still within window)
        mock_db.execute.assert_not_called()

    async def test_disable_success_just_past_7_days(
        self, delivery_worker, mock_endpoint
    ):
        """Test auto-disable when last success is just past 7 day window."""
        mock_endpoint.consecutive_failures = 10
        # Last success was 7 days + 1 second ago (outside window)
        mock_endpoint.last_success_at = datetime.now(UTC) - timedelta(days=7, seconds=1)

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should have called execute to disable
        mock_db.execute.assert_called_once()

    async def test_high_failures_with_recent_success_protected(
        self, delivery_worker, mock_endpoint
    ):
        """Test even 100+ failures don't disable if success is recent."""
        mock_endpoint.consecutive_failures = 100
        # Last success was 1 day ago
        mock_endpoint.last_success_at = datetime.now(UTC) - timedelta(days=1)

        mock_db = AsyncMock()
        log = MagicMock()

        await delivery_worker._check_auto_disable(mock_db, mock_endpoint, log)

        # Should NOT disable due to recent success
        mock_db.execute.assert_not_called()


@pytest.mark.asyncio
class TestReEnabling:
    """Tests for re-enabling webhook endpoints."""

    async def test_re_enable_clears_disabled_reason(self):
        """Test that re-enabling an endpoint clears the disabled_reason field."""
        from dalston.gateway.services.webhook_endpoints import WebhookEndpointService

        service = WebhookEndpointService()

        # Create a mock endpoint that was auto-disabled
        endpoint_id = uuid4()
        tenant_id = uuid4()
        mock_endpoint = WebhookEndpointModel(
            id=endpoint_id,
            tenant_id=tenant_id,
            url="https://example.com/webhook",
            events=["transcription.completed"],
            signing_secret="whsec_test123",
            is_active=False,
            consecutive_failures=10,
            last_success_at=None,
            disabled_reason="auto_disabled",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Mock the database
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_endpoint
        mock_db.execute.return_value = mock_result

        # Re-enable by setting is_active=True
        await service.update_endpoint(
            db=mock_db,
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            is_active=True,
        )

        # Verify the endpoint was re-enabled and disabled_reason cleared
        assert mock_endpoint.is_active is True
        assert mock_endpoint.disabled_reason is None
        # consecutive_failures should also be reset
        assert mock_endpoint.consecutive_failures == 0


@pytest.mark.asyncio
class TestCreateWebhookDelivery:
    """Tests for deduplicated webhook delivery creation."""

    async def test_returns_inserted_delivery(self):
        """Returns newly inserted delivery when no conflict occurs."""
        delivery_id = uuid4()
        inserted = MagicMock()
        inserted.scalar_one_or_none.return_value = delivery_id

        mock_delivery = WebhookDeliveryModel(
            id=delivery_id,
            endpoint_id=uuid4(),
            job_id=uuid4(),
            event_type="transcription.failed",
            payload={"type": "transcription.failed"},
            url_override=None,
            status="pending",
            attempts=0,
            next_retry_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=inserted)
        mock_db.get = AsyncMock(return_value=mock_delivery)

        result = await create_webhook_delivery(
            db=mock_db,
            endpoint_id=mock_delivery.endpoint_id,
            job_id=mock_delivery.job_id,
            event_type=mock_delivery.event_type,
            payload=mock_delivery.payload,
        )

        assert result == mock_delivery
        mock_db.get.assert_awaited_once_with(WebhookDeliveryModel, delivery_id)

    async def test_returns_existing_delivery_on_conflict(self):
        """Returns existing row when insert is deduplicated by unique constraints."""
        job_id = uuid4()
        endpoint_id = uuid4()

        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = None

        existing_delivery = WebhookDeliveryModel(
            id=uuid4(),
            endpoint_id=endpoint_id,
            job_id=job_id,
            event_type="transcription.failed",
            payload={"type": "transcription.failed"},
            url_override=None,
            status="pending",
            attempts=0,
            next_retry_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing_delivery

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[insert_result, select_result])
        mock_db.get = AsyncMock()

        result = await create_webhook_delivery(
            db=mock_db,
            endpoint_id=endpoint_id,
            job_id=job_id,
            event_type="transcription.failed",
            payload={"type": "transcription.failed"},
        )

        assert result == existing_delivery
        mock_db.execute.assert_awaited()
        mock_db.get.assert_not_called()

    async def test_returns_existing_per_job_url_delivery_on_conflict(self):
        """Dedupes per-job webhook URL deliveries when endpoint_id is None."""
        job_id = uuid4()

        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = None

        existing_delivery = WebhookDeliveryModel(
            id=uuid4(),
            endpoint_id=None,
            job_id=job_id,
            event_type="transcription.failed",
            payload={"type": "transcription.failed"},
            url_override="https://example.com/webhook",
            status="pending",
            attempts=0,
            next_retry_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = existing_delivery

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[insert_result, select_result])
        mock_db.get = AsyncMock()

        result = await create_webhook_delivery(
            db=mock_db,
            endpoint_id=None,
            job_id=job_id,
            event_type="transcription.failed",
            payload={"type": "transcription.failed"},
            url_override="https://example.com/webhook",
        )

        assert result == existing_delivery
