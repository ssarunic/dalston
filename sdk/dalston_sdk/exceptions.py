"""Exception classes for the Dalston SDK.

Provides a consistent exception hierarchy with HTTP status codes
for easy error handling.
"""

from __future__ import annotations


class DalstonError(Exception):
    """Base exception for all SDK errors.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code if applicable.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.status_code}] {self.message}"
        return self.message


class AuthenticationError(DalstonError):
    """API key invalid or missing (401)."""

    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__(message, status_code=401)


class ForbiddenError(DalstonError):
    """Insufficient permissions (403)."""

    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message, status_code=403)


# Backward compatibility alias (deprecated)
PermissionError = ForbiddenError


class NotFoundError(DalstonError):
    """Resource not found (404)."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status_code=404)


class ValidationError(DalstonError):
    """Invalid request parameters (400/422)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message, status_code=status_code)


class RateLimitError(DalstonError):
    """Rate limit exceeded (429).

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by server.
    """

    def __init__(
        self, message: str = "Rate limit exceeded", retry_after: int | None = None
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class ServerError(DalstonError):
    """Server-side error (5xx)."""

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message, status_code=500)


class ConnectError(DalstonError):
    """Network or connection error."""

    def __init__(self, message: str = "Connection failed") -> None:
        super().__init__(message)


# Backward compatibility alias (deprecated)
ConnectionError = ConnectError


class TimeoutException(DalstonError):
    """Request or operation timeout."""

    def __init__(self, message: str = "Operation timed out") -> None:
        super().__init__(message)


# Backward compatibility alias (deprecated)
TimeoutError = TimeoutException


class WebhookVerificationError(DalstonError):
    """Webhook signature verification failed."""

    def __init__(self, message: str = "Invalid webhook signature") -> None:
        super().__init__(message)


class RealtimeError(DalstonError):
    """Error during real-time session.

    Attributes:
        code: Error code from server (e.g., "no_capacity", "invalid_audio").
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
