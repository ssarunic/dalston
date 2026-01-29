"""Abstract Engine base class for batch processing engines."""

from abc import ABC, abstractmethod
from typing import Any

from dalston.engine_sdk.types import TaskInput, TaskOutput


class Engine(ABC):
    """Abstract base class for Dalston batch processing engines.

    Engines implement the `process` method to handle specific pipeline stages.
    The SDK runner handles queue polling, S3 I/O, and event publishing.

    Example:
        class MyTranscriptionEngine(Engine):
            def __init__(self):
                super().__init__()
                self.model = None

            def process(self, input: TaskInput) -> TaskOutput:
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

    @abstractmethod
    def process(self, input: TaskInput) -> TaskOutput:
        """Process a single task.

        This method should be implemented by concrete engine classes.
        The SDK ensures the audio file is downloaded before calling this method,
        and handles uploading results afterward.

        Args:
            input: Task input containing audio path, config, and previous outputs

        Returns:
            TaskOutput containing the processing results

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

    def run(self) -> None:
        """Start the engine's processing loop.

        This method creates an EngineRunner and starts polling the queue.
        It blocks until the engine is stopped (e.g., via signal).
        """
        # Import here to avoid circular imports
        from dalston.engine_sdk.runner import EngineRunner

        self._runner = EngineRunner(self)
        self._runner.run()
