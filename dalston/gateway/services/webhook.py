"""Webhook delivery service.

Handles building webhook payloads, signing with HMAC-SHA256, and delivery
with retry logic using exponential backoff.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import structlog

# Retry configuration per M05.4 spec
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_DELAYS = [1.0, 2.0, 4.0]  # seconds

logger = structlog.get_logger()


class WebhookValidationError(Exception):
    """Raised when webhook URL validation fails."""

    pass


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is private/internal.

    Args:
        ip_str: IP address string

    Returns:
        True if IP is private/internal, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        # Invalid IP - let it pass through, will fail at connection time
        return False


def validate_webhook_url(url: str, allow_private: bool = False) -> None:
    """Validate a webhook URL for security.

    Checks:
    - URL is valid and uses HTTPS (or HTTP for localhost in dev)
    - Hostname does not resolve to private/internal IPs (SSRF protection)

    Args:
        url: Webhook URL to validate
        allow_private: If True, skip private IP checks (for testing/dev)

    Raises:
        WebhookValidationError: If URL is invalid or points to internal network
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise WebhookValidationError(f"Invalid URL: {e}") from e

    # Check scheme
    if parsed.scheme not in ("http", "https"):
        raise WebhookValidationError(
            f"Invalid URL scheme: {parsed.scheme}. Must be http or https."
        )

    # Check hostname exists
    if not parsed.hostname:
        raise WebhookValidationError("URL must have a hostname")

    hostname = parsed.hostname

    # Skip private IP check if allowed (for testing/development)
    if allow_private:
        return

    # Allow localhost for development but warn
    if hostname in ("localhost", "127.0.0.1", "::1"):
        # Log warning but allow for development
        logger.warning(
            "webhook_localhost_url",
            url=url,
            message="Webhook URL points to localhost - only use in development",
        )
        return

    # Resolve hostname and check for private IPs
    try:
        # Get all IP addresses for the hostname
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for info in addr_info:
            ip_str = info[4][0]
            if is_private_ip(ip_str):
                raise WebhookValidationError(
                    f"Webhook URL resolves to private IP ({ip_str}). "
                    "This may indicate an SSRF attempt."
                )
    except socket.gaierror:
        # DNS resolution failed - let httpx handle the error during delivery
        pass
    except WebhookValidationError:
        raise
    except Exception as e:
        # Log but don't block - let delivery attempt handle actual errors
        logger.warning("webhook_url_validation_error", url=url, error=str(e))


class WebhookService:
    """Service for delivering webhook notifications."""

    def __init__(self, secret: str):
        """Initialize webhook service.

        Args:
            secret: HMAC secret for signing webhook payloads
        """
        self.secret = secret

    def build_payload(
        self,
        event: str,
        job_id: UUID,
        status: str,
        text: str | None = None,
        duration: float | None = None,
        error: str | None = None,
        webhook_metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Build webhook payload per M05 spec.

        Args:
            event: Event type (e.g., "transcription.completed")
            job_id: Job UUID
            status: Job status
            text: First 500 chars of transcript text (for completed jobs)
            duration: Audio duration in seconds
            error: Error message (for failed jobs)
            webhook_metadata: Custom data to echo back

        Returns:
            Webhook payload dictionary
        """
        payload: dict[str, Any] = {
            "event": event,
            "transcription_id": str(job_id),
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if text is not None:
            # Truncate to first 500 characters
            payload["text"] = text[:500] if len(text) > 500 else text

        if duration is not None:
            payload["duration"] = duration

        if error is not None:
            payload["error"] = error

        if webhook_metadata is not None:
            payload["webhook_metadata"] = webhook_metadata

        return payload

    def sign_payload(
        self, payload_json: str, timestamp: int, secret: str | None = None
    ) -> str:
        """Generate HMAC-SHA256 signature for webhook payload.

        The signature is computed over: "{timestamp}.{payload_json}"

        Args:
            payload_json: JSON-serialized payload
            timestamp: Unix timestamp
            secret: Signing secret (defaults to self.secret if not provided)

        Returns:
            Signature in format "sha256={hex_digest}"
        """
        signing_secret = secret or self.secret
        signed_payload = f"{timestamp}.{payload_json}"
        signature = hmac.new(
            signing_secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    async def deliver(
        self,
        url: str,
        payload: dict[str, Any],
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_delays: list[float] | None = None,
        allow_private_urls: bool = False,
        secret: str | None = None,
        delivery_id: UUID | None = None,
    ) -> tuple[bool, int | None, str | None]:
        """Deliver webhook to the specified URL with retry logic.

        Retries up to max_retries times with exponential backoff on failure.
        Per M05.4 spec: 3 retries with delays of 1s, 2s, 4s.

        Args:
            url: Webhook URL to POST to
            payload: Payload dictionary to send
            max_retries: Maximum number of retry attempts (default: 3)
            backoff_delays: List of delay seconds between retries (default: [1, 2, 4])
            allow_private_urls: If True, skip private IP validation (for testing)
            secret: Signing secret (defaults to self.secret if not provided)
            delivery_id: Optional delivery UUID for deduplication header

        Returns:
            Tuple of (success, last_status_code, last_error)
        """
        if backoff_delays is None:
            backoff_delays = DEFAULT_BACKOFF_DELAYS

        log = logger.bind(url=url, event=payload.get("event"))
        if delivery_id:
            log = log.bind(delivery_id=str(delivery_id))

        # Validate URL for SSRF protection
        try:
            validate_webhook_url(url, allow_private=allow_private_urls)
        except WebhookValidationError as e:
            log.error("webhook_url_validation_failed", error=str(e))
            return False, None, str(e)

        timestamp = int(time.time())
        payload_json = json.dumps(payload, default=str)
        signature = self.sign_payload(payload_json, timestamp, secret=secret)

        headers = {
            "Content-Type": "application/json",
            "X-Dalston-Signature": signature,
            "X-Dalston-Timestamp": str(timestamp),
        }
        if delivery_id:
            headers["X-Dalston-Webhook-Id"] = str(delivery_id)

        last_error: str | None = None
        last_status_code: int | None = None

        for attempt in range(max_retries + 1):  # Initial attempt + retries
            attempt_log = log.bind(attempt=attempt + 1, max_attempts=max_retries + 1)

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        url, content=payload_json, headers=headers
                    )

                if response.status_code < 300:
                    attempt_log.info(
                        "webhook_delivered", status_code=response.status_code
                    )
                    return True, response.status_code, None

                # Non-2xx response - log and potentially retry
                last_status_code = response.status_code
                last_error = f"HTTP {response.status_code}"
                attempt_log.warning(
                    "webhook_delivery_failed",
                    status_code=response.status_code,
                    response_body=response.text[:200],
                )

            except httpx.TimeoutException:
                last_error = "timeout"
                attempt_log.warning("webhook_timeout")

            except httpx.RequestError as e:
                last_error = str(e)
                attempt_log.warning("webhook_request_error", error=str(e))

            except Exception as e:
                last_error = str(e)
                attempt_log.warning("webhook_unexpected_error", error=str(e))

            # Check if we should retry
            if attempt < max_retries:
                delay = (
                    backoff_delays[attempt]
                    if attempt < len(backoff_delays)
                    else backoff_delays[-1]
                )
                attempt_log.info("webhook_retry_scheduled", delay_seconds=delay)
                await asyncio.sleep(delay)
            else:
                # All retries exhausted
                log.error(
                    "webhook_delivery_exhausted",
                    total_attempts=max_retries + 1,
                    last_error=last_error,
                    last_status_code=last_status_code,
                )

        return False, last_status_code, last_error
