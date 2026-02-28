"""Engine and model catalog loader.

The catalog is a JSON file declaring all runtimes and models that could be used
in the system. It enables:
- Validation: Check job requirements before engines are running
- Model routing: Map model IDs to runtimes and native model identifiers
- Auto-scaling (future): Know which images to boot for pending jobs

The catalog answers "what could I start?" while the registry (Redis heartbeats)
answers "what's running?"

M30: Catalog is generated from engine.yaml files at build time using
'python scripts/generate_catalog.py'. Each engine's metadata lives in its
engine.yaml file (single source of truth).

M36: Catalog now includes a models section with runtime mappings. The catalog
provides methods to resolve model IDs to runtimes and native model identifiers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import structlog

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()

# Default catalog path (relative to this module)
DEFAULT_CATALOG_PATH = Path(__file__).parent / "generated_catalog.json"


@dataclass
class CatalogEntry:
    """An engine/runtime entry in the catalog.

    Extends EngineCapabilities with deployment metadata (image name).
    """

    engine_id: str
    image: str
    capabilities: EngineCapabilities


@dataclass
class ModelEntry:
    """A model entry in the catalog (M36).

    Maps a Dalston public model ID to its runtime and native model identifier.

    Attributes:
        id: Dalston's public model ID (e.g., "parakeet-tdt-1.1b")
        runtime: Runtime that loads this model (e.g., "nemo")
        runtime_model_id: Native model ID for loading (e.g., "nvidia/parakeet-tdt-1.1b")
        name: Human-readable name
        source: Download source (e.g., HuggingFace model ID)
        size_gb: Estimated model size in GB
        stage: Pipeline stage this model serves
        languages: Supported languages (None means multilingual)
        word_timestamps: Whether model produces word-level timestamps
        supports_cpu: Whether model can run on CPU
    """

    id: str
    runtime: str
    runtime_model_id: str
    name: str
    source: str | None = None
    size_gb: float | None = None
    stage: str | None = None
    languages: list[str] | None = None
    word_timestamps: bool = False
    punctuation: bool = False
    capitalization: bool = False
    supports_cpu: bool = False
    min_vram_gb: int | None = None
    min_ram_gb: int | None = None
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None


class EngineCatalog:
    """Static catalog of deployable engines and models.

    Loaded from JSON at orchestrator startup. Used for early validation
    of job requirements before checking if engines are actually running.

    M36: Now supports both runtime and model lookups.

    Example:
        catalog = EngineCatalog.load()

        # Get model info for routing
        model = catalog.get_model("parakeet-tdt-1.1b")
        runtime = model.runtime  # "nemo"
        native_id = model.runtime_model_id  # "nvidia/parakeet-tdt-1.1b"

        # Check if any engine in catalog supports Croatian transcription
        engines = catalog.get_engines_for_stage("transcribe")
        supports_hr = any(
            e.capabilities.languages is None or "hr" in e.capabilities.languages
            for e in engines
        )
    """

    def __init__(
        self,
        entries: dict[str, CatalogEntry],
        models: dict[str, ModelEntry] | None = None,
    ) -> None:
        """Initialize catalog with entries.

        Args:
            entries: Map of engine_id to CatalogEntry
            models: Map of model_id to ModelEntry (M36)
        """
        self._entries = entries
        self._models = models or {}

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

        # Load engine/runtime entries (backward compatible)
        entries: dict[str, CatalogEntry] = {}
        engines_data = data.get("engines", {})

        for engine_id, engine_data in engines_data.items():
            caps_data = engine_data.get("capabilities", {})
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
                max_concurrency=caps_data.get("max_concurrency"),
                # M31: includes_diarization for capability-driven routing
                includes_diarization=caps_data.get("includes_diarization", False),
            )

            entries[engine_id] = CatalogEntry(
                engine_id=engine_id,
                image=engine_data.get("image", f"dalston/{engine_id}:latest"),
                capabilities=capabilities,
            )

        # M36: Load model entries
        models: dict[str, ModelEntry] = {}
        models_data = data.get("models", {})

        for model_id, model_data in models_data.items():
            caps_data = model_data.get("capabilities", {})
            hw_data = model_data.get("hardware", {})
            perf_data = model_data.get("performance", {})

            models[model_id] = ModelEntry(
                id=model_id,
                runtime=model_data["runtime"],
                runtime_model_id=model_data["runtime_model_id"],
                name=model_data.get("name", model_id),
                source=model_data.get("source"),
                size_gb=model_data.get("size_gb"),
                stage=model_data.get("stage"),
                languages=model_data.get("languages"),
                word_timestamps=caps_data.get("word_timestamps", False),
                punctuation=caps_data.get("punctuation", False),
                capitalization=caps_data.get("capitalization", False),
                supports_cpu=hw_data.get("supports_cpu", False),
                min_vram_gb=hw_data.get("min_vram_gb"),
                min_ram_gb=hw_data.get("min_ram_gb"),
                rtf_gpu=perf_data.get("rtf_gpu"),
                rtf_cpu=perf_data.get("rtf_cpu"),
            )

        logger.info(
            "engine_catalog_loaded",
            engine_count=len(entries),
            model_count=len(models),
        )
        return cls(entries, models)

    # =========================================================================
    # Engine/Runtime methods (backward compatible)
    # =========================================================================

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

    def find_engines(self, stage: str, requirements: dict) -> list[CatalogEntry]:
        """Find catalog engines that could satisfy requirements (M31).

        Used by the engine selector to identify alternatives when no running
        engine matches, enabling actionable error messages.

        Args:
            stage: Pipeline stage (e.g., "transcribe", "diarize")
            requirements: Dict of requirements to match:
                - language: ISO 639-1 code (optional)
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
        # Language check
        lang = requirements.get("language")
        if lang and caps.languages is not None:
            if lang.lower() not in [lng.lower() for lng in caps.languages]:
                return False

        # Streaming check
        if requirements.get("streaming") and not caps.supports_streaming:
            return False

        return True

    # =========================================================================
    # M36: Model catalog methods
    # =========================================================================

    def get_model(self, model_id: str) -> ModelEntry | None:
        """Get a specific model entry.

        Args:
            model_id: Dalston public model ID (e.g., "parakeet-tdt-1.1b")

        Returns:
            ModelEntry if found, None otherwise
        """
        return self._models.get(model_id)

    def get_all_models(self) -> list[ModelEntry]:
        """Get all models in the catalog.

        Returns:
            List of all ModelEntry objects
        """
        return list(self._models.values())

    def get_runtime_for_model(self, model_id: str) -> str | None:
        """Get the runtime that loads a model.

        Args:
            model_id: Dalston public model ID

        Returns:
            Runtime ID (e.g., "nemo") or None if model not found
        """
        model = self._models.get(model_id)
        return model.runtime if model else None

    def get_runtime_model_id(self, model_id: str) -> str | None:
        """Get the native model ID for loading.

        Args:
            model_id: Dalston public model ID (e.g., "faster-whisper-large-v3-turbo")

        Returns:
            Native model ID (e.g., "large-v3-turbo") or None if model not found
        """
        model = self._models.get(model_id)
        return model.runtime_model_id if model else None

    def get_models_for_runtime(self, runtime: str) -> list[ModelEntry]:
        """Get all models that a runtime can load.

        Args:
            runtime: Runtime ID (e.g., "nemo", "faster-whisper")

        Returns:
            List of ModelEntry objects for the runtime
        """
        return [m for m in self._models.values() if m.runtime == runtime]

    def get_models_for_stage(self, stage: str) -> list[ModelEntry]:
        """Get all models for a specific pipeline stage.

        Args:
            stage: Pipeline stage (e.g., "transcribe")

        Returns:
            List of ModelEntry objects for the stage
        """
        return [m for m in self._models.values() if m.stage == stage]

    def find_models_supporting_language(
        self, stage: str, language: str
    ) -> list[ModelEntry]:
        """Find models for a stage that support a specific language.

        Args:
            stage: Pipeline stage
            language: ISO 639-1 language code

        Returns:
            List of models that support the language for the stage
        """
        result = []
        for model in self.get_models_for_stage(stage):
            # None means all languages (multilingual)
            if model.languages is None:
                result.append(model)
            elif language.lower() in [lang.lower() for lang in model.languages]:
                result.append(model)
        return result

    # =========================================================================
    # Dunder methods
    # =========================================================================

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
