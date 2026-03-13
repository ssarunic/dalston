"""Engine catalog loader.

The catalog is a JSON file declaring all engine_ids (engines) that could be used
in the system. It enables:
- Validation: Check job requirements before engines are running
- Auto-scaling (future): Know which images to boot for pending jobs

The catalog answers "what could I start?" while the registry (Redis heartbeats)
answers "what's running?"

M30: Catalog is generated from engine.yaml files at build time using
'python scripts/generate_catalog.py'. Each engine's metadata lives in its
engine.yaml file (single source of truth).

M46: Model metadata has moved to the database (ModelRegistryModel). Use
ModelRegistryService for model lookups. The catalog now only handles engines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()

# Default catalog path (relative to this module)
DEFAULT_CATALOG_PATH = Path(__file__).parent / "generated_catalog.json"
ExecutionProfile = Literal["inproc", "venv", "container"]
DEFAULT_EXECUTION_PROFILE: ExecutionProfile = "container"
_VALID_EXECUTION_PROFILES = frozenset({"inproc", "venv", "container"})


@dataclass
class CatalogEntry:
    """A engine_id entry in the catalog.

    Extends EngineCapabilities with deployment metadata (image name).
    """

    engine_id: str
    image: str
    capabilities: EngineCapabilities
    execution_profile: ExecutionProfile = DEFAULT_EXECUTION_PROFILE


class EngineCatalog:
    """Static catalog of deployable engines.

    Loaded from JSON at orchestrator startup. Used for early validation
    of job requirements before checking if engines are actually running.

    M46: Model metadata has moved to the database. Use ModelRegistryService
    for model lookups. This catalog now only handles engine capabilities.

    Example:
        catalog = EngineCatalog.load()

        # Get all transcription engines
        engines = catalog.get_engines_for_stage("transcribe")
    """

    def __init__(self, entries: dict[str, CatalogEntry]) -> None:
        """Initialize catalog with engine entries.

        Args:
            entries: Map of engine_id to CatalogEntry
        """
        self._entries = entries

    @classmethod
    def load(cls, path: Path | str | None = None) -> EngineCatalog:
        """Load catalog from JSON file.

        Args:
            path: Path to catalog file. Defaults to generated_catalog.json.

        Returns:
            EngineCatalog instance

        Raises:
            FileNotFoundError: If catalog file doesn't exist. Run
                'python scripts/generate_catalog.py' to generate it.
            json.JSONDecodeError: If JSON catalog file is invalid
        """
        if path is None:
            path = DEFAULT_CATALOG_PATH
        path = Path(path)

        logger.info("loading_engine_catalog", path=str(path))

        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Engine catalog not found at {path}. "
                "Run 'python scripts/generate_catalog.py' to generate it from engine.yaml files."
            ) from None

        # Load engine/engine_id entries with strict validation
        entries: dict[str, CatalogEntry] = {}
        engines_data = data.get("engines", {})
        parse_errors: list[str] = []

        for engine_id, engine_data in engines_data.items():
            try:
                entry = cls._parse_engine_entry(engine_id, engine_data)
                entries[engine_id] = entry
            except (KeyError, TypeError, ValueError) as e:
                parse_errors.append(f"engine_id '{engine_id}': {e}")

        if parse_errors:
            raise ValueError(
                f"Engine catalog has {len(parse_errors)} invalid entries. "
                f"Regenerate with 'python scripts/generate_catalog.py'.\n"
                + "\n".join(f"  - {err}" for err in parse_errors)
            )

        logger.info("engine_catalog_loaded", engine_count=len(entries))
        return cls(entries)

    # =========================================================================
    # Parsing helpers (strict validation)
    # =========================================================================

    @classmethod
    def _parse_engine_entry(cls, engine_id: str, engine_data: dict) -> CatalogEntry:
        """Parse and validate a single engine entry.

        Raises:
            KeyError: Missing required field
            TypeError: Field has wrong type
            ValueError: Field has invalid value
        """
        caps_data = engine_data.get("capabilities", {})
        hw_data = engine_data.get("hardware", {})
        perf_data = engine_data.get("performance", {})

        # Validate required fields
        stages = caps_data.get("stages")
        if stages is None:
            raise KeyError("capabilities.stages is required")
        if not isinstance(stages, list):
            raise TypeError(
                f"capabilities.stages must be a list, got {type(stages).__name__}"
            )
        if not stages:
            raise ValueError("capabilities.stages cannot be empty")

        capabilities = EngineCapabilities(
            engine_id=engine_id,
            version=engine_data.get("version", "unknown"),
            stages=stages,
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
            max_concurrency=caps_data.get("max_concurrency"),
            includes_diarization=caps_data.get("includes_diarization", False),
        )
        execution_profile = engine_data.get(
            "execution_profile", DEFAULT_EXECUTION_PROFILE
        )
        if not isinstance(execution_profile, str):
            raise TypeError(
                "execution_profile must be a string, "
                f"got {type(execution_profile).__name__}"
            )
        if execution_profile not in _VALID_EXECUTION_PROFILES:
            valid = ", ".join(sorted(_VALID_EXECUTION_PROFILES))
            raise ValueError(
                f"execution_profile must be one of {{{valid}}}, got "
                f"{execution_profile!r}"
            )

        return CatalogEntry(
            engine_id=engine_id,
            image=engine_data.get("image", f"dalston/{engine_id}:latest"),
            execution_profile=execution_profile,
            capabilities=capabilities,
        )

    # =========================================================================
    # Engine methods
    # =========================================================================

    def get_engine(self, engine_id: str) -> CatalogEntry | None:
        """Get a specific engine entry.

        Args:
            engine_id: Runtime identifier

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

    def find_engines(self, stage: str, requirements: dict) -> list[CatalogEntry]:
        """Find catalog engines that could satisfy requirements (M31).

        Used by the engine selector to identify alternatives when no running
        engine matches, enabling actionable error messages.

        Args:
            stage: Pipeline stage (e.g., "transcribe", "diarize")
            requirements: Dict of requirements to match:
                - streaming: bool (optional)

        Returns:
            List of CatalogEntry objects that match all requirements
        """
        result = []
        for entry in self.get_engines_for_stage(stage):
            if self._matches_requirements(entry.capabilities, requirements):
                result.append(entry)
        return result

    def _matches_requirements(
        self, caps: EngineCapabilities, requirements: dict
    ) -> bool:
        """Check if capabilities satisfy requirements.

        Args:
            caps: Engine capabilities to check
            requirements: Requirements dict

        Returns:
            True if all requirements are satisfied
        """
        # Streaming check
        if requirements.get("streaming") and not caps.supports_streaming:
            return False

        return True

    # =========================================================================
    # Dunder methods
    # =========================================================================

    def __len__(self) -> int:
        """Return number of engines in catalog."""
        return len(self._entries)

    def __contains__(self, engine_id: str) -> bool:
        """Check if engine_id is in catalog."""
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
