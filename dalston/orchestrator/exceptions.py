"""Orchestrator exceptions (M30: Enhanced with catalog context)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineInfo:
    """Summary of an engine for error context."""

    id: str
    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "languages": self.languages,
            "word_timestamps": self.supports_word_timestamps,
            "status": self.status,
        }


@dataclass
class ErrorDetails:
    """Detailed error context for API responses (M30)."""

    required: dict[str, Any] = field(default_factory=dict)
    available_engines: list[EngineInfo] = field(default_factory=list)
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "required": self.required,
            "available_engines": [e.to_dict() for e in self.available_engines],
            "suggestion": self.suggestion,
        }


class EngineUnavailableError(Exception):
    """Raised when a required engine is not available.

    This error indicates that a task cannot be queued because no healthy
    engine is registered to process it. The job should fail immediately
    with a clear error message rather than waiting for the task to timeout.
    """

    def __init__(
        self,
        message: str,
        engine_id: str,
        stage: str,
        details: ErrorDetails | None = None,
    ) -> None:
        super().__init__(message)
        self.engine_id = engine_id
        self.stage = stage
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON error response."""
        result: dict[str, Any] = {
            "error": "engine_unavailable",
            "message": str(self),
            "engine_id": self.engine_id,
            "stage": self.stage,
        }
        if self.details:
            result["details"] = self.details.to_dict()
        return result


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
        details: ErrorDetails | None = None,
    ) -> None:
        super().__init__(message)
        self.engine_id = engine_id
        self.stage = stage
        self.language = language
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON error response."""
        result: dict[str, Any] = {
            "error": "engine_capability_mismatch",
            "message": str(self),
            "engine_id": self.engine_id,
            "stage": self.stage,
        }
        if self.language:
            result["language"] = self.language
        if self.details:
            result["details"] = self.details.to_dict()
        return result


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
        details: ErrorDetails | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.language = language
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON error response."""
        result: dict[str, Any] = {
            "error": "catalog_validation_error",
            "message": str(self),
        }
        if self.stage:
            result["stage"] = self.stage
        if self.language:
            result["language"] = self.language
        if self.details:
            result["details"] = self.details.to_dict()
        return result


def build_engine_suggestion(
    stage: str,
    language: str | None,
    available_engines: list[EngineInfo],
) -> str | None:
    """Build a helpful suggestion based on available engines.

    Args:
        stage: The pipeline stage that needs an engine
        language: The requested language (if any)
        available_engines: List of engines that could potentially help

    Returns:
        A suggestion string, or None if no helpful suggestion can be made
    """
    if not available_engines:
        return f"No engines configured for stage '{stage}'. Check your deployment."

    # Find engines that support all languages
    all_lang_engines = [
        e for e in available_engines if e.languages is None and e.status != "running"
    ]

    # Find engines that support the specific language but aren't running
    if language:
        lang_engines = [
            e
            for e in available_engines
            if e.languages is not None
            and language.lower() in [lang.lower() for lang in e.languages]
            and e.status != "running"
        ]
    else:
        lang_engines = []

    suggestions = []

    if all_lang_engines:
        engine_names = ", ".join(e.id for e in all_lang_engines[:2])
        suggestions.append(f"Start {engine_names} (supports all languages)")

    if lang_engines and language:
        engine_names = ", ".join(e.id for e in lang_engines[:2])
        suggestions.append(f"Start {engine_names} (supports {language})")

    if not suggestions:
        # All capable engines are already running - suggest alternative
        if language:
            suggestions.append(
                f"Try a different language, or wait for an engine that supports '{language}'"
            )

    return " or ".join(suggestions) if suggestions else None
