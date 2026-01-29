"""Stub merger engine for testing the pipeline.

This engine merges outputs from upstream stages into a final transcript.
For M01, it passes through the transcript from the transcriber and writes
the canonical transcript.json file for the Gateway to read.
"""

import os

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk import io


class StubMergerEngine(Engine):
    """Stub merger engine that produces the final transcript output.

    Used in M01 to prove the batch pipeline works end-to-end.
    Takes the transcriber output and writes it to the canonical location.
    """

    def process(self, input: TaskInput) -> TaskOutput:
        """Merge upstream outputs into final transcript.

        Args:
            input: Task input with previous_outputs from transcriber

        Returns:
            TaskOutput with merged transcript data
        """
        # Extract transcript from the transcribe stage
        transcribe_output = input.previous_outputs.get("transcribe", {})

        # Build the final transcript structure expected by the Gateway
        transcript = {
            "text": transcribe_output.get("text", ""),
            "segments": transcribe_output.get("segments", []),
            "words": None,  # Not available in stub
            "speakers": [],  # Empty for M01
            "metadata": {
                "language": transcribe_output.get("language", "en"),
                "language_probability": transcribe_output.get(
                    "language_probability", 1.0
                ),
                "pipeline": ["stub-transcriber", "stub-merger"],
            },
        }

        # Write to the canonical transcript location for the Gateway
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{input.job_id}/transcript.json"
        io.upload_json(transcript, transcript_uri)

        return TaskOutput(data=transcript)


if __name__ == "__main__":
    engine = StubMergerEngine()
    engine.run()
