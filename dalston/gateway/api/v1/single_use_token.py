"""ElevenLabs-compatible single-use token endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dalston.gateway.dependencies import (
    get_auth_service,
    get_security_manager,
    require_auth,
)
from dalston.gateway.error_codes import Err
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import APIKey, AuthService, Scope, SessionToken

router = APIRouter(prefix="/single-use-token", tags=["speech-to-text", "elevenlabs"])


class CreateSingleUseTokenRequest(BaseModel):
    """Optional request body for single-use token creation."""

    ttl: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Token lifetime in seconds (default: 300).",
    )


class SingleUseTokenResponse(BaseModel):
    """ElevenLabs-compatible single-use token response."""

    token: str
    expires_at: datetime
    token_type: str
    tenant_id: UUID


@router.post(
    "/{token_type}",
    response_model=SingleUseTokenResponse,
    status_code=200,
    summary="Create ElevenLabs single-use token",
    description=(
        "Create a short-lived token for browser-safe realtime auth on "
        "WS /v1/speech-to-text/realtime?token=..."
    ),
)
async def create_single_use_token(
    token_type: str,
    api_identity: Annotated[APIKey | SessionToken, Depends(require_auth)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    request: CreateSingleUseTokenRequest | None = None,
) -> SingleUseTokenResponse:
    """Create a single-use realtime token compatible with ElevenLabs contracts."""
    supported_token_types = {
        "speech_to_text",
        "realtime",
        "realtime_scribe",
        "tts_websocket",
    }
    if token_type not in supported_token_types:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported token_type. Supported values: speech_to_text, realtime, "
                "realtime_scribe, tts_websocket."
            ),
        )

    principal = (
        Principal.from_session_token(api_identity)
        if isinstance(api_identity, SessionToken)
        else Principal.from_api_key(api_identity)
    )
    security_manager.require_permission(principal, Permission.SESSION_CREATE)

    if isinstance(api_identity, SessionToken):
        raise HTTPException(
            status_code=403,
            detail="Session token cannot create another single-use token.",
        )

    if not api_identity.has_scope(Scope.REALTIME):
        raise HTTPException(status_code=403, detail=Err.KEY_REQUIRES_REALTIME_SCOPE)

    ttl = request.ttl if request is not None else 300
    raw_token, session_token = await auth_service.create_session_token(
        api_key=api_identity,
        ttl=ttl,
        scopes=[Scope.REALTIME],
        token_type=token_type,
        single_use=True,
    )
    return SingleUseTokenResponse(
        token=raw_token,
        expires_at=session_token.expires_at,
        token_type=session_token.token_type,
        tenant_id=session_token.tenant_id,
    )
