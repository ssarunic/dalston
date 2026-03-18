"""Dalston Engine SDK for batch processing engines.

This SDK provides the foundation for building batch processing engines
that integrate with the Dalston transcription pipeline.

Example usage:
    from dalston.engine_sdk import Engine, TaskRequest, TaskResponse
    from dalston.engine_sdk import PreparationResponse, Transcript, TranscriptSegment

    class MyTranscriptionEngine(Engine):
        def process(
            self, engine_request: TaskRequest, ctx: BatchTaskContext
        ) -> TaskResponse:
            # Get typed response from previous stage
            prepare = engine_request.get_prepare_response()
            audio_duration = prepare.duration if prepare else 0.0

            # Process the audio file
            result = transcribe(engine_request.audio_path)

            # Return typed response
            return TaskResponse(data=Transcript(
                text=result.text,
                segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
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
    S3_REGION: AWS region (default: eu-west-2)
    S3_ENDPOINT_URL: Custom S3 endpoint (optional, for MinIO)
    AWS_ACCESS_KEY_ID: AWS access key (optional, can use IAM roles)
    AWS_SECRET_ACCESS_KEY: AWS secret key (optional, can use IAM roles)
"""

# Re-export pipeline types for convenience
from dalston.common.pipeline_types import (
    PIPELINE_SCHEMA_VERSION,
    STAGE_CONFIG_MAP,
    AlignmentMethod,
    AlignmentRequest,
    AlignmentResponse,
    AudioMedia,
    AudioRedactRequest,
    DiarizationRequest,
    DiarizationResponse,
    MergedSegment,
    MergeRequest,
    MergeResponse,
    Phoneme,
    PIIDetectionRequest,
    PIIDetectionResponse,
    PIIEntity,
    PIIEntityCategory,
    PIIMetadata,
    PIIRedactionMode,
    PreparationRequest,
    PreparationResponse,
    RedactionResponse,
    Segment,
    SegmentMetaKeys,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    SpeechRegion,
    StageInput,
    TaskRequestData,
    TimestampGranularity,
    Transcript,
    TranscriptionRequest,
    TranscriptMetadata,
    TranscriptMetaKeys,
    TranscriptSegment,
    TranscriptWord,
    Word,
    WordMetaKeys,
)
from dalston.engine_sdk.audio import (
    SPEECH_STANDARD,
    AudioFormat,
    EngineAudioError,
    ensure_audio_format,
)
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.device import detect_device
from dalston.engine_sdk.local_runner import LocalRunner
from dalston.engine_sdk.model_manager import LoadedModel, ModelManager
from dalston.engine_sdk.types import (
    EngineCapabilities,
    TaskRequest,
    TaskResponse,
)


def __getattr__(name: str):
    """Lazy imports for HTTP server classes that depend on FastAPI."""
    if name == "EngineHTTPServer":
        from dalston.engine_sdk.http_server import EngineHTTPServer

        return EngineHTTPServer
    if name == "TranscribeHTTPServer":
        from dalston.engine_sdk.http_transcribe import TranscribeHTTPServer

        return TranscribeHTTPServer
    if name == "DiarizeHTTPServer":
        from dalston.engine_sdk.http_diarize import DiarizeHTTPServer

        return DiarizeHTTPServer
    if name == "AlignHTTPServer":
        from dalston.engine_sdk.http_align import AlignHTTPServer

        return AlignHTTPServer
    if name == "CombinedHTTPServer":
        from dalston.engine_sdk.http_combined import CombinedHTTPServer

        return CombinedHTTPServer
    if name == "CompositeEngine":
        from dalston.engine_sdk.base_composite import CompositeEngine

        return CompositeEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Audio format utilities (M81)
    "AudioFormat",
    "EngineAudioError",
    "SPEECH_STANDARD",
    "ensure_audio_format",
    # Core SDK
    "BaseBatchTranscribeEngine",
    "Engine",
    "detect_device",
    "EngineCapabilities",
    "LocalRunner",
    "TaskRequest",
    "TaskResponse",
    "BatchTaskContext",
    # Composite engine
    "CompositeEngine",
    # HTTP servers (M79)
    "AlignHTTPServer",
    "CombinedHTTPServer",
    "DiarizeHTTPServer",
    "EngineHTTPServer",
    "TranscribeHTTPServer",
    # Model management (M39.2)
    "LoadedModel",
    "ModelManager",
    # Schema version
    "PIPELINE_SCHEMA_VERSION",
    "STAGE_CONFIG_MAP",
    # Enums
    "AlignmentMethod",
    "PIIEntityCategory",
    "PIIRedactionMode",
    "SpeakerDetectionMode",
    "TimestampGranularity",
    # Stage input models
    "AlignmentRequest",
    "AudioRedactRequest",
    "DiarizationRequest",
    "MergeRequest",
    "PIIDetectionRequest",
    "PreparationRequest",
    "StageInput",
    "TranscriptionRequest",
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
    "TaskRequestData",
    "TranscriptMetadata",
    "TranscriptSegment",
    "TranscriptWord",
    "SegmentMetaKeys",
    "TranscriptMetaKeys",
    "Word",
    "WordMetaKeys",
    # Stage outputs
    "AlignmentResponse",
    "RedactionResponse",
    "Transcript",
    "DiarizationResponse",
    "MergeResponse",
    "PIIDetectionResponse",
    "PreparationResponse",
]
