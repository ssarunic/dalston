"""OpenAI-compatible Audio Transcription API helpers.

This module provides helper functions for OpenAI API compatibility:
- Model detection and mapping
- Response format transformation
- Error formatting

Used by:
- transcription.py (for detecting OpenAI-style requests on /v1/audio/transcriptions)
- openai_translation.py (for the standalone /v1/audio/translations endpoint)
"""

import os
import re
from enum import Enum
from typing import Any

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field

from dalston.gateway.services.export import ExportService

# Maximum file size for OpenAI compatibility (25MB)
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024


# =============================================================================
# OpenAI Model Mapping
# =============================================================================

# Default engine for OpenAI model mapping (can be overridden via env var)
DEFAULT_OPENAI_ENGINE = os.getenv("DALSTON_OPENAI_DEFAULT_ENGINE", "faster-whisper")

# Patterns that identify OpenAI-style model names
# These patterns are designed to match current and future OpenAI transcription models
OPENAI_MODEL_PATTERNS = [
    re.compile(r"^whisper-\d+$"),  # whisper-1, whisper-2, etc.
    re.compile(
        r"^gpt-\d+o(-mini)?-transcribe$"
    ),  # gpt-4o-transcribe, gpt-4o-mini-transcribe
    re.compile(
        r"^gpt-\d+(\.\d+)?-audio(-preview)?$"
    ),  # gpt-4-audio, gpt-4.5-audio-preview, etc.
]


# Model-to-engine mappings (falls back to DEFAULT_OPENAI_ENGINE if not found)
OPENAI_MODEL_MAP: dict[str, str] = {}


class OpenAIResponseFormat(str, Enum):
    """OpenAI response format options."""

    JSON = "json"
    TEXT = "text"
    SRT = "srt"
    VERBOSE_JSON = "verbose_json"
    VTT = "vtt"


OPENAI_RESPONSE_FORMATS = {f.value for f in OpenAIResponseFormat}


# =============================================================================
# OpenAI Response Models
# =============================================================================


class OpenAITranscriptionResponse(BaseModel):
    """OpenAI simple transcription response (response_format=json)."""

    text: str


class OpenAIWord(BaseModel):
    """OpenAI word with timestamp."""

    word: str
    start: float
    end: float


class OpenAISegment(BaseModel):
    """OpenAI verbose segment."""

    id: int
    seek: int = 0
    start: float
    end: float
    text: str
    tokens: list[int] = Field(default_factory=list)
    temperature: float = 0.0
    avg_logprob: float = -0.5
    compression_ratio: float = 1.0
    no_speech_prob: float = 0.0


class OpenAIVerboseResponse(BaseModel):
    """OpenAI verbose transcription response (response_format=verbose_json)."""

    task: str = "transcribe"
    language: str
    duration: float
    text: str
    segments: list[OpenAISegment] = Field(default_factory=list)
    words: list[OpenAIWord] | None = None


class OpenAIErrorDetail(BaseModel):
    """OpenAI error detail format."""

    message: str
    type: str
    param: str | None = None
    code: str


class OpenAIErrorResponse(BaseModel):
    """OpenAI error response format."""

    error: OpenAIErrorDetail


# =============================================================================
# Helper Functions
# =============================================================================


def is_openai_model(model: str) -> bool:
    """Check if the model is an OpenAI model ID.

    Uses pattern matching to detect OpenAI models, allowing for future model releases
    without requiring code changes. Matches:
    - whisper-1, whisper-2, etc.
    - gpt-4o-transcribe, gpt-4o-mini-transcribe
    - gpt-4-audio, gpt-4.5-audio-preview, etc.
    """
    return any(pattern.match(model) for pattern in OPENAI_MODEL_PATTERNS)


def is_openai_response_format(response_format: str | None) -> bool:
    """Check if the response format is an OpenAI format."""
    return response_format is not None and response_format in OPENAI_RESPONSE_FORMATS


def map_openai_model(model: str) -> str:
    """Map OpenAI model ID to Dalston engine ID.

    Returns: engine_id

    Uses explicit mappings from OPENAI_MODEL_MAP if available,
    otherwise falls back to DEFAULT_OPENAI_ENGINE.
    """
    return OPENAI_MODEL_MAP.get(model, DEFAULT_OPENAI_ENGINE)


