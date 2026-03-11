"""Dalston Engine SDK for batch processing engines.

This SDK provides the foundation for building batch processing engines
that integrate with the Dalston transcription pipeline.

Example usage:
    from dalston.engine_sdk import Engine, EngineInput, EngineOutput
    from dalston.engine_sdk import PrepareOutput, TranscribeOutput, Segment, Word

    class MyTranscriptionEngine(Engine):
        def process(
            self, engine_input: EngineInput, ctx: BatchTaskContext
        ) -> EngineOutput:
            # Get typed input from previous stage
            prepare = engine_input.get_prepare_output()
            audio_duration = prepare.duration if prepare else 0.0

            # Process the audio file
            result = transcribe(engine_input.audio_path)

            # Return typed output
            return EngineOutput(data=TranscribeOutput(
                text=result.text,
                segments=[Segment(start=0.0, end=1.0, text="hello")],
                language="en",
                runtime="my-engine",
            ))

    if __name__ == "__main__":
        engine = MyTranscriptionEngine()
        engine.run()

Environment variables:
    ENGINE_ID: Unique identifier for this engine (used for queue name)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    S3_BUCKET: S3 bucket for artifacts (default: dalston-artifacts)
    S3_REGION: AWS region (default: eu-west-2)
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
    DalstonTranscriptV1,
    DiarizeOutput,
    MergedSegment,
    MergeOutput,
    Phoneme,
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
    TranscriptSegment,
    TranscriptWord,
    Word,
)
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.local_runner import LocalRunner
from dalston.engine_sdk.model_manager import LoadedModel, ModelManager
from dalston.engine_sdk.types import (
    EngineCapabilities,
    EngineInput,
    EngineOutput,
)

__all__ = [
    # Core SDK
    "BaseBatchTranscribeEngine",
    "Engine",
    "EngineCapabilities",
    "LocalRunner",
    "EngineInput",
    "EngineOutput",
    "BatchTaskContext",
    # Model management (M39.2)
    "LoadedModel",
    "ModelManager",
    # Enums
    "AlignmentMethod",
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
    "TranscriptSegment",
    "TranscriptWord",
    "Word",
    # Stage outputs
    "AlignOutput",
    "AudioRedactOutput",
    "DalstonTranscriptV1",
    "DiarizeOutput",
    "MergeOutput",
    "PIIDetectOutput",
    "PrepareOutput",
    "TranscribeOutput",
]
