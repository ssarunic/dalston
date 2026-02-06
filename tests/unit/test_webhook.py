"""Unit tests for WebhookService."""

import hashlib
import hmac
import json
from uuid import UUID

import httpx
import pytest

from dalston.gateway.services.webhook import (
    WebhookService,
    WebhookValidationError,
    is_private_ip,
    validate_webhook_url,
)


@pytest.fixture
def webhook_service() -> WebhookService:
    """Create WebhookService instance with test secret."""
    return WebhookService(secret="test-webhook-secret")


@pytest.fixture
def sample_job_id() -> UUID:
    """Sample job UUID for testing."""
    return UUID("12345678-1234-5678-1234-567812345678")


class TestBuildPayload:
    """Tests for payload building."""

    def test_completed_payload_basic(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test building a basic completed payload."""
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
        )

        assert payload["event"] == "transcription.completed"
        assert payload["transcription_id"] == str(sample_job_id)
        assert payload["status"] == "completed"
        assert "timestamp" in payload
        assert "text" not in payload
        assert "duration" not in payload
        assert "error" not in payload
        assert "webhook_metadata" not in payload

    def test_completed_payload_with_text(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test payload with transcript text."""
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
            text="Hello, this is a test transcript.",
        )

        assert payload["text"] == "Hello, this is a test transcript."

    def test_text_truncation(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test that text is truncated to 500 characters."""
        long_text = "x" * 1000
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
            text=long_text,
        )

        assert len(payload["text"]) == 500
        assert payload["text"] == "x" * 500

    def test_completed_payload_with_duration(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test payload with audio duration."""
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
            duration=45.2,
        )

        assert payload["duration"] == 45.2

    def test_failed_payload_with_error(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test failed payload includes error message."""
        payload = webhook_service.build_payload(
            event="transcription.failed",
            job_id=sample_job_id,
            status="failed",
            error="Transcription engine failed: CUDA out of memory",
        )

        assert payload["event"] == "transcription.failed"
        assert payload["status"] == "failed"
        assert payload["error"] == "Transcription engine failed: CUDA out of memory"

    def test_payload_with_webhook_metadata(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test payload includes custom webhook_metadata."""
        metadata = {"user_id": "123", "episode": "ep_456"}
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
            webhook_metadata=metadata,
        )

        assert payload["webhook_metadata"] == metadata

    def test_full_completed_payload(
        self, webhook_service: WebhookService, sample_job_id: UUID
    ):
        """Test complete payload with all fields."""
        metadata = {"user_id": "123"}
        payload = webhook_service.build_payload(
            event="transcription.completed",
            job_id=sample_job_id,
            status="completed",
            text="Welcome to the show.",
            duration=120.5,
            webhook_metadata=metadata,
        )

        assert payload["event"] == "transcription.completed"
        assert payload["transcription_id"] == str(sample_job_id)
        assert payload["status"] == "completed"
        assert payload["text"] == "Welcome to the show."
        assert payload["duration"] == 120.5
        assert payload["webhook_metadata"] == metadata
        assert "timestamp" in payload


