"""Backfill canonical transcribe selector key in job parameters.

Revision ID: 0003_backfill_model_transcribe
Revises: 0002_drop_realtime_enhance_on_end
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_backfill_model_transcribe"
down_revision: Union[str, None] = "0002_drop_realtime_enhance_on_end"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _upgrade_parameters(parameters: Any) -> dict[str, Any] | None:
    """Return upgraded parameters dict, or None when no change is needed."""
    if not isinstance(parameters, dict):
        return None

    updated = dict(parameters)
    had_legacy_key = "engine_transcribe" in updated
    legacy_value = updated.pop("engine_transcribe", None)

    model_value = updated.get("model_transcribe")
    if legacy_value is not None and (model_value is None or model_value == ""):
        updated["model_transcribe"] = legacy_value

    return updated if had_legacy_key else None


def _downgrade_parameters(parameters: Any) -> dict[str, Any] | None:
    """Re-introduce legacy key from canonical key for downgrade safety."""
    if not isinstance(parameters, dict):
        return None

    if "engine_transcribe" in parameters:
        return None

    model_value = parameters.get("model_transcribe")
    if model_value is None or model_value == "":
        return None

    updated = dict(parameters)
    updated["engine_transcribe"] = model_value
    return updated


def _rewrite_job_parameters(
    transform: Callable[[Any], dict[str, Any] | None]
) -> None:
    jobs = sa.table(
        "jobs",
        sa.column("id"),
        sa.column("parameters", sa.JSON()),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.select(jobs.c.id, jobs.c.parameters)).all()

    for job_id, parameters in rows:
        updated = transform(parameters)
        if updated is None:
            continue
        bind.execute(
            sa.update(jobs).where(jobs.c.id == job_id).values(parameters=updated)
        )


def upgrade() -> None:
    _rewrite_job_parameters(_upgrade_parameters)


def downgrade() -> None:
    _rewrite_job_parameters(_downgrade_parameters)
