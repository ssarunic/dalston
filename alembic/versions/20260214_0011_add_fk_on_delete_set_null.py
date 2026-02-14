"""Add ON DELETE SET NULL to retention_policy_id foreign keys.

Provides defense-in-depth for retention policy deletion. While the
service layer checks for in-use policies before deletion, this ensures
FK violations cannot occur if a policy is deleted while jobs reference it.

Revision ID: 0011
Revises: 0010
Create Date: 2026-02-14
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop existing FK constraints (using actual constraint names from database)
    op.drop_constraint("jobs_retention_policy_id_fkey", "jobs", type_="foreignkey")
    op.drop_constraint(
        "realtime_sessions_retention_policy_id_fkey",
        "realtime_sessions",
        type_="foreignkey",
    )

    # Recreate with ON DELETE SET NULL
    op.create_foreign_key(
        "fk_jobs_retention_policy_id",
        "jobs",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_realtime_sessions_retention_policy_id",
        "realtime_sessions",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Drop FK constraints with ON DELETE SET NULL
    op.drop_constraint("fk_jobs_retention_policy_id", "jobs", type_="foreignkey")
    op.drop_constraint(
        "fk_realtime_sessions_retention_policy_id",
        "realtime_sessions",
        type_="foreignkey",
    )

    # Recreate without ON DELETE behavior
    op.create_foreign_key(
        "fk_jobs_retention_policy_id",
        "jobs",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_realtime_sessions_retention_policy_id",
        "realtime_sessions",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )
