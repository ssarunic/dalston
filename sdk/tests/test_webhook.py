"""Tests for webhook verification."""

import hashlib
import hmac
import json
import time

import pytest

from dalston import (
    WebhookEventType,
    WebhookPayload,
    WebhookVerificationError,
    parse_webhook_payload,
    verify_webhook_signature,
)


class TestVerifyWebhookSignature:
    """Tests for verify_webhook_signature function."""

    def test_valid_signature(self):
        """Test verification with valid signature."""
        secret = "test-secret"
        timestamp = str(int(time.time()))
        payload = b'{"event": "job.completed", "job_id": "123"}'

        # Compute valid signature
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        signature = "sha256=" + hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        result = verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is True

    def test_invalid_signature(self):
        """Test verification with invalid signature."""
        secret = "test-secret"
        timestamp = str(int(time.time()))
        payload = b'{"event": "job.completed"}'
        signature = "sha256=invalid"

        result = verify_webhook_signature(payload, signature, timestamp, secret)

        assert result is False

    def test_stale_timestamp(self):
        """Test verification with stale timestamp."""
        secret = "test-secret"
        timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        payload = b'{"event": "job.completed"}'

        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        signature = "sha256=" + hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        with pytest.raises(WebhookVerificationError, match="Timestamp too old"):
            verify_webhook_signature(payload, signature, timestamp, secret)

    def test_invalid_timestamp_format(self):
        """Test verification with invalid timestamp format."""
        with pytest.raises(WebhookVerificationError, match="Invalid timestamp"):
            verify_webhook_signature(
                b"payload", "sha256=abc", "not-a-number", "secret"
            )

    def test_invalid_signature_format(self):
        """Test verification with invalid signature format."""
        timestamp = str(int(time.time()))

        with pytest.raises(WebhookVerificationError, match="Invalid signature format"):
            verify_webhook_signature(
                b"payload", "invalid-prefix", timestamp, "secret"
            )

    def test_custom_max_age(self):
        """Test verification with custom max_age."""
        secret = "test-secret"
        timestamp = str(int(time.time()) - 60)  # 1 minute ago
        payload = b'{"event": "job.completed"}'

        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        signature = "sha256=" + hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        # Should pass with 120 second max_age
        result = verify_webhook_signature(
            payload, signature, timestamp, secret, max_age=120
        )
        assert result is True

        # Should fail with 30 second max_age
        with pytest.raises(WebhookVerificationError, match="Timestamp too old"):
            verify_webhook_signature(
                payload, signature, timestamp, secret, max_age=30
            )


class TestParseWebhookPayload:
    """Tests for parse_webhook_payload function."""

    def test_parse_completed_event(self):
        """Test parsing job.completed event."""
        payload = json.dumps({
            "event": "job.completed",
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": "2024-01-01T00:00:00Z",
            "data": {
                "text": "Hello world",
                "duration": 10.5,
            },
            "metadata": {"user_id": "123"},
        })

        result = parse_webhook_payload(payload)

        assert isinstance(result, WebhookPayload)
        assert result.event == WebhookEventType.JOB_COMPLETED
        assert str(result.job_id) == "550e8400-e29b-41d4-a716-446655440000"
        assert result.data["text"] == "Hello world"
        assert result.metadata == {"user_id": "123"}

    def test_parse_failed_event(self):
        """Test parsing job.failed event."""
        payload = json.dumps({
            "event": "job.failed",
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": "2024-01-01T00:00:00Z",
            "data": {
                "error": "Processing failed",
                "stage": "transcribe",
            },
        })

        result = parse_webhook_payload(payload)

        assert result.event == WebhookEventType.JOB_FAILED
        assert result.data["error"] == "Processing failed"

    def test_parse_bytes_payload(self):
        """Test parsing bytes payload."""
        payload = b'{"event": "job.completed", "job_id": "550e8400-e29b-41d4-a716-446655440000", "timestamp": 1704067200}'

        result = parse_webhook_payload(payload)

        assert result.event == WebhookEventType.JOB_COMPLETED

    def test_parse_unix_timestamp(self):
        """Test parsing with Unix timestamp."""
        payload = json.dumps({
            "event": "job.completed",
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": 1704067200,  # Unix timestamp
            "data": {},
        })

        result = parse_webhook_payload(payload)

        assert result.timestamp.year == 2024

    def test_missing_required_field(self):
        """Test parsing with missing required field."""
        payload = json.dumps({
            "event": "job.completed",
            # Missing job_id and timestamp
        })

        with pytest.raises(WebhookVerificationError, match="Missing required field"):
            parse_webhook_payload(payload)

    def test_invalid_event_type(self):
        """Test parsing with invalid event type."""
        payload = json.dumps({
            "event": "invalid.event",
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": "2024-01-01T00:00:00Z",
        })

        with pytest.raises(WebhookVerificationError, match="Invalid event type"):
            parse_webhook_payload(payload)

    def test_invalid_json(self):
        """Test parsing with invalid JSON."""
        with pytest.raises(WebhookVerificationError, match="Invalid JSON"):
            parse_webhook_payload(b"not json")

    def test_invalid_job_id(self):
        """Test parsing with invalid job_id."""
        payload = json.dumps({
            "event": "job.completed",
            "job_id": "not-a-uuid",
            "timestamp": "2024-01-01T00:00:00Z",
        })

        with pytest.raises(WebhookVerificationError, match="Invalid job_id"):
            parse_webhook_payload(payload)
