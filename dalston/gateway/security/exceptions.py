"""Domain-specific security exceptions (M45).

These exceptions are raised by the security layer and mapped to HTTP responses
by the SecurityErrorHandlerMiddleware.

Exception -> HTTP Status Code:
- AuthenticationError -> 401 Unauthorized
- AuthorizationError -> 403 Forbidden
- ResourceNotFoundError -> 404 Not Found (anti-enumeration)
- RateLimitExceededError -> 429 Too Many Requests
"""

from uuid import UUID


class SecurityError(Exception):
    """Base class for security exceptions.

    All security-related exceptions inherit from this class to enable
    centralized exception handling in middleware.
    """

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


class AuthenticationError(SecurityError):
    """Authentication failed - invalid or missing credentials.

    Raised when:
    - No API key provided
    - API key is invalid, expired, or revoked
    - Session token is invalid or expired

    Maps to HTTP 401 Unauthorized.
    """

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="authentication_failed")


class AuthorizationError(SecurityError):
    """Authorization failed - insufficient permissions.

    Raised when:
    - Principal lacks required scope
    - Principal lacks required permission
    - Principal cannot perform action on resource

    Maps to HTTP 403 Forbidden.
    """

    def __init__(
        self,
        message: str = "Permission denied",
        *,
        required_permission: str | None = None,
    ):
        super().__init__(message, code="authorization_failed")
        self.required_permission = required_permission


class ResourceNotFoundError(SecurityError):
    """Resource not found or not accessible to principal.

    This exception returns 404 instead of 403 to prevent information leakage
    about resource existence (anti-enumeration). Use this when a principal
    cannot access a resource due to tenant isolation or ownership rules.

    Maps to HTTP 404 Not Found.
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: str | UUID,
        *,
        message: str | None = None,
    ):
        msg = message or f"{resource_type} not found: {resource_id}"
        super().__init__(msg, code="resource_not_found")
        self.resource_type = resource_type
        self.resource_id = str(resource_id)


class RateLimitExceededError(SecurityError):
    """Rate limit exceeded.

    Raised when:
    - Request rate limit exceeded
    - Concurrent job/session limit exceeded

    Maps to HTTP 429 Too Many Requests.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: int = 60,
    ):
        super().__init__(message, code="rate_limit_exceeded")
        self.retry_after = retry_after
