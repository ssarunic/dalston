"""Dalston Engine SDK for batch processing engines.

This SDK provides the foundation for building batch processing engines
that integrate with the Dalston transcription pipeline.

Example usage:
    from dalston.engine_sdk import Engine, TaskInput, TaskOutput

    class MyTranscriptionEngine(Engine):
        def process(self, input: TaskInput) -> TaskOutput:
            # Process the audio file
            result = transcribe(input.audio_path)
            return TaskOutput(data={"text": result})

    if __name__ == "__main__":
        engine = MyTranscriptionEngine()
        engine.run()

Environment variables:
    ENGINE_ID: Unique identifier for this engine (used for queue name)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    S3_BUCKET: S3 bucket for artifacts (default: dalston-artifacts)
    S3_REGION: AWS region (default: us-east-1)
    S3_ENDPOINT_URL: Custom S3 endpoint (optional, for MinIO)
    AWS_ACCESS_KEY_ID: AWS access key (optional, can use IAM roles)
    AWS_SECRET_ACCESS_KEY: AWS secret key (optional, can use IAM roles)
"""

from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import TaskInput, TaskOutput

__all__ = [
    "Engine",
    "EngineRunner",
    "TaskInput",
    "TaskOutput",
]
