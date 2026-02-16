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


class EngineCapabilityError(Exception):
    """Raised when a running engine cannot handle the job's requirements.

    This error indicates a capability mismatch - the engine is running
    but doesn't support the job's specific requirements (e.g., language).

    Distinct from EngineUnavailableError (engine not running) to help
    operators diagnose whether to start a different engine.
    """

    def __init__(
        self,
        message: str,
        engine_id: str,
        stage: str,
        language: str | None = None,
    ) -> None:
        super().__init__(message)
        self.engine_id = engine_id
        self.stage = stage
        self.language = language


class CatalogValidationError(Exception):
    """Raised when job requirements cannot be met by any engine in the catalog.

    This is an early validation error - checked before queuing, before
    checking if any engines are running. Indicates a configuration issue:
    no engine in the catalog can handle the requested language/feature.
    """

    def __init__(
        self,
        message: str,
        stage: str | None = None,
        language: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.language = language
