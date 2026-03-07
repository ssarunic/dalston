"""Drop pii_detection_tier column from jobs table.

PII detection tiers (fast/standard/thorough) have been removed. All PII
detection now uses GLiNER as the primary detector, supplemented by Presidio
for checksum-validated patterns only. The tier selection is no longer needed.

Revision ID: 0023
Revises: 0022
Create Date: 2026-02-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("jobs", "pii_detection_tier")


def downgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("pii_detection_tier", sa.String(20), nullable=True),
    )
