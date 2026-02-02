"""API Key and session token management endpoints.

POST /auth/keys - Create new API key
GET /auth/keys - List tenant's API keys
GET /auth/keys/{id} - Get key details
DELETE /auth/keys/{id} - Revoke API key
GET /auth/me - Get current key info
POST /auth/tokens - Create ephemeral session token
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dalston.gateway.dependencies import (
    RequireAdmin,
    get_auth_service,
    require_auth,
)
from dalston.gateway.services.auth import (
    APIKey,
    AuthService,
    DEFAULT_TOKEN_TTL,
    Scope,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class CreateAPIKeyRequest(BaseModel):
    """Request to create a new API key."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name")
    scopes: list[str] | None = Field(
        default=None,
        description="Permission scopes. Defaults to jobs:read, jobs:write, realtime",
    )
    rate_limit: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="Requests per minute limit. Null = unlimited",
    )


class APIKeyResponse(BaseModel):
    """API key details (without secret)."""

    id: UUID
    prefix: str
    name: str
    tenant_id: UUID
    scopes: list[str]
    rate_limit: int | None
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_api_key(cls, api_key: APIKey) -> "APIKeyResponse":
        return cls(
            id=api_key.id,
            prefix=api_key.prefix,
            name=api_key.name,
            tenant_id=api_key.tenant_id,
            scopes=[s.value for s in api_key.scopes],
            rate_limit=api_key.rate_limit,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
        )


class APIKeyCreatedResponse(BaseModel):
    """Response when creating a new API key (includes secret once)."""

    id: UUID
    key: str = Field(..., description="Full API key. Store securely - shown only once!")
    prefix: str
    name: str
    tenant_id: UUID
    scopes: list[str]
    rate_limit: int | None
    created_at: datetime


class APIKeyListResponse(BaseModel):
    """List of API keys."""

    keys: list[APIKeyResponse]
    total: int


class CurrentKeyResponse(BaseModel):
    """Current API key info for /auth/me."""

    id: UUID
    prefix: str
    name: str
    tenant_id: UUID
    scopes: list[str]
    rate_limit: int | None
    created_at: datetime
    last_used_at: datetime | None


class CreateSessionTokenRequest(BaseModel):
    """Request to create an ephemeral session token."""

    ttl: int = Field(
        default=DEFAULT_TOKEN_TTL,
        ge=60,
        le=3600,
        description="Time-to-live in seconds (60-3600). Default 600 (10 minutes).",
    )
    scopes: list[str] | None = Field(
        default=None,
        description="Requested scopes. Defaults to [realtime]. Cannot exceed parent key's scopes.",
    )


class SessionTokenResponse(BaseModel):
    """Response when creating a session token."""

    token: str = Field(..., description="Session token. Use for WebSocket auth.")
    expires_at: datetime
    scopes: list[str]
    tenant_id: UUID


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/keys",
    response_model=APIKeyCreatedResponse,
    status_code=201,
    summary="Create API key",
    description="Create a new API key. Requires admin scope.",
)
async def create_api_key(
    request: CreateAPIKeyRequest,
    api_key: RequireAdmin,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKeyCreatedResponse:
    """Create a new API key for the current tenant.

    The full key is returned only once - store it securely!
    """
    # Parse scopes
    scopes: list[Scope] | None = None
    if request.scopes:
        try:
            scopes = [Scope(s) for s in request.scopes]
        except ValueError as e:
            valid_scopes = [s.value for s in Scope]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope. Valid scopes: {valid_scopes}",
            ) from e

    # Create key
    raw_key, new_key = await auth_service.create_api_key(
        name=request.name,
        tenant_id=api_key.tenant_id,
        scopes=scopes,
        rate_limit=request.rate_limit,
    )

    return APIKeyCreatedResponse(
        id=new_key.id,
        key=raw_key,
        prefix=new_key.prefix,
        name=new_key.name,
        tenant_id=new_key.tenant_id,
        scopes=[s.value for s in new_key.scopes],
        rate_limit=new_key.rate_limit,
        created_at=new_key.created_at,
    )


