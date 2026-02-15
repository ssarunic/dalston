"""Orchestrator exceptions."""

from __future__ import annotations


class EngineUnavailableError(Exception):
    """Raised when a required engine is not available.

    This error indicates that a task cannot be queued because no healthy
    engine is registered to process it. The job should fail immediately
    with a clear error message rather than waiting for the task to timeout.
    """

    def __init__(self, message: str, engine_id: str, stage: str) -> None:
        super().__init__(message)
        self.engine_id = engine_id
        self.stage = stage
