"""End-to-end tests for webhook delivery flow.

Tests the complete webhook flow from job creation to webhook delivery,
including signature verification. Uses Standard Webhooks format.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from dalston.gateway.services.webhook import WebhookService


class TestWebhookEndToEnd:
    """End-to-end tests for webhook delivery (Standard Webhooks format)."""

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

    def verify_signature(
        self,
        payload_json: str,
        signature: str,
        msg_id: str,
        timestamp: str,
        secret: str,
    ) -> bool:
        """Verify webhook signature per Standard Webhooks spec."""
        # Standard Webhooks: sign "{msg_id}.{timestamp}.{body}"
        signed_payload = f"{msg_id}.{timestamp}.{payload_json}"
        expected = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).digest()
        expected_sig = f"v1,{base64.b64encode(expected).decode()}"
        return hmac.compare_digest(expected_sig, signature)

    @pytest.mark.asyncio
    async def test_completed_webhook_e2e(
        self,
        webhook_service: WebhookService,
        webhook_secret: str,
        job_id: UUID,
        webhook_url: str,
        httpx_mock,
    ):
        """Test complete flow: job completes -> webhook delivered with valid signature."""
        httpx_mock.add_response(status_code=200, json={"status": "received"})

        # Build payload (simulating what orchestrator does)
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            duration=125.5,
        )

        # Deliver webhook
        success, status_code, error = await webhook_service.deliver(
            webhook_url, payload
        )

        # Verify delivery succeeded
        assert success is True
        assert status_code == 200

        # Get the captured request
        request = httpx_mock.get_request()
        assert request is not None

        # Verify Standard Webhooks headers
        headers = request.headers
        assert headers["content-type"] == "application/json"
        assert "webhook-signature" in headers
        assert "webhook-timestamp" in headers
        assert "webhook-id" in headers

        signature = headers["webhook-signature"]
        timestamp = headers["webhook-timestamp"]
        msg_id = headers["webhook-id"]
        body = request.content.decode()

        # Verify signature (as client would)
        assert self.verify_signature(body, signature, msg_id, timestamp, webhook_secret)

        # Verify payload content (Standard Webhooks envelope)
        received_payload = json.loads(body)
        assert received_payload["object"] == "event"
        assert received_payload["type"] == "transcription.completed"
        assert "id" in received_payload  # evt_...
        assert "created_at" in received_payload
        assert received_payload["data"]["transcription_id"] == str(job_id)
        assert received_payload["data"]["status"] == "completed"
        assert received_payload["data"]["duration"] == 125.5

    @pytest.mark.asyncio
    async def test_failed_webhook_e2e(
        self,
        webhook_service: WebhookService,
        webhook_secret: str,
        job_id: UUID,
        webhook_url: str,
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
        )

        success, status_code, error = await webhook_service.deliver(
            webhook_url, payload
        )

        assert success is True
        assert status_code == 200

        request = httpx_mock.get_request()
        received_payload = json.loads(request.content.decode())
        assert received_payload["type"] == "transcription.failed"
        assert received_payload["data"]["status"] == "failed"
        assert received_payload["data"]["error"] == error_message

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
        signature = headers["webhook-signature"]
        timestamp = headers["webhook-timestamp"]
        msg_id = headers["webhook-id"]

        # Should fail verification with wrong secret
        assert not self.verify_signature(
            body, signature, msg_id, timestamp, wrong_secret
        )

    @pytest.mark.asyncio
    async def test_webhook_delivery_success(
        self,
        webhook_service: WebhookService,
        job_id: UUID,
        webhook_url: str,
        httpx_mock,
    ):
        """Test webhook delivery succeeds and payload is correct."""
        httpx_mock.add_response(status_code=200)

        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            duration=60.5,
        )

        success, status_code, error = await webhook_service.deliver(
            webhook_url, payload
        )
        assert success is True
        assert status_code == 200

        request = httpx_mock.get_request()
        received_payload = json.loads(request.content.decode())
        assert received_payload["data"]["transcription_id"] == str(job_id)
        assert received_payload["data"]["status"] == "completed"
        assert received_payload["data"]["duration"] == 60.5


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
    """Test webhook payload matches Standard Webhooks specification."""

    @pytest.fixture
    def service(self) -> WebhookService:
        return WebhookService(secret="spec-test-secret")

    def test_completed_payload_matches_spec(self, service: WebhookService):
        """Verify completed payload matches Standard Webhooks spec."""
        job_id = uuid4()
        payload = service.build_payload(
            event="transcription.completed",
            job_id=job_id,
            status="completed",
            duration=45.2,
        )

        # Verify Standard Webhooks envelope
        assert payload["object"] == "event"
        assert payload["type"] == "transcription.completed"
        assert payload["id"].startswith("evt_")
        assert isinstance(payload["created_at"], int)

        # Verify data payload
        assert payload["data"]["transcription_id"] == str(job_id)
        assert payload["data"]["status"] == "completed"
        assert payload["data"]["duration"] == 45.2

    def test_failed_payload_matches_spec(self, service: WebhookService):
        """Verify failed payload matches Standard Webhooks spec."""
        job_id = uuid4()
        payload = service.build_payload(
            event="transcription.failed",
            job_id=job_id,
            status="failed",
            error="Transcription failed: timeout",
        )

        assert payload["object"] == "event"
        assert payload["type"] == "transcription.failed"
        assert payload["data"]["status"] == "failed"
        assert payload["data"]["error"] == "Transcription failed: timeout"

    def test_signature_format_matches_spec(self, service: WebhookService):
        """Verify signature format matches Standard Webhooks spec."""
        payload_json = '{"test": "data"}'
        msg_id = "msg_test123"
        timestamp = 1706443350

        signature = service.sign_payload(payload_json, msg_id, timestamp)

        # Standard Webhooks: v1,{base64}
        assert signature.startswith("v1,")
        # Base64 part should be decodable
        b64_part = signature[3:]
        decoded = base64.b64decode(b64_part)
        # SHA256 produces 32 bytes
        assert len(decoded) == 32
