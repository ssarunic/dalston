"""Abstract Engine base class for batch processing engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from threading import Lock
from typing import Any, Generic, TypeVar

import structlog
import yaml

from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineCapabilities, EngineInput, EngineOutput

# Paths for engine.yaml (container path first, local fallback second)
ENGINE_YAML_PATHS = [
    Path("/etc/dalston/engine.yaml"),
    Path("engine.yaml"),
]


InputPayloadT = TypeVar("InputPayloadT")
OutputPayloadT = TypeVar("OutputPayloadT")


class Engine(Generic[InputPayloadT, OutputPayloadT], ABC):
    """Abstract base class for Dalston batch processing engines.

    Engines implement the `process` method to handle specific pipeline stages.
    The SDK runner handles queue polling, S3 I/O, and event publishing.

    The base class provides ``self.logger``, a structlog bound logger
    pre-configured with the runtime.  Engine authors can use it directly::

        self.logger.info("model_loaded", model="large-v3")

    Example:
        class MyTranscriptionEngine(Engine):
            def __init__(self):
                super().__init__()
                self.model = None

            def process(
                self,
                input: EngineInput,
                ctx: BatchTaskContext,
            ) -> EngineOutput:
                # Load model lazily
                if self.model is None:
                    self.model = load_model(input.config.get("model", "large-v3"))

                result = self.model.transcribe(input.audio_path)
                return TaskOutput(data={"text": result.text, "segments": result.segments})

        if __name__ == "__main__":
            engine = MyTranscriptionEngine()
            engine.run()
    """

    def __init__(self) -> None:
        """Initialize the engine."""
        self._runner = None
        # structlog loggers are lazy proxies — configuration is resolved on
        # first log call, not at creation time.  EngineRunner.__init__() calls
        # dalston.logging.configure() before any logging happens, so this is
        # safe despite being created before configure() runs.
        self.logger = structlog.get_logger()

        # Thread-safe runtime state for heartbeat reporting (Phase 1: Runtime Model Management)
        # The runner's heartbeat loop reads this state each cycle to report
        # the currently loaded model and engine status to the registry.
        self._runtime_state_lock = Lock()
        self._runtime_state: dict[str, Any] = {"loaded_model": None, "status": "idle"}

    @abstractmethod
    def process(
        self,
        input: EngineInput[InputPayloadT],
        ctx: BatchTaskContext,
    ) -> EngineOutput[OutputPayloadT]:
        """Process a single task.

        This method should be implemented by concrete engine classes.
        The SDK ensures the audio file is downloaded before calling this method,
        and handles uploading results afterward.

        Args:
            input: Engine input containing typed payload and materialized artifacts
            ctx: Runtime context for tracing/logging metadata helpers

        Returns:
            EngineOutput containing typed payload and produced artifacts

        Raises:
            Exception: Any exception will be caught by the runner and reported
                as a task failure with the error message.
        """
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        """Return health status for monitoring.

        Override this method to provide engine-specific health information.

        Returns:
            Dictionary with at least a "status" key ("healthy" or "unhealthy")
        """
        return {
            "status": "healthy",
        }

    def shutdown(self) -> None:  # noqa: B027
        """Clean up resources on engine shutdown.

        Override this method to perform cleanup when the engine is stopped
        (e.g., via SIGTERM). Use this to unload models, close connections,
        or release other resources.

        The default implementation does nothing.
        """

    def _set_runtime_state(
        self, loaded_model: str | None = None, status: str = "idle"
    ) -> None:
        """Update the engine's runtime state in a thread-safe manner.

        This method is called by engines after loading/unloading models to report
        their current state. The runner's heartbeat loop reads this state to
        report to the registry.

        Args:
            loaded_model: The runtime_model_id of the currently loaded model,
                          or None if no model is loaded.
            status: Current engine status ("idle", "loading", "downloading", "processing")
        """
        with self._runtime_state_lock:
            self._runtime_state = {"loaded_model": loaded_model, "status": status}

    def get_runtime_state(self) -> dict[str, Any]:
        """Get the current runtime state in a thread-safe manner.

        Called by the runner's heartbeat loop to get the current state for
        inclusion in heartbeat payloads.

        Returns:
            Dictionary with "loaded_model" (str | None) and "status" (str) keys
        """
        with self._runtime_state_lock:
            return dict(self._runtime_state)

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Get local model cache statistics for heartbeat reporting.

        Override this method in engines that use S3ModelStorage to report
        which models are cached locally. The stats are included in heartbeat
        payloads so the orchestrator can track model availability.

        Returns:
            Dictionary with cache stats, or None if not using S3 model storage.
            Expected format: {"models": ["model-a", "model-b"], "total_size_mb": 3500, "model_count": 2}
        """
        return None

    def get_capabilities(self) -> EngineCapabilities:
        """Return engine capabilities for registration and validation.

        Loads capabilities from engine.yaml if available, otherwise falls back
        to a minimal default. The engine.yaml is expected at /etc/dalston/engine.yaml
        in containers, or ./engine.yaml for local development.

        Returns:
            EngineCapabilities describing what this engine can do
        """
        card = self._load_engine_yaml()
        if card is None:
            # Fallback for engines without engine.yaml
            return EngineCapabilities(
                runtime=getattr(self, "runtime", "unknown"),
                version="unknown",
                stages=[],
            )

        # Extract capabilities from engine.yaml
        caps = card.get("capabilities", {})
        hardware = card.get("hardware", {})
        performance = card.get("performance", {})

        # Determine GPU requirement from container.gpu field
        container = card.get("container", {})
        gpu_field = container.get("gpu", "none")
        gpu_required = gpu_field == "required"

        # Languages: convert ["all"] to None (meaning all languages)
        languages = caps.get("languages")
        if languages == ["all"]:
            languages = None

        # Stages: derive from stage field for batch engines
        stage = card.get("stage")
        stages = [stage] if stage else []

        return EngineCapabilities(
            runtime=card.get("runtime") or card.get("id", "unknown"),
            version=card.get("version", "unknown"),
            stages=stages,
            languages=languages,
            supports_word_timestamps=caps.get("word_timestamps", False),
            supports_streaming=caps.get("streaming", False),
            model_variants=None,
            gpu_required=gpu_required,
            gpu_vram_mb=(
                hardware.get("min_vram_gb", 0) * 1024
                if hardware.get("min_vram_gb")
                else None
            ),
            supports_cpu=hardware.get("supports_cpu", True),
            min_ram_gb=hardware.get("min_ram_gb"),
            rtf_gpu=performance.get("rtf_gpu"),
            rtf_cpu=performance.get("rtf_cpu"),
            max_concurrency=caps.get("max_concurrency"),
        )

    def _load_engine_yaml(self) -> dict[str, Any] | None:
        """Load engine.yaml from known paths.

        Returns:
            Parsed engine.yaml dict, or None if not found
        """
        for path in ENGINE_YAML_PATHS:
            if path.exists():
                try:
                    with open(path) as f:
                        return yaml.safe_load(f)
                except Exception as e:
                    self.logger.warning(
                        "failed_to_load_engine_yaml",
                        path=str(path),
                        error=str(e),
                    )
        return None

    def run(self) -> None:
        """Start the engine's processing loop.

        This method creates an EngineRunner and starts polling the queue.
        It blocks until the engine is stopped (e.g., via signal).
        """
        # Import here to avoid circular imports
        from dalston.engine_sdk.runner import EngineRunner

        self._runner = EngineRunner(self)
        self._runner.run()
