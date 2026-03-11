"""Stage payload contracts for M51 engine input/output typing.

These models are forward-declared stage envelopes for stricter typed engine
inputs/outputs. Current engine_id engines still consume/emit the shared
`dalston.common.pipeline_types` models directly; wiring these contracts into
all engines is an incremental follow-up.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dalston.common.pipeline_types import (
    AlignOutput,
    AudioMedia,
    AudioRedactOutput,
    DiarizeOutput,
    MergeOutput,
    PIIDetectOutput,
    PrepareOutput,
    SpeakerDetectionMode,
    Transcript,
)


class PrepareInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media: AudioMedia


class TranscribeInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)
    channel: int | None = Field(default=None, ge=0)


class AlignInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcription_stage: str = Field(default="transcribe")
    audio_artifact_id: str = Field(..., min_length=1)


class DiarizeInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)


class PIIDetectInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_stage: str = Field(default="align")


class AudioRedactInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_artifact_id: str = Field(..., min_length=1)
    pii_stage: str = Field(default="pii_detect")


class MergeInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_detection: SpeakerDetectionMode = SpeakerDetectionMode.NONE
    channel_count: int | None = Field(default=None, ge=1)


class PrepareOutputPayload(PrepareOutput):
    pass


# Transcript is the canonical output type for transcription.
TranscribeOutputPayload = Transcript


class AlignOutputPayload(AlignOutput):
    pass


class DiarizeOutputPayload(DiarizeOutput):
    pass


class PIIDetectOutputPayload(PIIDetectOutput):
    pass


class AudioRedactOutputPayload(AudioRedactOutput):
    pass


class MergeOutputPayload(MergeOutput):
    pass
