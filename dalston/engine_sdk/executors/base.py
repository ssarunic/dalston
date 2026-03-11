"""Runtime executor contracts shared by local and lite execution paths."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


@dataclass(slots=True)
class ExecutionRequest:
    """Canonical request envelope for engine_id executors."""

    task_id: str
    job_id: str
    stage: str
    engine_id: str
    instance: str
    config: dict[str, Any]
    previous_outputs: dict[str, Any]
    payload: dict[str, Any] | None
    artifacts: dict[str, Path]
    engine: Engine[Any, Any] | None = None
    engine_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeExecutor(ABC):
    """Abstract execution boundary for a single task invocation."""

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        """Execute one task and return the canonical output envelope."""
