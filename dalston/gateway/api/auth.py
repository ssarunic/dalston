"""API Key and session token management endpoints.

POST /auth/keys - Create new API key
GET /auth/keys - List tenant's API keys
GET /auth/keys/{id} - Get key details
DELETE /auth/keys/{id} - Revoke API key
GET /auth/me - Get current key info
POST /auth/tokens - Create ephemeral session token
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from dalston.common.audit import AuditService
from dalston.gateway.dependencies import (
    get_audit_service,
    get_auth_service,
    get_principal,
    get_security_manager,
    require_auth,
)
from dalston.gateway.error_codes import Err
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import (
    DEFAULT_TOKEN_TTL,
    APIKey,
    AuthService,
    Scope,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class CreateAPIKeyRequest(BaseModel):
    """Request to create a new API key."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Human-readable name"
    )
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
    expires_at: datetime
    is_current: bool = Field(
        default=False,
        description="True if this is the key making the current request",
    )
    is_revoked: bool = Field(
        default=False,
        description="True if this key has been revoked",
    )

    @classmethod
    def from_api_key(
        cls,
        api_key: APIKey,
        current_key_id: UUID | None = None,
    ) -> "APIKeyResponse":
        return cls(
            id=api_key.id,
            prefix=api_key.prefix,
            name=api_key.name,
            tenant_id=api_key.tenant_id,
            scopes=[s.value for s in api_key.scopes],
            rate_limit=api_key.rate_limit,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            expires_at=api_key.expires_at,
            is_current=current_key_id is not None and api_key.id == current_key_id,
            is_revoked=api_key.is_revoked,
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
    expires_at: datetime


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
    expires_at: datetime


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
    request: Request,
    create_request: CreateAPIKeyRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> APIKeyCreatedResponse:
    """Create a new API key for the current tenant.

    The full key is returned only once - store it securely!
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.API_KEY_CREATE)

    # Parse scopes
    scopes: list[Scope] | None = None
    if create_request.scopes:
        try:
            scopes = [Scope(s) for s in create_request.scopes]
        except ValueError as e:
            valid_scopes = [s.value for s in Scope]
            raise HTTPException(
                status_code=400,
                detail=Err.INVALID_SCOPE.format(valid_scopes=valid_scopes),
            ) from e

    # Create key
    raw_key, new_key = await auth_service.create_api_key(
        name=create_request.name,
        tenant_id=principal.tenant_id,
        scopes=scopes,
        rate_limit=create_request.rate_limit,
    )

    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_api_key_created(
        key_id=new_key.id,
        tenant_id=principal.tenant_id,
        key_name=new_key.name,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
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
        expires_at=new_key.expires_at,
    )


@router.get(
    "/keys",
    response_model=APIKeyListResponse,
    summary="List API keys",
    description="List all API keys for the current tenant. Requires admin scope.",
)
async def list_api_keys(
    principal: Annotated[Principal, Depends(get_principal)],
    include_revoked: Annotated[
        bool,
        Query(description="Include revoked keys in the list"),
    ] = False,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKeyListResponse:
    """List all API keys for the current tenant."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.API_KEY_LIST)

    keys = await auth_service.list_api_keys(
        principal.tenant_id,
        include_revoked=include_revoked,
    )

    return APIKeyListResponse(
        keys=[
            APIKeyResponse.from_api_key(k, current_key_id=principal.id) for k in keys
        ],
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
    principal: Annotated[Principal, Depends(get_principal)],
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKeyResponse:
    """Get details of a specific API key."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.API_KEY_LIST)

    target_key = await auth_service.get_api_key_by_id(key_id)

    if target_key is None:
        raise HTTPException(status_code=404, detail=Err.API_KEY_NOT_FOUND)

    # Verify tenant ownership
    if target_key.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail=Err.API_KEY_NOT_FOUND)

    return APIKeyResponse.from_api_key(target_key, current_key_id=principal.id)


@router.delete(
    "/keys/{key_id}",
    status_code=204,
    summary="Revoke API key",
    description="Revoke an API key. Requires admin scope.",
    responses={404: {"description": "API key not found"}},
)
async def revoke_api_key(
    request: Request,
    key_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> None:
    """Revoke an API key."""
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.API_KEY_REVOKE)

    # First check if key exists and belongs to tenant
    target_key = await auth_service.get_api_key_by_id(key_id)

    if target_key is None:
        raise HTTPException(status_code=404, detail=Err.API_KEY_NOT_FOUND)

    if target_key.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail=Err.API_KEY_NOT_FOUND)

    # Prevent self-revocation
    if target_key.id == principal.id:
        raise HTTPException(
            status_code=400,
            detail=Err.CANNOT_REVOKE_OWN_KEY,
        )

    await auth_service.revoke_api_key(key_id)

    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_api_key_revoked(
        key_id=key_id,
        tenant_id=principal.tenant_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
    )


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
        expires_at=api_key.expires_at,
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
            detail=Err.KEY_REQUIRES_REALTIME_SCOPE,
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
                detail=Err.INVALID_SCOPE.format(valid_scopes=valid_scopes),
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
