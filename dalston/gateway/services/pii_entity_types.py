"""PII entity type service for reference data queries."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import PIIEntityTypeModel


@dataclass
class PIIEntityTypeDTO:
    """PII entity type data transfer object."""

    id: str
    display_name: str
    category: str
    description: str | None
    is_default: bool


class PIIEntityTypeService:
    """Service for PII entity type reference data."""

    async def list_entity_types(
        self,
        db: AsyncSession,
        *,
        category: str | None = None,
        defaults_only: bool = False,
    ) -> list[PIIEntityTypeDTO]:
        """List PII entity types with optional filters.

        Args:
            db: Database session
            category: Filter by category (pii, pci, phi)
            defaults_only: Only return default entity types

        Returns:
            List of entity types, ordered by category and ID
        """
        query = select(PIIEntityTypeModel)

        if category:
            query = query.where(PIIEntityTypeModel.category == category)

        if defaults_only:
            query = query.where(PIIEntityTypeModel.is_default.is_(True))

        query = query.order_by(PIIEntityTypeModel.category, PIIEntityTypeModel.id)

        result = await db.execute(query)
        orm_entities = list(result.scalars().all())

        return [
            PIIEntityTypeDTO(
                id=e.id,
                display_name=e.display_name,
                category=e.category,
                description=e.description,
                is_default=e.is_default,
            )
            for e in orm_entities
        ]
