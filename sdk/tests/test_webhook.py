"""Tests for webhook verification (Standard Webhooks format)."""

import base64
import hashlib
import hmac
import json
import time

import pytest

from dalston_sdk import (
    WebhookEventType,
    WebhookPayload,
    WebhookVerificationError,
    parse_webhook_payload,
    verify_webhook_signature,
)


class TestVerifyWebhookSignature:
    """Tests for verify_webhook_signature function (Standard Webhooks)."""

    def test_valid_signature(self):
        """Test verification with valid Standard Webhooks signature."""
        secret = "test-secret"
        msg_id = "msg_test123"
        timestamp = str(int(time.time()))
        payload = b'{"type": "transcription.completed", "data": {}}'

        # Compute valid signature per Standard Webhooks: "{msg_id}.{timestamp}.{body}"
        signed_payload = f"{msg_id}.{timestamp}.{payload.decode('utf-8')}"
        sig_bytes = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).digest()
        signature = f"v1,{base64.b64encode(sig_bytes).decode()}"

        result = verify_webhook_signature(payload, signature, msg_id, timestamp, secret)

        assert result is True

    def test_invalid_signature(self):
        """Test verification with invalid signature."""
        secret = "test-secret"
        msg_id = "msg_test123"
        timestamp = str(int(time.time()))
        payload = b'{"type": "transcription.completed"}'
        signature = "v1,aW52YWxpZA=="  # base64 of "invalid"

        result = verify_webhook_signature(payload, signature, msg_id, timestamp, secret)

        assert result is False

    def test_stale_timestamp(self):
        """Test verification with stale timestamp."""
        secret = "test-secret"
        msg_id = "msg_test123"
        timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        payload = b'{"type": "transcription.completed"}'

        signed_payload = f"{msg_id}.{timestamp}.{payload.decode('utf-8')}"
        sig_bytes = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).digest()
        signature = f"v1,{base64.b64encode(sig_bytes).decode()}"

        with pytest.raises(WebhookVerificationError, match="Timestamp too old"):
            verify_webhook_signature(payload, signature, msg_id, timestamp, secret)

    def test_invalid_timestamp_format(self):
        """Test verification with invalid timestamp format."""
        with pytest.raises(WebhookVerificationError, match="Invalid timestamp"):
            verify_webhook_signature(
                b"payload", "v1,abc", "msg_123", "not-a-number", "secret"
            )

    def test_invalid_signature_format(self):
        """Test verification with invalid signature format (not v1,)."""
        timestamp = str(int(time.time()))

        with pytest.raises(WebhookVerificationError, match="Invalid signature format"):
            verify_webhook_signature(
                b"payload", "sha256=abc", "msg_123", timestamp, "secret"
            )

    def test_custom_max_age(self):
        """Test verification with custom max_age."""
        secret = "test-secret"
        msg_id = "msg_test123"
        timestamp = str(int(time.time()) - 60)  # 1 minute ago
        payload = b'{"type": "transcription.completed"}'

        signed_payload = f"{msg_id}.{timestamp}.{payload.decode('utf-8')}"
        sig_bytes = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).digest()
        signature = f"v1,{base64.b64encode(sig_bytes).decode()}"

        # Should pass with 120 second max_age
        result = verify_webhook_signature(
            payload, signature, msg_id, timestamp, secret, max_age=120
        )
        assert result is True

        # Should fail with 30 second max_age
        with pytest.raises(WebhookVerificationError, match="Timestamp too old"):
            verify_webhook_signature(
                payload, signature, msg_id, timestamp, secret, max_age=30
            )


class TestParseWebhookPayload:
    """Tests for parse_webhook_payload function (Standard Webhooks format)."""

    def test_parse_completed_event(self):
        """Test parsing transcription.completed event."""
        payload = json.dumps(
            {
                "object": "event",
                "id": "evt_abc123",
                "type": "transcription.completed",
                "created_at": 1704067200,
                "data": {
                    "transcription_id": "550e8400-e29b-41d4-a716-446655440000",
                    "status": "completed",
                    "duration": 10.5,
                    "webhook_metadata": {"user_id": "123"},
                },
            }
        )

        result = parse_webhook_payload(payload)

        assert isinstance(result, WebhookPayload)
        assert result.object == "event"
        assert result.id == "evt_abc123"
        assert result.type == WebhookEventType.TRANSCRIPTION_COMPLETED
        assert result.created_at == 1704067200
        assert result.transcription_id == "550e8400-e29b-41d4-a716-446655440000"
        assert result.data["duration"] == 10.5
        assert result.webhook_metadata == {"user_id": "123"}

    def test_parse_failed_event(self):
        """Test parsing transcription.failed event."""
        payload = json.dumps(
            {
                "object": "event",
                "id": "evt_xyz789",
                "type": "transcription.failed",
                "created_at": 1704067200,
                "data": {
                    "transcription_id": "550e8400-e29b-41d4-a716-446655440000",
                    "status": "failed",
                    "error": "Processing failed",
                },
            }
        )

        result = parse_webhook_payload(payload)

        assert result.type == WebhookEventType.TRANSCRIPTION_FAILED
        assert result.data["error"] == "Processing failed"

    def test_parse_bytes_payload(self):
        """Test parsing bytes payload."""
        payload = b'{"object": "event", "id": "evt_123", "type": "transcription.completed", "created_at": 1704067200, "data": {"transcription_id": "550e8400-e29b-41d4-a716-446655440000", "status": "completed"}}'

        result = parse_webhook_payload(payload)

        assert result.type == WebhookEventType.TRANSCRIPTION_COMPLETED

    def test_missing_required_field(self):
        """Test parsing with missing required field."""
        payload = json.dumps(
            {
                "object": "event",
                "type": "transcription.completed",
                # Missing id, created_at, data
            }
        )

        with pytest.raises(WebhookVerificationError, match="Missing required field"):
            parse_webhook_payload(payload)

    def test_invalid_object_type(self):
        """Test parsing with invalid object type."""
        payload = json.dumps(
            {
                "object": "not_event",
                "id": "evt_123",
                "type": "transcription.completed",
                "created_at": 1704067200,
                "data": {},
            }
        )

        with pytest.raises(WebhookVerificationError, match="Invalid object type"):
            parse_webhook_payload(payload)

    def test_invalid_event_type(self):
        """Test parsing with invalid event type."""
        payload = json.dumps(
            {
                "object": "event",
                "id": "evt_123",
                "type": "invalid.event",
                "created_at": 1704067200,
                "data": {},
            }
        )

        with pytest.raises(WebhookVerificationError, match="Invalid event type"):
            parse_webhook_payload(payload)

    def test_invalid_json(self):
        """Test parsing with invalid JSON."""
        with pytest.raises(WebhookVerificationError, match="Invalid JSON"):
            parse_webhook_payload(b"not json")
