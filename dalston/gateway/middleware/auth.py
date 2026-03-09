"""Authentication middleware for API key validation.

Extracts and validates API keys from:
1. Authorization: Bearer <key> header
2. xi-api-key: <key> header (ElevenLabs compatibility)
3. api_key query parameter (WebSocket fallback)
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, Request, WebSocket, status

from dalston.gateway.error_codes import Err
from dalston.gateway.services.auth import (
    TOKEN_PREFIX,
    APIKey,
    AuthService,
    Scope,
    SessionToken,
)

logger = structlog.get_logger()


class AuthenticationError(HTTPException):
    """Authentication failed."""

    def __init__(self, detail: str = "Invalid or missing API key"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthorizationError(HTTPException):
    """Authorization failed (missing scope)."""

    def __init__(self, scope: Scope):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required scope: {scope.value}",
        )


class RateLimitError(HTTPException):
    """Rate limit exceeded."""

    def __init__(self, retry_after: int = 60):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=Err.RATE_LIMIT_EXCEEDED,
            headers={"Retry-After": str(retry_after)},
        )


# WebSocket close codes for authentication errors
WS_CLOSE_INVALID_KEY = 4001
WS_CLOSE_MISSING_SCOPE = 4003
WS_CLOSE_RATE_LIMIT = 4029


def extract_api_key_from_request(request: Request) -> str | None:
    """Extract API key from HTTP request.

    Checks in order:
    1. Authorization: Bearer <key>
    2. xi-api-key: <key> (ElevenLabs compatibility, deprecated)

    Args:
        request: FastAPI Request object

    Returns:
        API key string or None if not found
    """
    # Check Authorization header
    auth_header = request.headers.get("authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]

    # Check xi-api-key header (ElevenLabs compatibility, deprecated)
    xi_key = request.headers.get("xi-api-key")
    if xi_key:
        logger.warning(
            "xi-api-key header is deprecated, use Authorization: Bearer instead",
            path=request.url.path,
        )
        return xi_key

    return None


def extract_api_key_from_websocket(websocket: WebSocket) -> str | None:
    """Extract API key from WebSocket connection.

    Checks in order:
    1. Authorization: Bearer header (preferred)
    2. api_key query parameter (fallback for browser clients)
    3. xi-api-key header (ElevenLabs-compatible, deprecated)

    Args:
        websocket: FastAPI WebSocket object

    Returns:
        API key string or None if not found
    """
    # Check Authorization header (preferred)
    auth_header = websocket.headers.get("authorization")
    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]

    # Check query parameter (fallback for browser clients that can't set headers)
    api_key = websocket.query_params.get("api_key")
    if api_key:
        return api_key

    # Check xi-api-key header (ElevenLabs-compatible, deprecated)
    xi_key = websocket.headers.get("xi-api-key")
    if xi_key:
        logger.warning(
            "xi-api-key header is deprecated, use Authorization: Bearer instead",
            path=str(websocket.url.path),
        )
        return xi_key

    return None


async def authenticate_request(
    request: Request,
    auth_service: AuthService,
) -> APIKey | SessionToken:
    """Authenticate an HTTP request.

    Supports both API keys (dk_) and session tokens (tk_).

    Args:
        request: FastAPI Request object
        auth_service: AuthService instance

    Returns:
        Validated APIKey or SessionToken object

    Raises:
        AuthenticationError: If key is missing or invalid
        RateLimitError: If rate limit exceeded
    """
    raw_key = extract_api_key_from_request(request)

    if not raw_key:
        raise AuthenticationError("Missing API key")

    if raw_key.startswith("sk-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": (
                        "Detected an OpenAI API key (sk-...) on a Dalston endpoint. "
                        "Use a Dalston API key (dk_...)."
                    ),
                    "type": "authentication_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if this is a session token
    if raw_key.startswith(TOKEN_PREFIX):
        session_token = await auth_service.validate_session_token(raw_key)
        if not session_token:
            raise AuthenticationError("Invalid or expired session token")
        # Store in request state
        request.state.api_key = session_token
        request.state.tenant_id = session_token.tenant_id
        return session_token

    # Otherwise, validate as API key
    api_key = await auth_service.validate_api_key(raw_key)

    if not api_key:
        raise AuthenticationError("Invalid API key")

    # Check rate limit (only for API keys, session tokens inherit from parent)
    allowed, remaining = await auth_service.check_rate_limit(api_key)
    if not allowed:
        raise RateLimitError()

    # Store in request state for later use
    request.state.api_key = api_key
    request.state.tenant_id = api_key.tenant_id

    return api_key


def _get_dev_api_key_for_websocket() -> APIKey:
    """Create a development API key for WebSocket security_mode=none.

    This is only used in development and returns an API key
    with full admin permissions.

    Raises:
        RuntimeError: If security_mode=none is used outside development environment
    """
    import os
    from datetime import UTC, datetime
    from uuid import UUID

    from dalston.db.session import DEFAULT_TENANT_ID

    # Guard: security_mode=none requires explicit development environment
    env = os.getenv("DALSTON_ENV", "").lower()
    if env not in ("development", "dev", "test"):
        raise RuntimeError(
            "security_mode=none requires DALSTON_ENV=development|dev|test. "
            "Refusing to disable auth in non-development environment."
        )

    # Well-known dev key ID (deterministic for testing)
    dev_key_id = UUID("00000000-0000-0000-0000-000000000002")

    return APIKey(
        id=dev_key_id,
        key_hash="dev_key_hash",
        prefix="dk_dev0000",
        name="Development Key",
        tenant_id=DEFAULT_TENANT_ID,
        scopes=[Scope.ADMIN],  # Full access in dev mode
        rate_limit=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
        revoked_at=None,
    )


async def authenticate_websocket(
    websocket: WebSocket,
    auth_service: AuthService,
    required_scope: Scope = Scope.REALTIME,
) -> APIKey | SessionToken | None:
    """Authenticate a WebSocket connection BEFORE accepting.

    Supports both API keys (dk_) and session tokens (tk_).
    This must be called before websocket.accept() to reject
    invalid connections with appropriate close codes.

    In security_mode=none (development only), returns a dev API key
    with admin permissions without requiring authentication.

    Args:
        websocket: FastAPI WebSocket object
        auth_service: AuthService instance
        required_scope: Required scope for WebSocket access

    Returns:
        Validated APIKey or SessionToken object, or None if authentication failed
        (connection will be closed with appropriate code)
    """
    from dalston.gateway.security.manager import get_security_manager

    security_manager = get_security_manager()
    if security_manager.mode == "none":
        # Development mode: return dev API key with admin access
        return _get_dev_api_key_for_websocket()

    raw_key = extract_api_key_from_websocket(websocket)

    if not raw_key:
        await websocket.close(code=WS_CLOSE_INVALID_KEY, reason="Missing API key")
        return None

    if raw_key.startswith("sk-"):
        await websocket.close(
            code=WS_CLOSE_INVALID_KEY,
            reason="Detected OpenAI API key (sk-...). Use Dalston key (dk_...).",
        )
        return None

    # Check if this is a session token
    if raw_key.startswith(TOKEN_PREFIX):
        session_token = await auth_service.validate_session_token(raw_key)
        if not session_token:
            await websocket.close(
                code=WS_CLOSE_INVALID_KEY, reason="Invalid or expired session token"
            )
            return None

        # Check scope
        if not session_token.has_scope(required_scope):
            await websocket.close(
                code=WS_CLOSE_MISSING_SCOPE,
                reason=f"Missing required scope: {required_scope.value}",
            )
            return None

        return session_token

    # Otherwise, validate as API key
    api_key = await auth_service.validate_api_key(raw_key)

    if not api_key:
        await websocket.close(code=WS_CLOSE_INVALID_KEY, reason="Invalid API key")
        return None

    # Check scope
    if not api_key.has_scope(required_scope):
        await websocket.close(
            code=WS_CLOSE_MISSING_SCOPE,
            reason=f"Missing required scope: {required_scope.value}",
        )
        return None

    # Check rate limit (only for API keys)
    allowed, _ = await auth_service.check_rate_limit(api_key)
    if not allowed:
        await websocket.close(code=WS_CLOSE_RATE_LIMIT, reason="Rate limit exceeded")
        return None

    return api_key


def require_scope(api_key: APIKey | SessionToken, scope: Scope) -> None:
    """Check if API key or session token has required scope.

    Args:
        api_key: Validated APIKey or SessionToken object
        scope: Required scope

    Raises:
        AuthorizationError: If scope is missing
    """
    if not api_key.has_scope(scope):
        raise AuthorizationError(scope)
