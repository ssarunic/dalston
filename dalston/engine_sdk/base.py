"""Abstract Engine base class for batch processing engines."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from threading import Lock
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import structlog

from dalston.common.engine_yaml import load_engine_yaml, parse_engine_capabilities
from dalston.engine_sdk.audio import SPEECH_STANDARD, AudioFormat
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

if TYPE_CHECKING:
    from dalston.engine_sdk.http_server import EngineHTTPServer

RequestPayloadT = TypeVar("RequestPayloadT")
ResponsePayloadT = TypeVar("ResponsePayloadT")


class Engine(Generic[RequestPayloadT, ResponsePayloadT], ABC):
    """Abstract base class for Dalston batch processing engines.

    Engines implement the `process` method to handle specific pipeline stages.
    The SDK runner handles queue polling, S3 I/O, and event publishing.

    The base class provides ``self.logger``, a structlog bound logger
    pre-configured with the engine_id.  Engine authors can use it directly::

        self.logger.info("model_loaded", model="large-v3")

    Example:
        class MyTranscriptionEngine(Engine):
            def __init__(self):
                super().__init__()
                self.model = None

            def process(
                self,
                task_request: TaskRequest,
                ctx: BatchTaskContext,
            ) -> TaskResponse:
                # Load model lazily
                if self.model is None:
                    self.model = load_model(
                        task_request.config.get("model", "large-v3")
                    )

                result = self.model.transcribe(task_request.audio_path)
                return TaskResponse(data={"text": result.text, "segments": result.segments})

        if __name__ == "__main__":
            engine = MyTranscriptionEngine()
            engine.run()
    """

    # Override in subclass to declare audio requirements.
    # Set to None for engines that don't consume audio (e.g., merge, llm-cleanup).
    audio_format: AudioFormat | None = SPEECH_STANDARD

    #: Default engine identifier. Override in subclasses. Resolved from
    #: ``DALSTON_ENGINE_ID`` env var at construction time, falling back to
    #: this class attribute.
    ENGINE_ID: str = "unknown"

    def __init__(self) -> None:
        """Initialize the engine."""
        self.engine_id = os.environ.get("DALSTON_ENGINE_ID", self.ENGINE_ID)
        self._runner = None
        # structlog loggers are lazy proxies — configuration is resolved on
        # first log call, not at creation time.  EngineRunner.__init__() calls
        # dalston.logging.configure() before any logging happens, so this is
        # safe despite being created before configure() runs.
        self.logger = structlog.get_logger()

        # Thread-safe engine_id state for heartbeat reporting (Phase 1: Runtime Model Management)
        # The runner's heartbeat loop reads this state each cycle to report
        # the currently loaded model and engine status to the registry.
        self._runtime_state_lock = Lock()
        self._runtime_state: dict[str, Any] = {"loaded_model": None, "status": "idle"}

    @abstractmethod
    def process(
        self,
        task_request: TaskRequest[RequestPayloadT],
        ctx: BatchTaskContext,
    ) -> TaskResponse[ResponsePayloadT]:
        """Process a single task.

        This method should be implemented by concrete engine classes.
        The SDK ensures the audio file is downloaded before calling this method,
        and handles uploading results afterward.

        Args:
            task_request: Engine request containing typed payload and materialized artifacts
            ctx: Runtime context for tracing/logging metadata helpers

        Returns:
            TaskResponse containing typed payload and produced artifacts

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
        """Update the engine's engine_id state in a thread-safe manner.

        This method is called by engines after loading/unloading models to report
        their current state. The runner's heartbeat loop reads this state to
        report to the registry.

        Args:
            loaded_model: The loaded_model_id of the currently loaded model,
                          or None if no model is loaded.
            status: Current engine status ("idle", "loading", "downloading", "processing")
        """
        with self._runtime_state_lock:
            self._runtime_state = {"loaded_model": loaded_model, "status": status}

    def get_runtime_state(self) -> dict[str, Any]:
        """Get the current engine_id state in a thread-safe manner.

        Called by the runner's heartbeat loop to get the current state for
        inclusion in heartbeat payloads.

        Returns:
            Dictionary with "loaded_model" (str | None) and "status" (str) keys
        """
        with self._runtime_state_lock:
            return dict(self._runtime_state)

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Get local model cache statistics for heartbeat reporting.

        Override this method in engines that use MultiSourceModelStorage to report
        which models are cached locally. The stats are included in heartbeat
        payloads so the orchestrator can track model availability.

        Returns:
            Dictionary with cache stats, or None if not using S3 model storage.
            Expected format: {"models": ["model-a", "model-b"], "total_size_mb": 3500, "model_count": 2}
        """
        return None

    def create_http_server(self, port: int = 9100) -> EngineHTTPServer:
        """Create the HTTP server for this engine.

        Override in subclasses to return a stage-specific server (e.g.
        ``TranscribeHTTPServer``, ``DiarizeHTTPServer``).  The default
        returns the base ``EngineHTTPServer`` which serves ``/health``,
        ``/metrics``, and ``/v1/capabilities`` only.
        """
        from dalston.engine_sdk.http_server import EngineHTTPServer

        return EngineHTTPServer(engine=self, port=port)

    def get_capabilities(self) -> EngineCapabilities:
        """Return engine capabilities for registration and validation.

        Loads capabilities from engine.yaml if available, otherwise falls back
        to a minimal default.
        """
        card = load_engine_yaml()
        if card is None:
            return EngineCapabilities(
                engine_id=self.engine_id,
                version="unknown",
                stages=[],
            )

        return EngineCapabilities(**parse_engine_capabilities(card))

    def run(self) -> None:
        """Start the engine's processing loop.

        This method creates an EngineRunner and starts polling the queue.
        It blocks until the engine is stopped (e.g., via signal).
        """
        # Import here to avoid circular imports
        from dalston.engine_sdk.runner import EngineRunner

        self._runner = EngineRunner(self)
        self._runner.run()
