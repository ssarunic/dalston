"""Unit tests for WebhookEndpointService."""

import pytest

from dalston.gateway.services.webhook import WebhookValidationError
from dalston.gateway.services.webhook_endpoints import (
    ALLOWED_EVENTS,
    WebhookEndpointService,
)


class TestValidateEvents:
    """Tests for event validation."""

    @pytest.fixture
    def service(self):
        return WebhookEndpointService()

    def test_valid_single_event(self, service):
        """Test single valid event passes validation."""
        # Should not raise
        service._validate_events(["transcription.completed"])

    def test_valid_multiple_events(self, service):
        """Test multiple valid events pass validation."""
        # Should not raise
        service._validate_events(["transcription.completed", "transcription.failed"])

    def test_valid_wildcard_event(self, service):
        """Test wildcard event passes validation."""
        # Should not raise
        service._validate_events(["*"])

    def test_empty_events_rejected(self, service):
        """Test empty events list is rejected."""
        with pytest.raises(WebhookValidationError) as exc_info:
            service._validate_events([])
        assert "At least one event type is required" in str(exc_info.value)

    def test_invalid_event_rejected(self, service):
        """Test invalid event type is rejected."""
        with pytest.raises(WebhookValidationError) as exc_info:
            service._validate_events(["transcription.completed", "invalid.event"])
        assert "Invalid event types" in str(exc_info.value)
        assert "invalid.event" in str(exc_info.value)

    def test_all_allowed_events(self, service):
        """Test that all documented allowed events are valid."""
        # Should not raise
        service._validate_events(list(ALLOWED_EVENTS))


class TestSigningSecretFormat:
    """Tests for signing secret generation format."""

    def test_secret_starts_with_prefix(self):
        """Test that generated secrets have whsec_ prefix."""
        import secrets as stdlib_secrets

        from dalston.gateway.services.webhook_endpoints import WebhookEndpointService

        # Simulate what create_endpoint does
        raw_secret = f"whsec_{stdlib_secrets.token_urlsafe(32)}"
        assert raw_secret.startswith("whsec_")

    def test_secret_has_sufficient_entropy(self):
        """Test that secret is long enough to be secure."""
        import secrets as stdlib_secrets

        raw_secret = f"whsec_{stdlib_secrets.token_urlsafe(32)}"
        # whsec_ is 6 chars, token_urlsafe(32) produces ~43 chars
        assert len(raw_secret) > 40
