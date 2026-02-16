"""Engine catalog loader and validator.

The catalog is a static configuration file declaring all engines that could
be started in the system. It enables:
- Validation: Check job requirements before engines are running
- Auto-scaling (future): Know which images to boot for pending jobs

The catalog answers "what could I start?" while the registry (Redis heartbeats)
answers "what's running?"

M30: Catalog is now generated from engine.yaml files. The generated_catalog.json
is the primary source, with engine_catalog.yaml as legacy fallback.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()

# Default catalog paths (relative to this module)
DEFAULT_CATALOG_PATH = Path(__file__).parent / "generated_catalog.json"
LEGACY_CATALOG_PATH = Path(__file__).parent / "engine_catalog.yaml"


@dataclass
class CatalogEntry:
    """An engine entry in the catalog.

    Extends EngineCapabilities with deployment metadata (image name).
    """

    engine_id: str
    image: str
    capabilities: EngineCapabilities


class EngineCatalog:
    """Static catalog of deployable engines.

    Loaded from YAML at orchestrator startup. Used for early validation
    of job requirements before checking if engines are actually running.

    Example:
        catalog = EngineCatalog.load()

        # Check if any engine in catalog supports Croatian transcription
        engines = catalog.get_engines_for_stage("transcribe")
        supports_hr = any(
            e.capabilities.languages is None or "hr" in e.capabilities.languages
            for e in engines
        )
    """

    def __init__(self, entries: dict[str, CatalogEntry]) -> None:
        """Initialize catalog with entries.

        Args:
            entries: Map of engine_id to CatalogEntry
        """
        self._entries = entries

    @classmethod
    def load(cls, path: Path | str | None = None) -> EngineCatalog:
        """Load catalog from JSON or YAML file.

        Args:
            path: Path to catalog file. Defaults to generated_catalog.json,
                  falls back to legacy engine_catalog.yaml if not found.

        Returns:
            EngineCatalog instance

        Raises:
            FileNotFoundError: If catalog file doesn't exist
            json.JSONDecodeError: If JSON catalog file is invalid
            yaml.YAMLError: If YAML catalog file is invalid
        """
        if path is None:
            path = DEFAULT_CATALOG_PATH
            # Fall back to legacy YAML if generated JSON doesn't exist
            if not path.exists() and LEGACY_CATALOG_PATH.exists():
                warnings.warn(
                    "Using legacy engine_catalog.yaml. Run 'python scripts/generate_catalog.py' "
                    "to generate the new catalog format.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                path = LEGACY_CATALOG_PATH
        path = Path(path)

        logger.info("loading_engine_catalog", path=str(path))

        with open(path) as f:
            if path.suffix == ".json":
                data = json.load(f)
            else:
                data = yaml.safe_load(f)

        entries: dict[str, CatalogEntry] = {}
        engines_data = data.get("engines", {})

        for engine_id, engine_data in engines_data.items():
            # Handle both new (nested capabilities) and legacy (flat) formats
            if "capabilities" in engine_data and isinstance(
                engine_data["capabilities"], dict
            ):
                # New generated catalog format
                caps_data = engine_data["capabilities"]
                hw_data = engine_data.get("hardware", {})
                perf_data = engine_data.get("performance", {})

                capabilities = EngineCapabilities(
                    engine_id=engine_id,
                    version=engine_data.get("version", "unknown"),
                    stages=caps_data.get("stages", []),
                    languages=caps_data.get("languages"),
                    supports_word_timestamps=caps_data.get(
                        "supports_word_timestamps", False
                    ),
                    supports_streaming=caps_data.get("supports_streaming", False),
                    model_variants=None,
                    gpu_required=hw_data.get("gpu_required", False),
                    gpu_vram_mb=(
                        hw_data.get("min_vram_gb", 0) * 1024
                        if hw_data.get("min_vram_gb")
                        else None
                    ),
                    supports_cpu=hw_data.get("supports_cpu", True),
                    min_ram_gb=hw_data.get("min_ram_gb"),
                    rtf_gpu=perf_data.get("rtf_gpu"),
                    rtf_cpu=perf_data.get("rtf_cpu"),
                    max_concurrent_jobs=perf_data.get("max_concurrent_jobs"),
                )
            else:
                # Legacy YAML format
                capabilities = EngineCapabilities(
                    engine_id=engine_id,
                    version="catalog",
                    stages=engine_data.get("stages", []),
                    languages=engine_data.get("languages"),
                    supports_word_timestamps=engine_data.get(
                        "supports_word_timestamps", False
                    ),
                    supports_streaming=engine_data.get("supports_streaming", False),
                    model_variants=engine_data.get("model_variants"),
                    gpu_required=engine_data.get("gpu_required", False),
                    gpu_vram_mb=engine_data.get("gpu_vram_mb"),
                )

            entries[engine_id] = CatalogEntry(
                engine_id=engine_id,
                image=engine_data.get("image", f"dalston/{engine_id}:latest"),
                capabilities=capabilities,
            )

        logger.info("engine_catalog_loaded", engine_count=len(entries))
        return cls(entries)

    def get_engine(self, engine_id: str) -> CatalogEntry | None:
        """Get a specific engine entry.

        Args:
            engine_id: Engine identifier

        Returns:
            CatalogEntry if found, None otherwise
        """
        return self._entries.get(engine_id)

    def get_all_engines(self) -> list[CatalogEntry]:
        """Get all engines in the catalog.

        Returns:
            List of all CatalogEntry objects
        """
        return list(self._entries.values())

    def get_engines_for_stage(self, stage: str) -> list[CatalogEntry]:
        """Get all engines that handle a specific pipeline stage.

        Args:
            stage: Pipeline stage (e.g., "transcribe", "diarize")

        Returns:
            List of engines for the given stage
        """
        return [e for e in self._entries.values() if stage in e.capabilities.stages]

    def find_engines_supporting_language(
        self, stage: str, language: str
    ) -> list[CatalogEntry]:
        """Find engines for a stage that support a specific language.

        Args:
            stage: Pipeline stage
            language: ISO 639-1 language code

        Returns:
            List of engines that support the language for the stage
        """
        result = []
        for entry in self.get_engines_for_stage(stage):
            caps = entry.capabilities
            # None means all languages
            if caps.languages is None:
                result.append(entry)
            elif language.lower() in [lang.lower() for lang in caps.languages]:
                result.append(entry)
        return result

    def validate_language_support(self, stage: str, language: str) -> str | None:
        """Check if any engine in catalog supports a language for a stage.

        Args:
            stage: Pipeline stage
            language: ISO 639-1 language code

        Returns:
            None if supported, error message if not supported
        """
        engines = self.find_engines_supporting_language(stage, language)
        if not engines:
            available_engines = self.get_engines_for_stage(stage)
            if not available_engines:
                return f"No engine in catalog handles stage '{stage}'"
            return (
                f"No engine in catalog supports language '{language}' "
                f"for stage '{stage}'. Available engines: "
                f"{[e.engine_id for e in available_engines]}"
            )
        return None

    def __len__(self) -> int:
        """Return number of engines in catalog."""
        return len(self._entries)

    def __contains__(self, engine_id: str) -> bool:
        """Check if engine is in catalog."""
        return engine_id in self._entries


# Module-level singleton for shared access
_catalog: EngineCatalog | None = None


def get_catalog(path: Path | str | None = None) -> EngineCatalog:
    """Get or load the engine catalog singleton.

    Args:
        path: Optional path to catalog file. Only used on first load.

    Returns:
        EngineCatalog instance
    """
    global _catalog
    if _catalog is None:
        _catalog = EngineCatalog.load(path)
    return _catalog


def reload_catalog(path: Path | str | None = None) -> EngineCatalog:
    """Force reload the engine catalog.

    Args:
        path: Optional path to catalog file.

    Returns:
        New EngineCatalog instance
    """
    global _catalog
    _catalog = EngineCatalog.load(path)
    return _catalog
