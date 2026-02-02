"""Webhook signature verification for Dalston.

Provides utilities for verifying webhook signatures and parsing
webhook payloads. Uses timing-safe comparison to prevent timing attacks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from .exceptions import WebhookVerificationError
from .types import WebhookEventType, WebhookPayload


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    secret: str,
    max_age: int = 300,
) -> bool:
    """Verify Dalston webhook signature.

    Uses HMAC-SHA256 with timing-safe comparison to prevent timing attacks.
    The signed payload format is: "{timestamp}.{payload}"

    Args:
        payload: Raw request body bytes.
        signature: X-Dalston-Signature header value ("sha256=...").
        timestamp: X-Dalston-Timestamp header value (Unix timestamp).
        secret: Webhook secret from Dalston configuration.
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
            signature=request.headers["X-Dalston-Signature"],
            timestamp=request.headers["X-Dalston-Timestamp"],
            secret="your-webhook-secret",
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

    # Validate signature format
    if not signature.startswith("sha256="):
        raise WebhookVerificationError("Invalid signature format: must start with 'sha256='")

    provided_hash = signature[7:]  # Remove "sha256=" prefix

    # Compute expected signature
    # Format: "{timestamp}.{payload}"
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected_hash = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Timing-safe comparison
    return hmac.compare_digest(expected_hash, provided_hash)


def parse_webhook_payload(payload: bytes | str) -> WebhookPayload:
    """Parse webhook payload JSON into WebhookPayload object.

    Args:
        payload: Raw payload bytes or JSON string.

    Returns:
        Parsed WebhookPayload object.

    Raises:
        WebhookVerificationError: If payload is invalid JSON or missing fields.

    Example:
        ```python
        payload = parse_webhook_payload(request.body)

        if payload.event == WebhookEventType.JOB_COMPLETED:
            job_id = payload.job_id
            transcript = payload.data.get("transcript")
        ```
    """
    try:
        if isinstance(payload, bytes):
            data = json.loads(payload.decode("utf-8"))
        else:
            data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise WebhookVerificationError(f"Invalid JSON payload: {e}") from e

    # Validate required fields
    required_fields = ["event", "job_id", "timestamp"]
    for field in required_fields:
        if field not in data:
            raise WebhookVerificationError(f"Missing required field: {field}")

    # Parse event type
    try:
        event = WebhookEventType(data["event"])
    except ValueError as e:
        raise WebhookVerificationError(f"Invalid event type: {data['event']}") from e

    # Parse job_id
    try:
        job_id = UUID(data["job_id"]) if isinstance(data["job_id"], str) else data["job_id"]
    except ValueError as e:
        raise WebhookVerificationError(f"Invalid job_id: {data['job_id']}") from e

    # Parse timestamp
    timestamp_val = data["timestamp"]
    if isinstance(timestamp_val, str):
        timestamp_val = timestamp_val.replace("Z", "+00:00")
        try:
            timestamp = datetime.fromisoformat(timestamp_val)
        except ValueError as e:
            raise WebhookVerificationError(f"Invalid timestamp: {timestamp_val}") from e
    elif isinstance(timestamp_val, (int, float)):
        timestamp = datetime.fromtimestamp(timestamp_val)
    else:
        raise WebhookVerificationError(f"Invalid timestamp type: {type(timestamp_val)}")

    return WebhookPayload(
        event=event,
        job_id=job_id,
        timestamp=timestamp,
        data=data.get("data", {}),
        metadata=data.get("metadata"),
    )


# -----------------------------------------------------------------------------
# Framework Integrations
# -----------------------------------------------------------------------------


def fastapi_webhook_dependency(secret: str, max_age: int = 300) -> Any:
    """Create a FastAPI dependency for webhook verification.

    Args:
        secret: Webhook secret for signature verification.
        max_age: Maximum age in seconds for timestamp validation.

    Returns:
        FastAPI dependency function.

    Example:
        ```python
        from fastapi import FastAPI, Depends
        from dalston import fastapi_webhook_dependency

        app = FastAPI()
        verify_webhook = fastapi_webhook_dependency("your-secret")

        @app.post("/webhooks/dalston")
        async def handle_webhook(payload: WebhookPayload = Depends(verify_webhook)):
            if payload.event == WebhookEventType.JOB_COMPLETED:
                # Handle completion
                pass
        ```
    """
    # Import here to avoid requiring FastAPI as a dependency
    from fastapi import HTTPException, Request

    async def verify(request: Request) -> WebhookPayload:
        body = await request.body()
        signature = request.headers.get("X-Dalston-Signature", "")
        timestamp = request.headers.get("X-Dalston-Timestamp", "")

        if not signature or not timestamp:
            raise HTTPException(
                status_code=401,
                detail="Missing signature or timestamp headers",
            )

        try:
            if not verify_webhook_signature(body, signature, timestamp, secret, max_age):
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

    Args:
        secret: Webhook secret for signature verification.
        max_age: Maximum age in seconds for timestamp validation.

    Returns:
        Decorator function.

    Example:
        ```python
        from flask import Flask
        from dalston import flask_verify_webhook

        app = Flask(__name__)
        verify = flask_verify_webhook("your-secret")

        @app.route("/webhooks/dalston", methods=["POST"])
        @verify
        def handle_webhook(payload: WebhookPayload):
            if payload.event == WebhookEventType.JOB_COMPLETED:
                # Handle completion
                pass
        ```
    """
    from functools import wraps
    from typing import Callable

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Import here to avoid requiring Flask as a dependency
            from flask import abort, request

            body = request.get_data()
            signature = request.headers.get("X-Dalston-Signature", "")
            timestamp = request.headers.get("X-Dalston-Timestamp", "")

            if not signature or not timestamp:
                abort(401, "Missing signature or timestamp headers")

            try:
                if not verify_webhook_signature(body, signature, timestamp, secret, max_age):
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
