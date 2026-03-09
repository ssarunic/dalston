"""Shared engine reference loader for local and isolated execution paths."""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from dalston.engine_sdk.base import Engine


def load_engine(engine_ref: str) -> Engine[Any, Any]:
    """Load and instantiate an engine class from ``<module:Class>`` reference."""
    if ":" not in engine_ref:
        raise ValueError(
            f"Engine reference must use '<module:Class>' format, got: {engine_ref}"
        )

    module_name, class_name = engine_ref.split(":", maxsplit=1)
    module = _import_engine_module(module_name)
    engine_type = getattr(module, class_name)

    if not isinstance(engine_type, type) or not issubclass(engine_type, Engine):
        raise TypeError(f"Engine class must inherit from Engine: {engine_ref}")

    return engine_type()


def _import_engine_module(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as import_error:
        module_path = _resolve_engine_module_path(module_name)
        if module_path is None:
            raise import_error

        loader_name = "dalston_engine_loader_" + re.sub(
            r"[^a-zA-Z0-9_]", "_", str(module_path)
        )
        spec = importlib.util.spec_from_file_location(loader_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Unable to import engine module from path: {module_path}"
            ) from import_error

        module = importlib.util.module_from_spec(spec)
        sys.modules[loader_name] = module
        search_path = str(module_path.parent)
        path_added = False
        if search_path not in sys.path:
            sys.path.insert(0, search_path)
            path_added = True
        try:
            spec.loader.exec_module(module)
        finally:
            if path_added:
                sys.path.remove(search_path)
        return module


def _resolve_engine_module_path(module_name: str) -> Path | None:
    if "/" in module_name or module_name.endswith(".py"):
        candidate = Path(module_name)
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    candidate = Path(*module_name.split(".")).with_suffix(".py")
    if candidate.exists() and candidate.is_file():
        return candidate

    # Handle runtime IDs that include dots, for example:
    # engines.stt-diarize.pyannote-4.0.engine -> engines/stt-diarize/pyannote-4.0/engine.py
    parts = module_name.split(".")
    if len(parts) >= 4 and parts[0] == "engines" and parts[-1] == "engine":
        stage = parts[1]
        runtime = ".".join(parts[2:-1])
        runtime_candidate = Path("engines") / stage / runtime / "engine.py"
        if runtime_candidate.exists() and runtime_candidate.is_file():
            return runtime_candidate

    return None
