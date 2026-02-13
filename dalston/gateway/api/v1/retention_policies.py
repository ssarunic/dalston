"""Retention policy API endpoints.

POST   /v1/retention-policies           Create policy
GET    /v1/retention-policies           List policies (tenant + system)
GET    /v1/retention-policies/{id}      Get policy by ID
GET    /v1/retention-policies/by-name/{name}  Get policy by name
DELETE /v1/retention-policies/{id}      Delete policy (if not in use)
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audit import AuditService
from dalston.common.models import RetentionMode, RetentionScope
from dalston.gateway.dependencies import (
    RequireAdmin,
    get_audit_service,
    get_db,
    get_retention_service,
)
from dalston.gateway.services.retention import (
    RetentionPolicyInUseError,
    RetentionPolicyNotFoundError,
    RetentionService,
)

router = APIRouter(prefix="/retention-policies", tags=["retention"])


# Request/Response models


class CreatePolicyRequest(BaseModel):
    """Request to create a retention policy."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique policy name",
    )
    mode: RetentionMode = Field(
        ...,
        description="Retention mode: auto_delete, keep, or none",
    )
    hours: int | None = Field(
        default=None,
        ge=1,
        description="Hours to retain (required for auto_delete)",
    )
    scope: RetentionScope = Field(
        default=RetentionScope.ALL,
        description="What to delete: all or audio_only",
    )
    realtime_mode: str = Field(
        default="inherit",
        description="Mode for realtime sessions: inherit, auto_delete, keep, none",
    )
    realtime_hours: int | None = Field(
        default=None,
        ge=1,
        description="Override hours for realtime sessions",
    )
    delete_realtime_on_enhancement: bool = Field(
        default=True,
        description="Delete realtime artifacts when batch enhancement completes",
    )


class RetentionPolicyResponse(BaseModel):
    """Response for a retention policy."""

    id: UUID
    tenant_id: UUID | None
    name: str
    mode: str
    hours: int | None
    scope: str
    realtime_mode: str
    realtime_hours: int | None
    delete_realtime_on_enhancement: bool
    is_system: bool
    created_at: datetime


class RetentionPolicyListResponse(BaseModel):
    """Response for listing retention policies."""

    policies: list[RetentionPolicyResponse]


def _policy_to_response(policy) -> RetentionPolicyResponse:
    """Convert RetentionPolicyModel to response."""
    return RetentionPolicyResponse(
        id=policy.id,
        tenant_id=policy.tenant_id,
        name=policy.name,
        mode=policy.mode,
        hours=policy.hours,
        scope=policy.scope,
        realtime_mode=policy.realtime_mode,
        realtime_hours=policy.realtime_hours,
        delete_realtime_on_enhancement=policy.delete_realtime_on_enhancement,
        is_system=policy.is_system,
        created_at=policy.created_at,
    )


@router.post(
    "",
    response_model=RetentionPolicyResponse,
    status_code=201,
    summary="Create retention policy",
    description="Create a new tenant retention policy.",
)
async def create_retention_policy(
    request: Request,
    body: CreatePolicyRequest,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    retention_service: RetentionService = Depends(get_retention_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> RetentionPolicyResponse:
    """Create a new retention policy."""
    try:
        policy = await retention_service.create_policy(
            db=db,
            tenant_id=api_key.tenant_id,
            name=body.name,
            mode=body.mode,
            hours=body.hours,
            scope=body.scope,
            realtime_mode=body.realtime_mode,
            realtime_hours=body.realtime_hours,
            delete_realtime_on_enhancement=body.delete_realtime_on_enhancement,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Audit log
    correlation_id = getattr(request.state, "request_id", None)
    await audit_service.log_retention_policy_created(
        policy_id=policy.id,
        tenant_id=api_key.tenant_id,
        policy_name=policy.name,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=correlation_id,
        ip_address=request.client.host if request.client else None,
    )

    return _policy_to_response(policy)


@router.get(
    "",
    response_model=RetentionPolicyListResponse,
    summary="List retention policies",
    description="List all retention policies available to the tenant (including system policies).",
)
async def list_retention_policies(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    retention_service: RetentionService = Depends(get_retention_service),
) -> RetentionPolicyListResponse:
    """List all available retention policies."""
    policies = await retention_service.list_policies(db, api_key.tenant_id)
    return RetentionPolicyListResponse(
        policies=[_policy_to_response(p) for p in policies]
    )


@router.get(
    "/by-name/{name}",
    response_model=RetentionPolicyResponse,
    summary="Get retention policy by name",
    description="Get a retention policy by name (tenant policies, then system policies).",
)
async def get_retention_policy_by_name(
    name: str,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    retention_service: RetentionService = Depends(get_retention_service),
) -> RetentionPolicyResponse:
    """Get a retention policy by name."""
    policy = await retention_service.get_policy_by_name(db, api_key.tenant_id, name)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy '{name}' not found")
    return _policy_to_response(policy)


@router.get(
    "/{policy_id}",
    response_model=RetentionPolicyResponse,
    summary="Get retention policy",
    description="Get a retention policy by ID.",
)
async def get_retention_policy(
    policy_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    retention_service: RetentionService = Depends(get_retention_service),
) -> RetentionPolicyResponse:
    """Get a retention policy by ID."""
    policy = await retention_service.get_policy(db, policy_id, api_key.tenant_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _policy_to_response(policy)


@router.delete(
    "/{policy_id}",
    status_code=204,
    summary="Delete retention policy",
    description="Delete a retention policy. Cannot delete system policies or policies in use.",
    responses={
        204: {"description": "Policy deleted successfully"},
        404: {"description": "Policy not found"},
        409: {"description": "Policy is in use and cannot be deleted"},
    },
)
async def delete_retention_policy(
    request: Request,
    policy_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    retention_service: RetentionService = Depends(get_retention_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> None:
    """Delete a retention policy."""
    try:
        await retention_service.delete_policy(db, policy_id, api_key.tenant_id)
    except RetentionPolicyNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found") from None
    except RetentionPolicyInUseError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Audit log
    correlation_id = getattr(request.state, "request_id", None)
    await audit_service.log_retention_policy_deleted(
        policy_id=policy_id,
        tenant_id=api_key.tenant_id,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=correlation_id,
        ip_address=request.client.host if request.client else None,
    )
