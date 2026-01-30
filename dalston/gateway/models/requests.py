"""Pydantic request schemas for Gateway API."""

from pydantic import BaseModel, Field


class TranscriptionCreateParams(BaseModel):
    """Parameters for creating a transcription job.

    These map to form fields in POST /v1/audio/transcriptions.
    Minimal set for M01 - more parameters added in later milestones.
    """

    # Core parameters
    language: str = Field(
        default="auto",
        description="ISO 639-1 language code or 'auto' for detection",
    )

    # Speaker detection (M04)
    speaker_detection: str = Field(
        default="none",
        description="Speaker detection mode: 'none', 'diarize', 'per_channel'",
    )
    num_speakers: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Expected number of speakers (hint for diarization)",
    )
    exclusive: bool = Field(
        default=False,
        description="Exclusive diarization mode (pyannote 4.0+): one speaker per segment",
    )

    # Timestamps (M03)
    timestamps_granularity: str = Field(
        default="word",
        description="Timestamp granularity: 'none', 'segment', 'word'",
    )

    # Webhook (M05)
    webhook_url: str | None = Field(
        default=None,
        description="URL for completion callback",
    )

    def to_job_parameters(self) -> dict:
        """Convert to job parameters dict for storage."""
        return {
            "language": self.language,
            "speaker_detection": self.speaker_detection,
            "num_speakers": self.num_speakers,
            "exclusive": self.exclusive,
            "timestamps_granularity": self.timestamps_granularity,
        }
