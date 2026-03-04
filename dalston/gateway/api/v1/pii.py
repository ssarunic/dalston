"""PII detection API endpoints (M26)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.dependencies import (
    get_db,
    get_pii_entity_type_service,
    get_principal,
    get_security_manager,
)
from dalston.gateway.models.responses import (
    PIIEntityTypeResponse,
    PIIEntityTypesResponse,
)
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.pii_entity_types import PIIEntityTypeService

router = APIRouter(prefix="/pii", tags=["pii"])


@router.get(
    "/entity-types",
    response_model=PIIEntityTypesResponse,
    summary="List available PII entity types",
    description="Returns all available PII entity types that can be detected.",
)
async def list_entity_types(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pii_service: Annotated[PIIEntityTypeService, Depends(get_pii_entity_type_service)],
    category: str | None = Query(
        default=None,
        description="Filter by category: pii, pci, phi",
    ),
    defaults_only: bool = Query(
        default=False,
        description="Only return default entity types",
    ),
) -> PIIEntityTypesResponse:
    """List available PII entity types.

    Returns all entity types that can be configured for PII detection.
    Optionally filter by category (pii, pci, phi) or default status.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.JOB_READ)

    entity_types = await pii_service.list_entity_types(
        db,
        category=category,
        defaults_only=defaults_only,
    )

    return PIIEntityTypesResponse(
        entity_types=[PIIEntityTypeResponse.model_validate(et) for et in entity_types],
        total=len(entity_types),
    )
