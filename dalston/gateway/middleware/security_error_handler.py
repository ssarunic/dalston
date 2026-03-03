"""Middleware to convert security exceptions to HTTP responses (M45).

This middleware catches security exceptions raised by the application
and converts them to appropriate HTTP responses with consistent structure.

Exception -> HTTP Status Code:
- AuthenticationError -> 401 Unauthorized
- AuthorizationError -> 403 Forbidden
- ResourceNotFoundError -> 404 Not Found
- RateLimitExceededError -> 429 Too Many Requests
- SecurityError (generic) -> 403 Forbidden
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from dalston.gateway.security.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitExceededError,
    ResourceNotFoundError,
    SecurityError,
)


class SecurityErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Convert domain security exceptions to HTTP responses.

    This middleware should be added early in the middleware stack
    to catch security exceptions from any handler or dependency.

    Example response for AuthenticationError:
        {
            "detail": "Authentication required",
            "code": "authentication_failed"
        }

    Example response for AuthorizationError:
        {
            "detail": "Missing required permission: job:delete",
            "code": "authorization_failed",
            "required_permission": "job:delete"
        }
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except AuthenticationError as e:
            return JSONResponse(
                status_code=401,
                content={"detail": str(e), "code": e.code},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except AuthorizationError as e:
            content = {
                "detail": str(e),
                "code": e.code,
            }
            if e.required_permission:
                content["required_permission"] = e.required_permission
            return JSONResponse(
                status_code=403,
                content=content,
            )
        except ResourceNotFoundError as e:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": str(e),
                    "code": e.code,
                    "resource_type": e.resource_type,
                },
            )
        except RateLimitExceededError as e:
            return JSONResponse(
                status_code=429,
                content={"detail": str(e), "code": e.code},
                headers={"Retry-After": str(e.retry_after)},
            )
        except SecurityError as e:
            # Generic security error - shouldn't happen often
            return JSONResponse(
                status_code=403,
                content={"detail": str(e), "code": e.code or "security_error"},
            )
