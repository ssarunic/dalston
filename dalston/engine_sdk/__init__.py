"""Dalston Engine SDK for batch processing engines.

This SDK provides the foundation for building batch processing engines
that integrate with the Dalston transcription pipeline.

Example usage:
    from dalston.engine_sdk import Engine, TaskInput, TaskOutput
    from dalston.engine_sdk import PrepareOutput, TranscribeOutput, Segment, Word

    class MyTranscriptionEngine(Engine):
        def process(self, input: TaskInput) -> TaskOutput:
            # Get typed input from previous stage
            prepare = input.get_prepare_output()
            audio_duration = prepare.duration if prepare else 0.0

            # Process the audio file
            result = transcribe(input.audio_path)

            # Return typed output
            return TaskOutput(data=TranscribeOutput(
                text=result.text,
                segments=[Segment(start=0.0, end=1.0, text="hello")],
                language="en",
                engine_id="my-engine",
            ))

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

# Re-export pipeline types for convenience
from dalston.common.pipeline_types import (
    AlignmentMethod,
    AlignOutput,
    AudioMedia,
    AudioRedactOutput,
    DiarizeOutput,
    MergedSegment,
    MergeOutput,
    Phoneme,
    PIIDetectionTier,
    PIIDetectOutput,
    PIIEntity,
    PIIEntityCategory,
    PIIMetadata,
    PIIRedactionMode,
    PrepareOutput,
    Segment,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    SpeechRegion,
    TaskInputData,
    TimestampGranularity,
    TranscribeOutput,
    TranscriptMetadata,
    Word,
)
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import EngineCapabilities, TaskInput, TaskOutput

__all__ = [
    # Core SDK
    "Engine",
    "EngineCapabilities",
    "EngineRunner",
    "TaskInput",
    "TaskOutput",
    # Enums
    "AlignmentMethod",
    "PIIDetectionTier",
    "PIIEntityCategory",
    "PIIRedactionMode",
    "SpeakerDetectionMode",
    "TimestampGranularity",
    # Core structures
    "AudioMedia",
    "MergedSegment",
    "Phoneme",
    "PIIEntity",
    "PIIMetadata",
    "Segment",
    "Speaker",
    "SpeakerTurn",
    "SpeechRegion",
    "TaskInputData",
    "TranscriptMetadata",
    "Word",
    # Stage outputs
    "AlignOutput",
    "AudioRedactOutput",
    "DiarizeOutput",
    "MergeOutput",
    "PIIDetectOutput",
    "PrepareOutput",
    "TranscribeOutput",
]
