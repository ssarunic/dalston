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

    # Vocabulary boosting
    vocabulary: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Terms to boost recognition (e.g., product names, technical jargon). Max 100 terms.",
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

    # PII Detection (M26)
    pii_detection: bool = Field(
        default=False,
        description="Enable PII detection in transcript",
    )
    pii_detection_tier: str = Field(
        default="standard",
        description="PII detection tier: 'fast', 'standard', 'thorough'",
    )
    pii_entity_types: list[str] | None = Field(
        default=None,
        description="Entity types to detect (null = all defaults)",
    )
    redact_pii: bool = Field(
        default=False,
        description="Generate redacted transcript text",
    )
    redact_pii_audio: bool = Field(
        default=False,
        description="Generate redacted audio file",
    )
    pii_redaction_mode: str = Field(
        default="silence",
        description="Audio redaction mode: 'silence', 'beep'",
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
        if self.vocabulary is not None:
            params["vocabulary"] = self.vocabulary
        if self.min_speakers is not None:
            params["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            params["max_speakers"] = self.max_speakers

        # PII detection parameters (M26)
        if self.pii_detection:
            params["pii_detection"] = True
            params["pii_detection_tier"] = self.pii_detection_tier
            if self.pii_entity_types:
                params["pii_entity_types"] = self.pii_entity_types
            if self.redact_pii:
                params["redact_pii"] = True
            if self.redact_pii_audio:
                params["redact_pii_audio"] = True
                params["pii_redaction_mode"] = self.pii_redaction_mode

        return params
