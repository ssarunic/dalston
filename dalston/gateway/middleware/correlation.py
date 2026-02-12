"""Correlation ID middleware for request tracing.

Generates a unique request_id for every incoming HTTP request and
WebSocket connection, stores it in structlog contextvars, and returns
it in the X-Request-ID response header.

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) so that
WebSocket connections and streaming responses are handled correctly.
"""

from __future__ import annotations

import re
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

import dalston.logging
import dalston.telemetry

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_LOWER = REQUEST_ID_HEADER.lower().encode()

# Client-provided request IDs must be alphanumeric (plus dash, underscore,
# dot) and at most 128 characters to prevent log injection / bloat.
_VALID_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")


def _generate_request_id() -> str:
    """Generate a request ID with the req_ prefix."""
    return f"req_{uuid.uuid4().hex}"


def _sanitize_request_id(raw: str | None) -> str:
    """Return *raw* if it passes validation, otherwise generate a new ID."""
    if raw and _VALID_REQUEST_ID_RE.match(raw):
        return raw
    return _generate_request_id()


class CorrelationIdMiddleware:
    """Pure ASGI middleware that assigns a correlation ID to every request.

    - Reads X-Request-ID from the incoming request if present and valid.
    - Otherwise generates a new ``req_<uuid4>`` identifier.
    - Binds it into structlog contextvars so all downstream log calls
      include ``request_id`` automatically.
    - For HTTP requests, sets the X-Request-ID response header.
    - For WebSocket connections, binds the context but does not attempt
      to modify the handshake headers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract client-provided request ID from headers
        raw_id = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == _REQUEST_ID_HEADER_LOWER:
                raw_id = header_value.decode("latin-1")
                break

        request_id = _sanitize_request_id(raw_id)

        # Reset structlog context for this request/connection and bind request_id.
        # Uses reset_context() to preserve the service name set by configure().
        dalston.logging.reset_context(request_id=request_id)

        # Link request_id to current trace span (M19)
        dalston.telemetry.set_span_attribute("dalston.request_id", request_id)

        # Store on scope state so route handlers can access request.state.request_id.
        # For HTTP, use Request; for WebSocket, set directly on scope state dict.
        if scope["type"] == "http":
            Request(scope).state.request_id = request_id
        else:
            # WebSocket - ensure state dict exists and set request_id
            if "state" not in scope:
                scope["state"] = {}
            scope["state"]["request_id"] = request_id

        if scope["type"] == "http":
            # Wrap send to inject the X-Request-ID response header
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers.append(REQUEST_ID_HEADER, request_id)
                await send(message)

            await self.app(scope, receive, send_with_header)
        else:
            # WebSocket — no response headers to inject
            await self.app(scope, receive, send)
