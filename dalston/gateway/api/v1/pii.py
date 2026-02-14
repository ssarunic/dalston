"""PII detection API endpoints (M26)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import PIIEntityTypeModel
from dalston.gateway.dependencies import get_db
from dalston.gateway.models.responses import (
    PIIEntityTypeResponse,
    PIIEntityTypesResponse,
)

router = APIRouter(prefix="/pii", tags=["pii"])


@router.get(
    "/entity-types",
    response_model=PIIEntityTypesResponse,
    summary="List available PII entity types",
    description="Returns all available PII entity types that can be detected.",
)
async def list_entity_types(
    db: Annotated[AsyncSession, Depends(get_db)],
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
    query = select(PIIEntityTypeModel)

    if category:
        query = query.where(PIIEntityTypeModel.category == category)

    if defaults_only:
        query = query.where(PIIEntityTypeModel.is_default.is_(True))

    query = query.order_by(PIIEntityTypeModel.category, PIIEntityTypeModel.id)

    result = await db.execute(query)
    entity_types = result.scalars().all()

    return PIIEntityTypesResponse(
        entity_types=[PIIEntityTypeResponse.model_validate(et) for et in entity_types],
        total=len(entity_types),
    )
