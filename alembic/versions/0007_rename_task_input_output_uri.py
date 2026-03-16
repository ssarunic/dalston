"""Rename tasks.input_uri -> request_uri, output_uri -> response_uri.

Revision ID: 0007_rename_task_input_output_uri
Revises: 0006_rename_streaming_to_native_streaming
Create Date: 2026-03-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_rename_task_input_output_uri"
down_revision: str = "0006_rename_streaming_to_native_streaming"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tasks", "input_uri", new_column_name="request_uri")
    op.alter_column("tasks", "output_uri", new_column_name="response_uri")


def downgrade() -> None:
    op.alter_column("tasks", "request_uri", new_column_name="input_uri")
    op.alter_column("tasks", "response_uri", new_column_name="output_uri")
