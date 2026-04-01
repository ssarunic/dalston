"""Shared engine.yaml loading and capabilities parsing.

Used by both the batch engine SDK and the realtime engine SDK to locate
and parse the engine.yaml configuration file.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

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


def parse_engine_capabilities(
    card: dict[str, Any],
    *,
    default_engine_id: str = "unknown",
    default_stages: list[str] | None = None,
    supports_native_streaming: bool = False,
    max_concurrency: int | None = None,
    vocabulary_support: Any = None,
) -> dict[str, Any]:
    """Parse an engine.yaml dict into kwargs for ``EngineCapabilities``.

    Returns a plain dict so that ``dalston.common`` does not depend on
    ``dalston.engine_sdk``.  Callers construct the Pydantic model::

        EngineCapabilities(**parse_engine_capabilities(card, ...))

    Args:
        card: Parsed engine.yaml dictionary.
        default_engine_id: Fallback engine_id when card has none.
        default_stages: Fallback stage list when card has no stage field.
        supports_native_streaming: Fallback for native_streaming capability.
        max_concurrency: Fallback max concurrency.
        vocabulary_support: VocabularySupport instance (realtime only).
    """
    if default_stages is None:
        default_stages = []

    caps = card.get("capabilities", {})
    hardware = card.get("hardware", {})
    performance = card.get("performance", {})

    # Determine GPU requirement: container.gpu is authoritative when present;
    # otherwise infer from hardware metadata.
    container = card.get("container", {})
    gpu_field = container.get("gpu")
    if gpu_field is None:
        min_vram_gb = hardware.get("min_vram_gb")
        supports_cpu_val = hardware.get("supports_cpu", True)
        gpu_required = bool(min_vram_gb and not supports_cpu_val)
    else:
        gpu_required = gpu_field == "required"

    stage = card.get("stage")
    stages = [stage] if stage else default_stages

    result: dict[str, Any] = {
        "engine_id": card.get("engine_id") or card.get("id", default_engine_id),
        "version": card.get("version", "unknown"),
        "stages": stages,
        "supports_word_timestamps": caps.get("word_timestamps", False),
        "supports_native_streaming": caps.get(
            "native_streaming", supports_native_streaming
        ),
        "gpu_required": gpu_required,
        "gpu_vram_mb": (
            hardware.get("min_vram_gb", 0) * 1024
            if hardware.get("min_vram_gb")
            else None
        ),
        "supports_cpu": hardware.get("supports_cpu", True),
        "min_ram_gb": hardware.get("min_ram_gb"),
        "rtf_gpu": performance.get("rtf_gpu"),
        "rtf_cpu": performance.get("rtf_cpu"),
        "max_concurrency": caps.get("max_concurrency", max_concurrency),
    }
    if vocabulary_support is not None:
        result["vocabulary_support"] = vocabulary_support
    return result


def generate_instance_id(engine_id: str, infix: str = "") -> str:
    """Generate a unique instance identifier for registry keys.

    Priority: ``DALSTON_WORKER_ID`` > ``DALSTON_INSTANCE`` > random UUID.
    The result is ``{engine_id}[-{infix}]-{suffix[:12]}``.

    Used by both the batch runner and the realtime engine base class.
    """
    stable_id = os.environ.get("DALSTON_WORKER_ID") or os.environ.get(
        "DALSTON_INSTANCE"
    )
    suffix = (stable_id or uuid4().hex)[:12]
    sep = f"-{infix}-" if infix else "-"
    return f"{engine_id}{sep}{suffix}"


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether a TCP port is already bound.

    Used by both the batch and realtime runners to skip binding
    the metrics/HTTP port when the other side of a unified engine
    already occupies it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex((host, port)) == 0
