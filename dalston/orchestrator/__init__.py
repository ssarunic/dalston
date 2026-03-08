"""Dalston Orchestrator package.

Keep package import side effects minimal so shared modules such as the engine
runner can import lightweight orchestrator submodules without pulling in the
distributed scheduler stack and its database dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ModelSelectionError",
    "NoCapableEngineError",
    "NoDownloadedModelError",
    "build_task_dag",
    "handle_job_created",
    "handle_task_completed",
    "handle_task_failed",
    "queue_task",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "build_task_dag": ("dalston.orchestrator.dag", "build_task_dag"),
    "ModelSelectionError": (
        "dalston.orchestrator.engine_selector",
        "ModelSelectionError",
    ),
    "NoCapableEngineError": (
        "dalston.orchestrator.engine_selector",
        "NoCapableEngineError",
    ),
    "NoDownloadedModelError": (
        "dalston.orchestrator.engine_selector",
        "NoDownloadedModelError",
    ),
    "handle_job_created": ("dalston.orchestrator.handlers", "handle_job_created"),
    "handle_task_completed": (
        "dalston.orchestrator.handlers",
        "handle_task_completed",
    ),
    "handle_task_failed": ("dalston.orchestrator.handlers", "handle_task_failed"),
    "queue_task": ("dalston.orchestrator.scheduler", "queue_task"),
}


def __getattr__(name: str) -> Any:
    """Resolve legacy package-level exports lazily."""
    if name in _EXPORTS:
        module_name, attr_name = _EXPORTS[name]
        module = import_module(module_name)
        return getattr(module, attr_name)

    try:
        return import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{name}":
            raise
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    """Expose lazy exports for interactive discovery."""
    return sorted(__all__)
