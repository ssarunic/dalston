"""Execution context for stateless batch engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from dalston.common.artifacts import ProducedArtifact


@dataclass(slots=True)
class BatchTaskContext:
    """Runtime context passed to engine process methods.

    The context intentionally excludes storage side-effect methods.
    """

    engine_id: str
    instance: str
    task_id: str
    job_id: str
    stage: str
    metadata: dict[str, Any] = field(default_factory=dict)
    logger: structlog.stdlib.BoundLogger = field(
        default_factory=structlog.get_logger  # type: ignore[arg-type]
    )

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Return engine_id metadata value."""
        return self.metadata.get(key, default)

    def describe_artifact(
        self,
        *,
        logical_name: str,
        local_path: Path,
        kind: str,
        channel: int | None = None,
        role: str | None = None,
        media_type: str | None = None,
    ) -> ProducedArtifact:
        """Construct an artifact descriptor without performing side effects."""
        return ProducedArtifact(
            logical_name=logical_name,
            local_path=local_path,
            kind=kind,
            channel=channel,
            role=role,
            media_type=media_type,
        )
