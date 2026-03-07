"""Remove unused realtime artifact type rules.

The REALTIME_TRANSCRIPT and REALTIME_EVENTS artifact types were placeholders
for hybrid mode (realtime sessions enhanced with batch processing). Since hybrid
mode was removed, these artifact types are never used and their retention rules
can be cleaned up.

Revision ID: 0017
Revises: 0016
Create Date: 2026-02-24

"""

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove realtime.transcript and realtime.events retention rules."""
    # Delete unused realtime artifact type rules from all templates
    op.execute("""
        DELETE FROM retention_template_rules
        WHERE artifact_type IN ('realtime.transcript', 'realtime.events')
    """)


def downgrade() -> None:
    """Re-add realtime artifact type rules to templates.

    Note: This restores the default rules. Custom templates may have had
    different values which cannot be recovered.
    """
    # Get template IDs for system templates
    op.execute("""
        INSERT INTO retention_template_rules (template_id, artifact_type, store, ttl_seconds)
        SELECT id, 'realtime.transcript', true, 86400
        FROM retention_templates WHERE name = 'default'
        UNION ALL
        SELECT id, 'realtime.events', false, NULL
        FROM retention_templates WHERE name = 'default'
        UNION ALL
        SELECT id, 'realtime.transcript', true, 0
        FROM retention_templates WHERE name = 'zero-retention'
        UNION ALL
        SELECT id, 'realtime.events', false, NULL
        FROM retention_templates WHERE name = 'zero-retention'
        UNION ALL
        SELECT id, 'realtime.transcript', true, NULL
        FROM retention_templates WHERE name = 'keep-forever'
        UNION ALL
        SELECT id, 'realtime.events', false, NULL
        FROM retention_templates WHERE name = 'keep-forever'
        UNION ALL
        SELECT id, 'realtime.transcript', false, NULL
        FROM retention_templates WHERE name = 'pii-compliant'
        UNION ALL
        SELECT id, 'realtime.events', false, NULL
        FROM retention_templates WHERE name = 'pii-compliant'
    """)
