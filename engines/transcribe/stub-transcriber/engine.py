"""Stub transcription engine for testing the pipeline.

This engine returns a hardcoded transcript to verify that the
engine SDK, queue polling, and event publishing work correctly.
"""

from dalston.engine_sdk import Engine, TaskInput, TaskOutput


class StubTranscriberEngine(Engine):
    """Stub transcription engine that returns hardcoded output.

    Used in M01 to prove the batch pipeline works end-to-end
    before implementing real transcription engines.
    """

    def process(self, input: TaskInput) -> TaskOutput:
        """Return a hardcoded transcript.

        Args:
            input: Task input (ignored by this stub)

        Returns:
            TaskOutput with hardcoded transcript data
        """
        return TaskOutput(
            data={
                "text": "This is a stub transcript. The system works!",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 3.5,
                        "text": "This is a stub transcript. The system works!",
                    }
                ],
                "language": "en",
                "language_confidence": 1.0,
            }
        )


if __name__ == "__main__":
    engine = StubTranscriberEngine()
    engine.run()
