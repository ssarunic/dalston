"""Stage payload contracts for M51 engine request/response typing.

These models are forward-declared stage envelopes for stricter typed engine
requests/responses. Current engine_id engines still consume/emit the shared
`dalston.common.pipeline_types` models directly; wiring these contracts into
all engines is an incremental follow-up.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dalston.common.pipeline_types import (
    AlignmentResponse,
    AudioMedia,
    DiarizationResponse,
    MergeResponse,
    PIIDetectionResponse,
    PreparationResponse,
    RedactionResponse,
    SpeakerDetectionMode,
    Transcript,
)


class PreparationRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media: AudioMedia


class TranscriptionRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)
    channel: int | None = Field(default=None, ge=0)


class AlignmentRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcription_stage: str = Field(default="transcribe")
    audio_artifact_id: str = Field(..., min_length=1)


class DiarizationRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)


class PIIDetectionRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_stage: str = Field(default="align")


class RedactionRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)
    pii_stage: str = Field(default="pii_detect")


class MergeRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_detection: SpeakerDetectionMode = SpeakerDetectionMode.NONE
    channel_count: int | None = Field(default=None, ge=1)


class PreparationResponsePayload(PreparationResponse):
    pass


# Transcript is the canonical response type for transcription.
TranscriptionResponsePayload = Transcript


class AlignmentResponsePayload(AlignmentResponse):
    pass


class DiarizationResponsePayload(DiarizationResponse):
    pass


class PIIDetectionResponsePayload(PIIDetectionResponse):
    pass


class RedactionResponsePayload(RedactionResponse):
    pass


class MergeResponsePayload(MergeResponse):
    pass