class TestSignPayload:
    """Tests for HMAC signature generation."""

    def test_sign_payload_format(self, webhook_service: WebhookService):
        """Test signature has correct format."""
        signature = webhook_service.sign_payload('{"test": "data"}', 1234567890)
        assert signature.startswith("sha256=")

    def test_sign_payload_consistent(self, webhook_service: WebhookService):
        """Test signature is consistent for same input."""
        payload = '{"event": "transcription.completed"}'
        timestamp = 1234567890

        sig1 = webhook_service.sign_payload(payload, timestamp)
        sig2 = webhook_service.sign_payload(payload, timestamp)

        assert sig1 == sig2

    def test_sign_payload_different_for_different_input(
        self, webhook_service: WebhookService
    ):
        """Test signature differs for different payloads."""
        timestamp = 1234567890

        sig1 = webhook_service.sign_payload('{"a": 1}', timestamp)
        sig2 = webhook_service.sign_payload('{"a": 2}', timestamp)

        assert sig1 != sig2

    def test_sign_payload_different_for_different_timestamp(
        self, webhook_service: WebhookService
    ):
        """Test signature differs for different timestamps."""
        payload = '{"test": "data"}'

        sig1 = webhook_service.sign_payload(payload, 1234567890)
        sig2 = webhook_service.sign_payload(payload, 1234567891)

        assert sig1 != sig2

    def test_signature_verification_roundtrip(self, webhook_service: WebhookService):
        """Test that signature can be verified."""
        payload = '{"event": "transcription.completed"}'
        timestamp = 1234567890

        signature = webhook_service.sign_payload(payload, timestamp)

        # Verify manually
        signed_payload = f"{timestamp}.{payload}"
        expected = hmac.new(
            b"test-webhook-secret",
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        assert signature == f"sha256={expected}"


class TestDifferentSecrets:
    """Tests for secret-dependent behavior."""

    def test_different_secrets_produce_different_signatures(self):
        """Test that different secrets produce different signatures."""
        service1 = WebhookService(secret="secret-one")
        service2 = WebhookService(secret="secret-two")

        payload = '{"test": "data"}'
        timestamp = 1234567890

        sig1 = service1.sign_payload(payload, timestamp)
        sig2 = service2.sign_payload(payload, timestamp)

        assert sig1 != sig2


@pytest.mark.asyncio
class TestDeliver:
    """Tests for webhook delivery."""

    async def test_deliver_success(self, webhook_service: WebhookService, httpx_mock):
        """Test successful webhook delivery."""
        httpx_mock.add_response(status_code=200, json={"status": "ok"})

        payload = {"event": "transcription.completed", "status": "completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
        )

        assert success is True
        assert status_code == 200
        assert error is None

    async def test_deliver_includes_headers(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that delivery includes required headers."""
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
        )

        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["Content-Type"] == "application/json"
        assert "X-Dalston-Signature" in request.headers
        assert request.headers["X-Dalston-Signature"].startswith("sha256=")
        assert "X-Dalston-Timestamp" in request.headers

    async def test_deliver_failure_4xx_no_retry(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test failed delivery returns False for 4xx (no retries)."""
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,  # Disable retries for this test
        )

        assert success is False
        assert status_code == 400

    async def test_deliver_failure_5xx_no_retry(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test failed delivery returns False for 5xx (no retries)."""
        httpx_mock.add_response(status_code=500)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,  # Disable retries for this test
        )

        assert success is False
        assert status_code == 500

    async def test_deliver_timeout_no_retry(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test timeout handling (no retries)."""
        httpx_mock.add_exception(httpx.TimeoutException("Connection timeout"))

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,  # Disable retries for this test
        )

        assert success is False
        assert status_code is None
        assert error == "timeout"

    async def test_deliver_connection_error_no_retry(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test connection error handling (no retries)."""
        httpx_mock.add_exception(httpx.ConnectError("Connection refused"))

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,  # Disable retries for this test
        )

        assert success is False
        assert status_code is None

    async def test_deliver_201_success(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test 201 is treated as success."""
        httpx_mock.add_response(status_code=201)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
        )

        assert success is True
        assert status_code == 201

    async def test_deliver_payload_is_json(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that payload is sent as valid JSON."""
        httpx_mock.add_response(status_code=200)

        payload = {
            "event": "transcription.completed",
            "transcription_id": "test-123",
            "webhook_metadata": {"nested": {"data": True}},
        }
        await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
        )

        request = httpx_mock.get_request()
        assert request is not None
        sent_payload = json.loads(request.content)
        assert sent_payload["event"] == "transcription.completed"
        assert sent_payload["transcription_id"] == "test-123"
        assert sent_payload["webhook_metadata"]["nested"]["data"] is True


@pytest.mark.asyncio
class TestDeliverRetry:
    """Tests for webhook delivery retry behavior."""

    @pytest.fixture
    def webhook_service(self) -> WebhookService:
        return WebhookService(secret="test-retry-secret")

    async def test_retry_succeeds_on_second_attempt(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that retry succeeds after initial failure."""
        # First request fails, second succeeds
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],  # Fast delays for testing
        )

        assert success is True
        # Should have made 2 requests
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_retry_succeeds_on_third_attempt(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that retry succeeds after multiple failures."""
        # First two requests fail, third succeeds
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=502)
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is True
        requests = httpx_mock.get_requests()
        assert len(requests) == 3

    async def test_retry_exhausted_returns_false(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that all retries exhausted returns False."""
        # All requests fail
        for _ in range(4):  # 1 initial + 3 retries
            httpx_mock.add_response(status_code=500)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is False
        requests = httpx_mock.get_requests()
        assert len(requests) == 4  # 1 initial + 3 retries

    async def test_retry_on_timeout(self, webhook_service: WebhookService, httpx_mock):
        """Test that timeout triggers retry."""
        # First request times out, second succeeds
        httpx_mock.add_exception(httpx.TimeoutException("timeout"))
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is True
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_retry_on_connection_error(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that connection error triggers retry."""
        # First request has connection error, second succeeds
        httpx_mock.add_exception(httpx.ConnectError("refused"))
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is True
        requests = httpx_mock.get_requests()
        assert len(requests) == 2

    async def test_no_retry_on_success(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that successful delivery doesn't trigger retry."""
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is True
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

    async def test_max_retries_zero_no_retry(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that max_retries=0 disables retry."""
        httpx_mock.add_response(status_code=500)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,
        )

        assert success is False
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

    async def test_custom_backoff_delays(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test that custom backoff delays are used."""
        # All fail
        for _ in range(3):
            httpx_mock.add_response(status_code=500)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=2,
            backoff_delays=[0.01, 0.02],
        )

        assert success is False
        requests = httpx_mock.get_requests()
        assert len(requests) == 3  # 1 initial + 2 retries

    async def test_mixed_errors_then_success(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test retry through different error types then success."""
        httpx_mock.add_exception(httpx.TimeoutException("timeout"))
        httpx_mock.add_response(status_code=503)
        httpx_mock.add_exception(httpx.ConnectError("refused"))
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=3,
            backoff_delays=[0.01, 0.01, 0.01],
        )

        assert success is True
        requests = httpx_mock.get_requests()
        assert len(requests) == 4


class TestUrlValidation:
    """Tests for webhook URL validation."""

    def test_is_private_ip_loopback(self):
        """Test loopback IPs are detected as private."""
        assert is_private_ip("127.0.0.1") is True
        assert is_private_ip("::1") is True

    def test_is_private_ip_private_ranges(self):
        """Test private IP ranges are detected."""
        assert is_private_ip("10.0.0.1") is True
        assert is_private_ip("172.16.0.1") is True
        assert is_private_ip("192.168.1.1") is True

    def test_is_private_ip_public(self):
        """Test public IPs are not flagged as private."""
        assert is_private_ip("8.8.8.8") is False
        assert is_private_ip("1.1.1.1") is False

    def test_is_private_ip_link_local(self):
        """Test link-local IPs are detected as private."""
        assert is_private_ip("169.254.1.1") is True
        assert is_private_ip("fe80::1") is True

    def test_validate_webhook_url_valid_https(self):
        """Test valid HTTPS URLs pass validation."""
        # Should not raise
        validate_webhook_url("https://example.com/webhook")
        validate_webhook_url("https://api.mysite.com/hooks/dalston")

    def test_validate_webhook_url_valid_http(self):
        """Test HTTP URLs pass validation (used in development)."""
        # Should not raise
        validate_webhook_url("http://example.com/webhook")

    def test_validate_webhook_url_invalid_scheme(self):
        """Test invalid URL schemes are rejected."""
        with pytest.raises(WebhookValidationError) as exc_info:
            validate_webhook_url("ftp://example.com/webhook")
        assert "Invalid URL scheme" in str(exc_info.value)

    def test_validate_webhook_url_no_hostname(self):
        """Test URLs without hostname are rejected."""
        with pytest.raises(WebhookValidationError) as exc_info:
            validate_webhook_url("https:///webhook")
        assert "hostname" in str(exc_info.value).lower()

    def test_validate_webhook_url_localhost_allowed(self):
        """Test localhost is allowed (for development) but logs warning."""
        # Should not raise - localhost is allowed for dev
        validate_webhook_url("http://localhost:9999/webhook")
        validate_webhook_url("http://127.0.0.1:8000/webhook")

    def test_validate_webhook_url_allow_private_flag(self):
        """Test allow_private flag skips private IP check."""
        # Should not raise with allow_private=True
        validate_webhook_url("http://10.0.0.1/webhook", allow_private=True)
        validate_webhook_url("http://192.168.1.100/webhook", allow_private=True)


@pytest.mark.asyncio
class TestDeliverUrlValidation:
    """Tests for URL validation in deliver method."""

    @pytest.fixture
    def webhook_service(self) -> WebhookService:
        return WebhookService(secret="test-validation-secret")

    async def test_deliver_rejects_private_ip_url(
        self, webhook_service: WebhookService
    ):
        """Test delivery fails for private IP URLs."""
        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="http://10.0.0.1/webhook",
            payload=payload,
            max_retries=0,
        )
        # Should return False due to validation failure
        assert success is False
        assert status_code is None
        assert error is not None

    async def test_deliver_allows_private_with_flag(
        self, webhook_service: WebhookService, httpx_mock
    ):
        """Test delivery proceeds when allow_private_urls=True."""
        httpx_mock.add_response(status_code=200)

        payload = {"event": "transcription.completed"}
        success, status_code, error = await webhook_service.deliver(
            url="https://example.com/webhook",
            payload=payload,
            max_retries=0,
            allow_private_urls=True,
        )
        assert success is True
        assert status_code == 200
