"""Prometheus metrics middleware for the Gateway.

Records request counts, latencies, and other HTTP metrics.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

import dalston.metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that records Prometheus metrics for HTTP requests.

    Records:
    - Total requests by method, endpoint, and status code
    - Request duration by method and endpoint
    - Upload bytes for file uploads
    """

    # Endpoints to exclude from metrics (health checks, metrics endpoint itself)
    EXCLUDE_PATHS = {"/health", "/metrics", "/"}

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process request and record metrics.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response from the handler
        """
        # Skip metrics for excluded paths
        path = request.url.path
        if path in self.EXCLUDE_PATHS:
            return await call_next(request)

        # Normalize path for metrics (remove IDs to avoid cardinality explosion)
        endpoint = self._normalize_path(path)
        method = request.method

        # Record request start time
        start_time = time.perf_counter()

        # Process request
        response = await call_next(request)

        # Record duration
        duration = time.perf_counter() - start_time

        # Record metrics
        dalston.metrics.inc_gateway_requests(method, endpoint, response.status_code)
        dalston.metrics.observe_gateway_request_duration(method, endpoint, duration)

        # Track upload bytes for POST requests with content-length
        if method == "POST" and request.headers.get("content-length"):
            try:
                content_length = int(request.headers["content-length"])
                dalston.metrics.inc_gateway_upload_bytes(content_length)
            except ValueError:
                pass

        return response

    def _normalize_path(self, path: str) -> str:
        """Normalize path by replacing IDs with placeholders.

        This prevents metric cardinality explosion from unique IDs.

        Args:
            path: Original request path

        Returns:
            Normalized path with IDs replaced
        """
        parts = path.split("/")
        normalized = []

        for i, part in enumerate(parts):
            if not part:
                continue

            # Check if this looks like a UUID or job/task ID
            if self._looks_like_id(part):
                # Use previous part to determine ID type
                if i > 0 and parts[i - 1] in ("jobs", "transcriptions"):
                    normalized.append("{job_id}")
                elif i > 0 and parts[i - 1] in ("tasks",):
                    normalized.append("{task_id}")
                elif i > 0 and parts[i - 1] in ("sessions",):
                    normalized.append("{session_id}")
                elif i > 0 and parts[i - 1] in ("tenants",):
                    normalized.append("{tenant_id}")
                elif i > 0 and parts[i - 1] in ("endpoints",):
                    normalized.append("{endpoint_id}")
                elif i > 0 and parts[i - 1] in ("keys",):
                    normalized.append("{key_id}")
                else:
                    normalized.append("{id}")
            else:
                normalized.append(part)

        return "/" + "/".join(normalized) if normalized else "/"

    def _looks_like_id(self, part: str) -> bool:
        """Check if a path part looks like an ID.

        Args:
            part: Path segment to check

        Returns:
            True if it looks like an ID
        """
        # UUID format (with or without dashes)
        if len(part) == 36 and part.count("-") == 4:
            return True
        if len(part) == 32 and all(c in "0123456789abcdef" for c in part.lower()):
            return True

        # Prefixed IDs (job_, task_, sess_, etc.)
        prefixes = ("job_", "task_", "sess_", "req_", "key_", "ep_")
        if any(part.startswith(p) for p in prefixes):
            return True

        return False