@router.get(
    "/keys",
    response_model=APIKeyListResponse,
    summary="List API keys",
    description="List all API keys for the current tenant. Requires admin scope.",
)
async def list_api_keys(
    api_key: RequireAdmin,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKeyListResponse:
    """List all API keys for the current tenant."""
    keys = await auth_service.list_api_keys(api_key.tenant_id)

    return APIKeyListResponse(
        keys=[APIKeyResponse.from_api_key(k) for k in keys],
        total=len(keys),
    )


@router.get(
    "/keys/{key_id}",
    response_model=APIKeyResponse,
    summary="Get API key",
    description="Get details of a specific API key. Requires admin scope.",
    responses={404: {"description": "API key not found"}},
)
async def get_api_key(
    key_id: UUID,
    api_key: RequireAdmin,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKeyResponse:
    """Get details of a specific API key."""
    target_key = await auth_service.get_api_key_by_id(key_id)

    if target_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    # Verify tenant ownership
    if target_key.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="API key not found")

    return APIKeyResponse.from_api_key(target_key)


@router.delete(
    "/keys/{key_id}",
    status_code=204,
    summary="Revoke API key",
    description="Revoke an API key. Requires admin scope.",
    responses={404: {"description": "API key not found"}},
)
async def revoke_api_key(
    key_id: UUID,
    api_key: RequireAdmin,
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    """Revoke an API key."""
    # First check if key exists and belongs to tenant
    target_key = await auth_service.get_api_key_by_id(key_id)

    if target_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    if target_key.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="API key not found")

    # Prevent self-revocation
    if target_key.id == api_key.id:
        raise HTTPException(
            status_code=400,
            detail="Cannot revoke your own API key",
        )

    await auth_service.revoke_api_key(key_id)


@router.get(
    "/me",
    response_model=CurrentKeyResponse,
    summary="Get current key info",
    description="Get information about the currently authenticated API key.",
)
async def get_current_key(
    api_key: APIKey = Depends(require_auth),
) -> CurrentKeyResponse:
    """Get information about the current API key."""
    return CurrentKeyResponse(
        id=api_key.id,
        prefix=api_key.prefix,
        name=api_key.name,
        tenant_id=api_key.tenant_id,
        scopes=[s.value for s in api_key.scopes],
        rate_limit=api_key.rate_limit,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
    )


@router.post(
    "/tokens",
    response_model=SessionTokenResponse,
    status_code=201,
    summary="Create session token",
    description="Create an ephemeral session token for client-side WebSocket auth. "
    "Requires realtime scope.",
)
async def create_session_token(
    request: CreateSessionTokenRequest,
    api_key: APIKey = Depends(require_auth),
    auth_service: AuthService = Depends(get_auth_service),
) -> SessionTokenResponse:
    """Create an ephemeral session token for browser-based WebSocket connections.

    Session tokens:
    - Have a short TTL (default 10 minutes, max 1 hour)
    - Cannot exceed the parent API key's scopes
    - Are designed for direct client-side use without exposing the API key
    """
    # Verify parent key has realtime scope (required to create realtime tokens)
    if not api_key.has_scope(Scope.REALTIME):
        raise HTTPException(
            status_code=403,
            detail="API key requires 'realtime' scope to create session tokens",
        )

    # Parse scopes
    scopes: list[Scope] | None = None
    if request.scopes:
        try:
            scopes = [Scope(s) for s in request.scopes]
        except ValueError as e:
            valid_scopes = [s.value for s in Scope]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope. Valid scopes: {valid_scopes}",
            ) from e

    # Create token
    try:
        raw_token, session_token = await auth_service.create_session_token(
            api_key=api_key,
            ttl=request.ttl,
            scopes=scopes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return SessionTokenResponse(
        token=raw_token,
        expires_at=session_token.expires_at,
        scopes=[s.value for s in session_token.scopes],
        tenant_id=session_token.tenant_id,
    )
