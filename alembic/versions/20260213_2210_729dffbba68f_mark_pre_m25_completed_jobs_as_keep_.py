"""Mark pre-M25 completed jobs as keep retention

Revision ID: 729dffbba68f
Revises: 0009
Create Date: 2026-02-13 22:10:36.292030
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = '729dffbba68f'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Jobs that completed before M25 have retention_mode='auto_delete' but no purge_after.
    # They will never be purged, so mark them as 'keep' to reflect reality.
    op.execute("""
        UPDATE jobs
        SET retention_mode = 'keep',
            retention_hours = NULL
        WHERE completed_at IS NOT NULL
          AND retention_mode = 'auto_delete'
          AND purge_after IS NULL
    """)


def downgrade() -> None:
    # Revert to auto_delete with 24h default (though purge_after will still be NULL)
    op.execute("""
        UPDATE jobs
        SET retention_mode = 'auto_delete',
            retention_hours = 24
        WHERE completed_at IS NOT NULL
          AND retention_mode = 'keep'
          AND purge_after IS NULL
    """)
