"""Configuration loading for Dalston CLI.

Supports configuration from:
1. Default values
2. Config file (~/.dalston/config.yaml)
3. Environment variables
4. CLI arguments (handled by Click)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def load_config() -> dict[str, Any]:
    """Load configuration from file and environment.

    Priority (lowest to highest):
    1. Default values
    2. Config file
    3. Environment variables

    Returns:
        Configuration dictionary.
    """
    config: dict[str, Any] = {
        "server": "http://localhost:8000",
        "api_key": None,
        "defaults": {
            "language": "auto",
            "format": "txt",
            "speakers": "none",
        },
    }

    # Load from config file
    config_path = Path.home() / ".dalston" / "config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                file_config = yaml.safe_load(f)
                if file_config:
                    _merge_config(config, file_config)
        except ImportError:
            # yaml not installed, skip config file
            pass
        except Exception:
            # Ignore config file errors
            pass

    # Override with environment variables
    if os.environ.get("DALSTON_SERVER"):
        config["server"] = os.environ["DALSTON_SERVER"]
    if os.environ.get("DALSTON_API_KEY"):
        config["api_key"] = os.environ["DALSTON_API_KEY"]

    return config


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Merge override config into base config."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge_config(base[key], value)
        else:
            base[key] = value


def get_default(config: dict[str, Any], key: str, fallback: Any = None) -> Any:
    """Get a default value from config.

    Args:
        config: Configuration dictionary.
        key: Key to look up in defaults.
        fallback: Value to return if not found.

    Returns:
        The default value or fallback.
    """
    return config.get("defaults", {}).get(key, fallback)
