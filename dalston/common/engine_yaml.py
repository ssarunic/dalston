"""Shared engine.yaml loading utilities.

Used by both the batch engine SDK and the realtime engine SDK to locate
and parse the engine.yaml configuration file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]

logger = structlog.get_logger()

# Paths for engine.yaml (container path first, local fallback second)
ENGINE_YAML_PATHS = [
    Path("/etc/dalston/engine.yaml"),
    Path("engine.yaml"),
]


def load_engine_yaml() -> dict[str, Any] | None:
    """Load engine.yaml from known paths.

    Searches ``ENGINE_YAML_PATHS`` in order and returns the first
    successfully parsed file, or ``None`` if none are found.
    """
    for path in ENGINE_YAML_PATHS:
        if path.exists():
            try:
                with open(path) as f:
                    return yaml.safe_load(f)
            except Exception as e:
                logger.warning(
                    "failed_to_load_engine_yaml",
                    path=str(path),
                    error=str(e),
                )
    return None
