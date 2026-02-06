"""Webhook signature verification for Dalston.

Follows the Standard Webhooks specification:
https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md

Provides utilities for verifying webhook signatures and parsing
webhook payloads. Uses timing-safe comparison to prevent timing attacks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from .exceptions import WebhookVerificationError
from .types import WebhookEventType, WebhookPayload


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    msg_id: str,
    timestamp: str,
    secret: str,
    max_age: int = 300,
) -> bool:
    """Verify webhook signature per Standard Webhooks specification.

    Uses HMAC-SHA256 with timing-safe comparison to prevent timing attacks.
    The signed payload format is: "{msg_id}.{timestamp}.{payload}"

    Args:
        payload: Raw request body bytes.
        signature: webhook-signature header value ("v1,{base64}").
        msg_id: webhook-id header value.
        timestamp: webhook-timestamp header value (Unix timestamp).
        secret: Webhook signing secret (whsec_...).
        max_age: Maximum age in seconds (default 5 minutes).

    Returns:
        True if signature is valid.

    Raises:
        WebhookVerificationError: If signature is invalid or timestamp is stale.

    Example:
        ```python
        # In your webhook handler
        is_valid = verify_webhook_signature(
            payload=request.body,
            signature=request.headers["webhook-signature"],
            msg_id=request.headers["webhook-id"],
            timestamp=request.headers["webhook-timestamp"],
            secret="whsec_...",
        )

        if not is_valid:
            return Response(status=401)
        ```
    """
    # Validate timestamp format
    try:
        ts = int(timestamp)
    except (ValueError, TypeError) as e:
        raise WebhookVerificationError(f"Invalid timestamp format: {timestamp}") from e

    # Check timestamp freshness
    current_time = int(time.time())
    if abs(current_time - ts) > max_age:
        raise WebhookVerificationError(
            f"Timestamp too old: {abs(current_time - ts)}s > {max_age}s"
        )

    # Validate signature format (v1,{base64})
    if not signature.startswith("v1,"):
        raise WebhookVerificationError(
            "Invalid signature format: must start with 'v1,'"
        )

    provided_sig_b64 = signature[3:]  # Remove "v1," prefix

    try:
        provided_sig = base64.b64decode(provided_sig_b64)
    except Exception as e:
        raise WebhookVerificationError(f"Invalid signature encoding: {e}") from e

    # Extract raw secret bytes (remove whsec_ prefix if present)
    secret_bytes = secret.encode("utf-8")
    if secret.startswith("whsec_"):
        try:
            # Decode URL-safe base64 portion after prefix
            # token_urlsafe produces base64 without padding, so we add it
            b64_part = secret[6:]
            # Add padding if needed (base64 length must be multiple of 4)
            padding = 4 - (len(b64_part) % 4)
            if padding != 4:
                b64_part += "=" * padding
            secret_bytes = base64.urlsafe_b64decode(b64_part)
        except Exception as e:
            raise WebhookVerificationError(f"Invalid secret encoding: {e}") from e

    # Standard Webhooks: sign "{msg_id}.{timestamp}.{body}"
    signed_payload = f"{msg_id}.{timestamp}.{payload.decode('utf-8')}"
    expected_sig = hmac.new(
        secret_bytes,
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    # Timing-safe comparison
    return hmac.compare_digest(expected_sig, provided_sig)


def parse_webhook_payload(payload: bytes | str) -> WebhookPayload:
    """Parse webhook payload JSON into WebhookPayload object.

    Expects Standard Webhooks format with envelope fields.

    Args:
        payload: Raw payload bytes or JSON string.

    Returns:
        Parsed WebhookPayload object.

    Raises:
        WebhookVerificationError: If payload is invalid JSON or missing fields.

    Example:
        ```python
        payload = parse_webhook_payload(request.body)

        if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
            transcription_id = payload.transcription_id
            # Fetch full transcript using transcription_id
        ```
    """
    try:
        if isinstance(payload, bytes):
            data = json.loads(payload.decode("utf-8"))
        else:
            data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise WebhookVerificationError(f"Invalid JSON payload: {e}") from e

    # Validate required Standard Webhooks fields
    required_fields = ["object", "id", "type", "created_at", "data"]
    for field in required_fields:
        if field not in data:
            raise WebhookVerificationError(f"Missing required field: {field}")

    # Validate object type
    if data["object"] != "event":
        raise WebhookVerificationError(
            f"Invalid object type: {data['object']} (expected 'event')"
        )

    # Parse event type
    try:
        event_type = WebhookEventType(data["type"])
    except ValueError as e:
        raise WebhookVerificationError(f"Invalid event type: {data['type']}") from e

    return WebhookPayload(
        object=data["object"],
        id=data["id"],
        type=event_type,
        created_at=data["created_at"],
        data=data.get("data", {}),
    )


# -----------------------------------------------------------------------------
# Framework Integrations
# -----------------------------------------------------------------------------


def fastapi_webhook_dependency(secret: str, max_age: int = 300) -> Any:
    """Create a FastAPI dependency for webhook verification.

    Uses Standard Webhooks headers: webhook-id, webhook-timestamp, webhook-signature.

    Args:
        secret: Webhook secret for signature verification (whsec_...).
        max_age: Maximum age in seconds for timestamp validation.

    Returns:
        FastAPI dependency function.

    Example:
        ```python
        from fastapi import FastAPI, Depends
        from dalston import fastapi_webhook_dependency

        app = FastAPI()
        verify_webhook = fastapi_webhook_dependency("whsec_...")

        @app.post("/webhooks/dalston")
        async def handle_webhook(payload: WebhookPayload = Depends(verify_webhook)):
            if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
                # Handle completion
                pass
        ```
    """
    # Import here to avoid requiring FastAPI as a dependency
    from fastapi import HTTPException, Request

    async def verify(request: Request) -> WebhookPayload:
        body = await request.body()

        # Standard Webhooks headers
        signature = request.headers.get("webhook-signature", "")
        msg_id = request.headers.get("webhook-id", "")
        timestamp = request.headers.get("webhook-timestamp", "")

        if not signature or not msg_id or not timestamp:
            raise HTTPException(
                status_code=401,
                detail="Missing webhook headers (webhook-id, webhook-timestamp, webhook-signature)",
            )

        try:
            if not verify_webhook_signature(
                body, signature, msg_id, timestamp, secret, max_age
            ):
                raise HTTPException(status_code=401, detail="Invalid signature")
        except WebhookVerificationError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

        try:
            return parse_webhook_payload(body)
        except WebhookVerificationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return verify


def flask_verify_webhook(secret: str, max_age: int = 300) -> Any:
    """Create a Flask decorator for webhook verification.

    Uses Standard Webhooks headers: webhook-id, webhook-timestamp, webhook-signature.

    Args:
        secret: Webhook secret for signature verification (whsec_...).
        max_age: Maximum age in seconds for timestamp validation.

    Returns:
        Decorator function.

    Example:
        ```python
        from flask import Flask
        from dalston import flask_verify_webhook

        app = Flask(__name__)
        verify = flask_verify_webhook("whsec_...")

        @app.route("/webhooks/dalston", methods=["POST"])
        @verify
        def handle_webhook(payload: WebhookPayload):
            if payload.type == WebhookEventType.TRANSCRIPTION_COMPLETED:
                # Handle completion
                pass
        ```
    """
    from collections.abc import Callable
    from functools import wraps

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Import here to avoid requiring Flask as a dependency
            from flask import abort, request

            body = request.get_data()

            # Standard Webhooks headers
            signature = request.headers.get("webhook-signature", "")
            msg_id = request.headers.get("webhook-id", "")
            timestamp = request.headers.get("webhook-timestamp", "")

            if not signature or not msg_id or not timestamp:
                abort(401, "Missing webhook headers")

            try:
                if not verify_webhook_signature(
                    body, signature, msg_id, timestamp, secret, max_age
                ):
                    abort(401, "Invalid signature")
            except WebhookVerificationError as e:
                abort(401, str(e))

            try:
                payload = parse_webhook_payload(body)
            except WebhookVerificationError as e:
                abort(400, str(e))

            return f(payload, *args, **kwargs)

        return wrapper

    return decorator
