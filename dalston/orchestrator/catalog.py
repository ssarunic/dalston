"""Engine catalog loader and validator.

The catalog is a static configuration file declaring all engines that could
be started in the system. It enables:
- Validation: Check job requirements before engines are running
- Auto-scaling (future): Know which images to boot for pending jobs

The catalog answers "what could I start?" while the registry (Redis heartbeats)
answers "what's running?"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()

# Default catalog path (relative to this module)
DEFAULT_CATALOG_PATH = Path(__file__).parent / "engine_catalog.yaml"


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
        """Load catalog from YAML file.

        Args:
            path: Path to catalog YAML. Defaults to engine_catalog.yaml in this directory.

        Returns:
            EngineCatalog instance

        Raises:
            FileNotFoundError: If catalog file doesn't exist
            yaml.YAMLError: If catalog file is invalid YAML
        """
        if path is None:
            path = DEFAULT_CATALOG_PATH
        path = Path(path)

        logger.info("loading_engine_catalog", path=str(path))

        with open(path) as f:
            data = yaml.safe_load(f)

        entries: dict[str, CatalogEntry] = {}
        engines_data = data.get("engines", {})

        for engine_id, engine_data in engines_data.items():
            capabilities = EngineCapabilities(
                engine_id=engine_id,
                version="catalog",  # Catalog doesn't track versions
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
