"""Correlation ID middleware for request tracing.

Generates a unique request_id for every incoming HTTP request and
WebSocket connection, stores it in structlog contextvars, and returns
it in the X-Request-ID response header.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


def _generate_request_id() -> str:
    """Generate a request ID with the req_ prefix."""
    return f"req_{uuid.uuid4().hex}"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that assigns a correlation ID to every request.

    - Reads X-Request-ID from the incoming request if present.
    - Otherwise generates a new ``req_<uuid4>`` identifier.
    - Binds it into structlog contextvars so all downstream log calls
      include ``request_id`` automatically.
    - Sets the X-Request-ID response header for client-side correlation.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or _generate_request_id()

        # Bind into structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Store on request.state for access in route handlers
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