def raise_openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str = "invalid_request",
) -> None:
    """Raise HTTPException with OpenAI error format."""
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            }
        },
    )


def format_openai_response(
    transcript: dict[str, Any],
    response_format: str,
    timestamp_granularities: list[str] | None,
    export_service: ExportService,
) -> Response | dict[str, Any]:
    """Format Dalston transcript as OpenAI response."""
    text = transcript.get("text", "")
    metadata = transcript.get("metadata", {})
    language = (
        metadata.get("language")
        or transcript.get("language_code")
        or transcript.get("language")
        or "en"
    )
    duration = metadata.get("duration") or transcript.get("audio_duration") or 0.0

    # Plain text
    if response_format == OpenAIResponseFormat.TEXT.value:
        return Response(content=text, media_type="text/plain")

    # SRT subtitle format
    if response_format == OpenAIResponseFormat.SRT.value:
        return export_service.create_export_response(
            transcript=transcript,
            export_format="srt",
            include_speakers=True,
            max_line_length=42,
            max_lines=2,
        )

    # VTT subtitle format
    if response_format == OpenAIResponseFormat.VTT.value:
        return export_service.create_export_response(
            transcript=transcript,
            export_format="vtt",
            include_speakers=True,
            max_line_length=42,
            max_lines=2,
        )

    # Verbose JSON
    if response_format == OpenAIResponseFormat.VERBOSE_JSON.value:
        # Build segments
        segments = []
        for i, seg in enumerate(transcript.get("segments", [])):
            segments.append(
                OpenAISegment(
                    id=i,
                    seek=0,
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", ""),
                    tokens=[],
                    temperature=0.0,
                    avg_logprob=-0.5,
                    compression_ratio=1.0,
                    no_speech_prob=0.02,
                )
            )

        # Build words if requested
        words = None
        if timestamp_granularities and "word" in timestamp_granularities:
            word_list = transcript.get("words", [])
            if not word_list:
                # Extract from segments
                for seg in transcript.get("segments", []):
                    word_list.extend(seg.get("words", []))
            words = [
                OpenAIWord(
                    word=w.get("text", w.get("word", "")),
                    start=w.get("start", 0.0),
                    end=w.get("end", 0.0),
                )
                for w in word_list
            ]

        return OpenAIVerboseResponse(
            task="transcribe",
            language=language,
            duration=duration,
            text=text,
            segments=segments,
            words=words,
        ).model_dump()

    # Default: simple JSON
    return OpenAITranscriptionResponse(text=text).model_dump()


def validate_openai_request(
    model: str,
    response_format: str | None,
    timestamp_granularities: list[str] | None,
) -> None:
    """Validate OpenAI-specific request parameters.

    Raises HTTPException with OpenAI error format if invalid.
    """
    # Validate model using pattern-based detection
    # Note: is_openai_model() is typically called before this function,
    # but we validate here for defense in depth
    if not is_openai_model(model):
        raise_openai_error(
            400,
            f"Invalid model: {model}. Supported: whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe.",
            param="model",
            code="model_not_found",
        )

    # Validate response format
    if response_format and response_format not in OPENAI_RESPONSE_FORMATS:
        raise_openai_error(
            400,
            f"Invalid response_format: {response_format}. Supported: {', '.join(OPENAI_RESPONSE_FORMATS)}.",
            param="response_format",
            code="invalid_response_format",
        )

    # Validate timestamp_granularities requires verbose_json
    if (
        timestamp_granularities
        and response_format != OpenAIResponseFormat.VERBOSE_JSON.value
    ):
        raise_openai_error(
            400,
            "timestamp_granularities requires response_format=verbose_json",
            param="timestamp_granularities",
            code="invalid_request",
        )

    # Validate timestamp_granularities values
    valid_granularities = {"word", "segment"}
    if timestamp_granularities:
        invalid = set(timestamp_granularities) - valid_granularities
        if invalid:
            raise_openai_error(
                400,
                f"Invalid timestamp_granularities: {invalid}. Supported: word, segment.",
                param="timestamp_granularities",
                code="invalid_request",
            )
