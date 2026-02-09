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

    # Transcription hints
    initial_prompt: str | None = Field(
        default=None,
        max_length=1000,
        description="Domain vocabulary hints to improve accuracy (e.g., technical terms, names)",
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
        description="Exact number of speakers (hint for diarization)",
    )
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Minimum number of speakers for diarization auto-detection",
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=32,
        description="Maximum number of speakers for diarization auto-detection",
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
    webhook_metadata: dict | None = Field(
        default=None,
        description="Custom data echoed back in webhook callback (max 16KB)",
    )

    def to_job_parameters(self) -> dict:
        """Convert to job parameters dict for storage."""
        params = {
            "language": self.language,
            "speaker_detection": self.speaker_detection,
            "num_speakers": self.num_speakers,
            "exclusive": self.exclusive,
            "timestamps_granularity": self.timestamps_granularity,
        }
        # Only include optional parameters if set
        if self.initial_prompt is not None:
            params["initial_prompt"] = self.initial_prompt
        if self.min_speakers is not None:
            params["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            params["max_speakers"] = self.max_speakers
        return params
