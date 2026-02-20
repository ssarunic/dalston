"""Add webhook delivery dedupe constraints.

Ensures replayed job events do not create duplicate webhook delivery rows.

Revision ID: 0015
Revises: 0014
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Deduplicate endpoint-based deliveries (endpoint_id is set).
    conn.execute(
        sa.text(
            """
            DELETE FROM webhook_deliveries
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY job_id, event_type, endpoint_id
                               ORDER BY created_at ASC, id ASC
                           ) AS row_num
                    FROM webhook_deliveries
                    WHERE endpoint_id IS NOT NULL
                ) ranked
                WHERE row_num > 1
            )
            """
        )
    )

    # Deduplicate per-job URL deliveries (endpoint_id is NULL, url_override is set).
    conn.execute(
        sa.text(
            """
            DELETE FROM webhook_deliveries
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY job_id, event_type, url_override
                               ORDER BY created_at ASC, id ASC
                           ) AS row_num
                    FROM webhook_deliveries
                    WHERE endpoint_id IS NULL AND url_override IS NOT NULL
                ) ranked
                WHERE row_num > 1
            )
            """
        )
    )

    op.create_unique_constraint(
        "uq_webhook_deliveries_job_event_endpoint",
        "webhook_deliveries",
        ["job_id", "event_type", "endpoint_id"],
    )
    op.create_unique_constraint(
        "uq_webhook_deliveries_job_event_url",
        "webhook_deliveries",
        ["job_id", "event_type", "url_override"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_webhook_deliveries_job_event_url",
        "webhook_deliveries",
        type_="unique",
    )
    op.drop_constraint(
        "uq_webhook_deliveries_job_event_endpoint",
        "webhook_deliveries",
        type_="unique",
    )
