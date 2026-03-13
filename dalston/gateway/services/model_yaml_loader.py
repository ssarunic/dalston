"""Load model definitions from YAML files.

This module provides direct loading of model YAML files for database seeding,
bypassing the intermediate JSON catalog generation step.

Usage:
    entries = load_model_yamls()
    for entry in entries:
        print(f"{entry.id}: {entry.engine_id}")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger()


def _get_default_models_dir() -> Path:
    """Get the default models directory.

    Checks these locations in order:
    1. DALSTON_MODELS_DIR environment variable
    2. /app/models (Docker container path)
    3. Relative to repo root (for local development)
    """
    import os

    # Check environment variable first
    env_path = os.environ.get("DALSTON_MODELS_DIR")
    if env_path:
        return Path(env_path)

    # Docker container path
    docker_path = Path("/app/models")
    if docker_path.exists():
        return docker_path

    # Local development: relative to repo root
    # Path: dalston/gateway/services/model_yaml_loader.py -> up 4 levels -> models/
    return Path(__file__).parents[4] / "models"


DEFAULT_MODELS_DIR = _get_default_models_dir()


@dataclass
class ModelYAMLEntry:
    """Parsed model YAML entry.

    Contains all metadata needed to create a ModelRegistryModel database entry.
    """

    id: str
    engine_id: str
    loaded_model_id: str
    name: str
    stage: str
    source: str | None = None
    size_gb: float | None = None
    languages: list[str] | None = None
    word_timestamps: bool = False
    punctuation: bool = False
    capitalization: bool = False
    native_streaming: bool = False
    min_vram_gb: float | None = None
    min_ram_gb: float | None = None
    supports_cpu: bool = False
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None


def load_model_yamls(models_dir: Path | None = None) -> list[ModelYAMLEntry]:
    """Load and validate all model YAML files.

    Args:
        models_dir: Directory containing model YAMLs. Defaults to repo/models/

    Returns:
        List of parsed model entries

    Raises:
        ValueError: If any YAML is invalid (fail-closed)
        FileNotFoundError: If models directory doesn't exist
    """
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    if not models_dir.exists():
        raise FileNotFoundError(f"Models directory not found: {models_dir}")

    entries = []
    errors = []

    for yaml_path in sorted(models_dir.glob("*.yaml")):
        try:
            entry = _load_single_yaml(yaml_path)
            entries.append(entry)
        except Exception as e:
            errors.append(f"{yaml_path.name}: {e}")

    if errors:
        raise ValueError(
            f"Failed to parse {len(errors)} model YAML file(s):\n"
            + "\n".join(f"  - {err}" for err in errors)
        )

    logger.info("model_yamls_loaded", count=len(entries))
    return entries


def _load_single_yaml(yaml_path: Path) -> ModelYAMLEntry:
    """Load and parse a single model YAML file.

    Args:
        yaml_path: Path to the YAML file

    Returns:
        Parsed ModelYAMLEntry

    Raises:
        ValueError: If required fields are missing
        yaml.YAMLError: If YAML is malformed
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("YAML must be a dictionary")

    # Required fields
    model_id = data.get("id")
    if not model_id:
        raise ValueError("Missing required field: id")

    engine_id = data.get("engine_id")
    if not engine_id:
        raise ValueError("Missing required field: engine_id")

    loaded_model_id = data.get("loaded_model_id")
    if not loaded_model_id:
        raise ValueError("Missing required field: loaded_model_id")

    # Normalize languages: ["all"] or null means multilingual
    languages = data.get("languages")
    if languages == ["all"]:
        languages = None

    # Extract nested fields
    caps = data.get("capabilities", {})
    hardware = data.get("hardware", {})
    performance = data.get("performance", {})

    return ModelYAMLEntry(
        id=model_id,
        engine_id=engine_id,
        loaded_model_id=loaded_model_id,
        name=data.get("name", model_id),
        stage=data.get("stage", "transcribe"),
        source=data.get("source"),
        size_gb=data.get("size_gb"),
        languages=languages,
        word_timestamps=caps.get("word_timestamps", False),
        punctuation=caps.get("punctuation", False),
        capitalization=caps.get("capitalization", False),
        native_streaming=caps.get("native_streaming", False),
        min_vram_gb=hardware.get("min_vram_gb"),
        min_ram_gb=hardware.get("min_ram_gb"),
        supports_cpu=hardware.get("supports_cpu", False),
        rtf_gpu=performance.get("rtf_gpu"),
        rtf_cpu=performance.get("rtf_cpu"),
    )
