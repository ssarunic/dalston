"""End-to-end tests for webhook delivery flow.

Tests the complete webhook flow from job creation to webhook delivery,
including signature verification.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from dalston.gateway.services.webhook import WebhookService


class TestWebhookEndToEnd:
    """End-to-end tests for webhook delivery."""

    @pytest.fixture
    def webhook_secret(self) -> str:
        return "test-e2e-webhook-secret"

    @pytest.fixture
    def webhook_service(self, webhook_secret: str) -> WebhookService:
        return WebhookService(secret=webhook_secret)

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def webhook_url(self) -> str:
        return "https://my-app.example.com/webhooks/dalston"

    @pytest.fixture
    def webhook_metadata(self) -> dict:
        return {"user_id": "user_123", "project": "podcast-transcription"}

    @pytest.fixture
    def transcript_text(self) -> str:
        return "Welcome to the show. Today we're discussing webhooks and how they make async workflows possible."

    def verify_signature(
        self, payload_json: str, signature: str, timestamp: str, secret: str
    ) -> bool:
        """Verify webhook signature (simulating what client would do)."""
        signed_payload = f"{timestamp}.{payload_json}"
        expected = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    @pytest.mark.asyncio
    async def test_completed_webhook_e2e(
        self,
        webhook_service: WebhookService,
        webhook_secret: str,
        job_id: UUID,
        webhook_url: str,
        webhook_metadata: dict,
        transcript_text: str,
        httpx_mock,
    ):
        """Test complete flow: job completes -> webhook delivered with valid signature."""
        httpx_mock.add_response(status_code=200, json={"status": "received"})

        # Build payload (simulating what orchestrator does)
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            text=transcript_text,
            duration=125.5,
            webhook_metadata=webhook_metadata,
        )

        # Deliver webhook
        success = await webhook_service.deliver(webhook_url, payload)

        # Verify delivery succeeded
        assert success is True

        # Get the captured request
        request = httpx_mock.get_request()
        assert request is not None

        # Verify headers
        headers = request.headers
        assert headers["content-type"] == "application/json"
        assert "x-dalston-signature" in headers
        assert "x-dalston-timestamp" in headers

        signature = headers["x-dalston-signature"]
        timestamp = headers["x-dalston-timestamp"]
        body = request.content.decode()

        # Verify signature (as client would)
        assert self.verify_signature(body, signature, timestamp, webhook_secret)

        # Verify payload content
        received_payload = json.loads(body)
        assert received_payload["event"] == "transcription.completed"
        assert received_payload["transcription_id"] == str(job_id)
        assert received_payload["status"] == "completed"
        assert received_payload["text"] == transcript_text
        assert received_payload["duration"] == 125.5
        assert received_payload["webhook_metadata"] == webhook_metadata
        assert "timestamp" in received_payload

    @pytest.mark.asyncio
    async def test_failed_webhook_e2e(
        self,
        webhook_service: WebhookService,
        webhook_secret: str,
        job_id: UUID,
        webhook_url: str,
        webhook_metadata: dict,
        httpx_mock,
    ):
        """Test complete flow: job fails -> webhook delivered with error."""
        httpx_mock.add_response(status_code=200)

        error_message = "Transcription engine failed: CUDA out of memory"

        payload = webhook_service.build_payload(
            event="transcription.failed",
            job_id=job_id,
            status="failed",
            error=error_message,
            webhook_metadata=webhook_metadata,
        )

        success = await webhook_service.deliver(webhook_url, payload)

        assert success is True

        request = httpx_mock.get_request()
        received_payload = json.loads(request.content.decode())
        assert received_payload["event"] == "transcription.failed"
        assert received_payload["status"] == "failed"
        assert received_payload["error"] == error_message
        assert received_payload["webhook_metadata"] == webhook_metadata
        assert "text" not in received_payload
        assert "duration" not in received_payload

    @pytest.mark.asyncio
    async def test_long_text_truncated(
        self,
        webhook_service: WebhookService,
        job_id: UUID,
        webhook_url: str,
        httpx_mock,
    ):
        """Test that long transcript text is truncated to 500 chars."""
        httpx_mock.add_response(status_code=200)

        # Create very long text
        long_text = "This is a test. " * 100  # ~1600 chars

        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            text=long_text,
        )

        await webhook_service.deliver(webhook_url, payload)

        request = httpx_mock.get_request()
        received_payload = json.loads(request.content.decode())
        assert len(received_payload["text"]) == 500

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(
        self,
        webhook_service: WebhookService,
        job_id: UUID,
        webhook_url: str,
        httpx_mock,
    ):
        """Test that client can detect invalid signature."""
        httpx_mock.add_response(status_code=200)

        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
        )

        await webhook_service.deliver(webhook_url, payload)

        request = httpx_mock.get_request()
        headers = request.headers
        body = request.content.decode()

        # Verify with WRONG secret
        wrong_secret = "wrong-secret"
        signature = headers["x-dalston-signature"]
        timestamp = headers["x-dalston-timestamp"]

        # Should fail verification with wrong secret
        assert not self.verify_signature(body, signature, timestamp, wrong_secret)

    @pytest.mark.asyncio
    async def test_webhook_delivery_with_nested_metadata(
        self,
        webhook_service: WebhookService,
        job_id: UUID,
        webhook_url: str,
        httpx_mock,
    ):
        """Test webhook with complex nested metadata."""
        httpx_mock.add_response(status_code=200)

        complex_metadata = {
            "user": {"id": "u_123", "email": "test@example.com"},
            "project": {"id": "p_456", "name": "Podcast"},
            "tags": ["audio", "interview", "2024"],
            "config": {"notify_slack": True, "channel": "#transcripts"},
        }

        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            webhook_metadata=complex_metadata,
        )

        success = await webhook_service.deliver(webhook_url, payload)
        assert success is True

        request = httpx_mock.get_request()
        received_payload = json.loads(request.content.decode())
        assert received_payload["webhook_metadata"] == complex_metadata


class TestWebhookEventFlow:
    """Test the event publishing and handling flow."""

    @pytest.mark.asyncio
    async def test_publish_job_completed_event(self):
        """Test that job.completed event is published correctly."""
        from dalston.common.events import publish_job_completed

        mock_redis = AsyncMock()
        job_id = uuid4()

        await publish_job_completed(mock_redis, job_id)

        # Verify publish was called
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "dalston:events"

        # Parse the published message
        published_data = json.loads(call_args[0][1])
        assert published_data["type"] == "job.completed"
        assert published_data["job_id"] == str(job_id)
        assert "timestamp" in published_data

    @pytest.mark.asyncio
    async def test_publish_job_failed_event(self):
        """Test that job.failed event is published correctly."""
        from dalston.common.events import publish_job_failed

        mock_redis = AsyncMock()
        job_id = uuid4()
        error = "Task transcribe failed: Audio file corrupted"

        await publish_job_failed(mock_redis, job_id, error)

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args

        published_data = json.loads(call_args[0][1])
        assert published_data["type"] == "job.failed"
        assert published_data["job_id"] == str(job_id)
        assert published_data["error"] == error


class TestWebhookPayloadSpec:
    """Test webhook payload matches M05 specification."""

    @pytest.fixture
    def service(self) -> WebhookService:
        return WebhookService(secret="spec-test-secret")

    def test_completed_payload_matches_spec(self, service: WebhookService):
        """Verify completed payload matches M05 spec."""
        job_id = uuid4()
        payload = service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            text="First 500 chars of transcript...",
            duration=45.2,
            webhook_metadata={"user_id": "123"},
        )

        # Verify all required fields per M05 spec
        assert payload["event"] == "transcription.completed"
        assert payload["transcription_id"] == str(job_id)
        assert payload["status"] == "completed"
        assert "timestamp" in payload
        assert payload["text"] == "First 500 chars of transcript..."
        assert payload["duration"] == 45.2
        assert payload["webhook_metadata"] == {"user_id": "123"}

    def test_failed_payload_matches_spec(self, service: WebhookService):
        """Verify failed payload matches M05 spec."""
        job_id = uuid4()
        payload = service.build_payload(
            event="transcription.failed",
            job_id=job_id,
            status="failed",
            error="Transcription failed: timeout",
            webhook_metadata={"user_id": "123"},
        )

        assert payload["event"] == "transcription.failed"
        assert payload["status"] == "failed"
        assert payload["error"] == "Transcription failed: timeout"
        assert payload["webhook_metadata"] == {"user_id": "123"}

    def test_headers_match_spec(self, service: WebhookService):
        """Verify signature format matches M05 spec."""
        payload_json = '{"test": "data"}'
        timestamp = 1706443350

        signature = service.sign_payload(payload_json, timestamp)

        # M05 spec: X-Dalston-Signature: sha256={hmac_hex}
        assert signature.startswith("sha256=")
        # Hex digest should be 64 chars
        hex_part = signature.replace("sha256=", "")
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)
