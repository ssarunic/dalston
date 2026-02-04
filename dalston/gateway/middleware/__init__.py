from dalston.gateway.middleware.auth import (
    WS_CLOSE_INVALID_KEY,
    WS_CLOSE_MISSING_SCOPE,
    WS_CLOSE_RATE_LIMIT,
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
    authenticate_request,
    authenticate_websocket,
    extract_api_key_from_request,
    extract_api_key_from_websocket,
    require_scope,
)
from dalston.gateway.middleware.error_handler import setup_exception_handlers

__all__ = [
    "setup_exception_handlers",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "WS_CLOSE_INVALID_KEY",
    "WS_CLOSE_MISSING_SCOPE",
    "WS_CLOSE_RATE_LIMIT",
    "authenticate_request",
    "authenticate_websocket",
    "extract_api_key_from_request",
    "extract_api_key_from_websocket",
    "require_scope",
]
