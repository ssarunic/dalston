"""OpenAI-compatible Audio Transcription API helpers.

This module provides helper functions for OpenAI API compatibility:
- Model detection and mapping
- Response format transformation
- Error formatting

Used by:
- transcription.py (for detecting OpenAI-style requests on /v1/audio/transcriptions)
- openai_translation.py (for the standalone /v1/audio/translations endpoint)
"""

import math
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, NoReturn

import structlog
from fastapi import HTTPException, Response
from pydantic import BaseModel, Field

from dalston.gateway.services.export import ExportService

# Maximum file size for OpenAI compatibility (25MB)
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024
OPENAI_PROMPT_MAX_TOKENS = 224
logger = structlog.get_logger()


# =============================================================================
# OpenAI Model Mapping
# =============================================================================

# Default engine for OpenAI model mapping (can be overridden via env var)
DEFAULT_OPENAI_ENGINE = os.getenv("DALSTON_OPENAI_DEFAULT_ENGINE", "faster-whisper")
DEFAULT_OPENAI_RUNTIME_MODEL_ID = os.getenv(
    "DALSTON_OPENAI_DEFAULT_MODEL_ID",
    "Systran/faster-whisper-base",
)

UsageMode = Literal["none", "audio_seconds", "tokens"]


class OpenAIEndpoint(StrEnum):
    """OpenAI STT endpoint identifiers."""

    TRANSCRIPTIONS = "transcriptions"
    TRANSLATIONS = "translations"
    REALTIME = "realtime"


class OpenAIResponseFormat(StrEnum):
    """OpenAI response format options."""

    JSON = "json"
    TEXT = "text"
    SRT = "srt"
    VERBOSE_JSON = "verbose_json"
    VTT = "vtt"
    DIARIZED_JSON = "diarized_json"


OPENAI_RESPONSE_FORMATS = {f.value for f in OpenAIResponseFormat}


@dataclass(frozen=True)
class OpenAIModelCapabilities:
    """Capability matrix entry for one OpenAI STT model alias."""

    endpoints: frozenset[OpenAIEndpoint]
    response_formats: frozenset[str]
    supports_diarized_output: bool = False
    usage_mode: UsageMode = "none"
    supports_chunking_strategy: bool = False
    supports_known_speaker_names: bool = False
    supports_known_speaker_references: bool = False
    supports_transcription_logprobs: bool = False


_STANDARD_RESPONSE_FORMATS = frozenset(
    {
        OpenAIResponseFormat.JSON.value,
        OpenAIResponseFormat.TEXT.value,
        OpenAIResponseFormat.SRT.value,
        OpenAIResponseFormat.VERBOSE_JSON.value,
        OpenAIResponseFormat.VTT.value,
    }
)


OPENAI_STT_CAPABILITIES: dict[str, OpenAIModelCapabilities] = {
    "whisper-1": OpenAIModelCapabilities(
        endpoints=frozenset(
            {
                OpenAIEndpoint.TRANSCRIPTIONS,
                OpenAIEndpoint.TRANSLATIONS,
                OpenAIEndpoint.REALTIME,
            }
        ),
        response_formats=_STANDARD_RESPONSE_FORMATS,
        usage_mode="audio_seconds",
    ),
    "gpt-4o-transcribe": OpenAIModelCapabilities(
        endpoints=frozenset(
            {
                OpenAIEndpoint.TRANSCRIPTIONS,
                OpenAIEndpoint.REALTIME,
            }
        ),
        response_formats=_STANDARD_RESPONSE_FORMATS,
        supports_chunking_strategy=True,
    ),
    "gpt-4o-mini-transcribe": OpenAIModelCapabilities(
        endpoints=frozenset(
            {
                OpenAIEndpoint.TRANSCRIPTIONS,
                OpenAIEndpoint.REALTIME,
            }
        ),
        response_formats=_STANDARD_RESPONSE_FORMATS,
        supports_chunking_strategy=True,
    ),
    "gpt-4o-transcribe-diarize": OpenAIModelCapabilities(
        endpoints=frozenset(
            {
                OpenAIEndpoint.TRANSCRIPTIONS,
            }
        ),
        response_formats=frozenset(
            {
                *_STANDARD_RESPONSE_FORMATS,
                OpenAIResponseFormat.DIARIZED_JSON.value,
            }
        ),
        supports_diarized_output=True,
        supports_known_speaker_names=True,
    ),
    "gpt-4o-transcribe-latest": OpenAIModelCapabilities(
        endpoints=frozenset(
            {
                OpenAIEndpoint.TRANSCRIPTIONS,
                OpenAIEndpoint.REALTIME,
            }
        ),
        response_formats=_STANDARD_RESPONSE_FORMATS,
        supports_chunking_strategy=True,
    ),
}


# Model-to-engine mappings (falls back to DEFAULT_OPENAI_ENGINE if not found)
OPENAI_MODEL_MAP: dict[str, str] = dict.fromkeys(
    OPENAI_STT_CAPABILITIES,
    DEFAULT_OPENAI_ENGINE,
)

OPENAI_RUNTIME_MODEL_MAP: dict[str, str] = dict.fromkeys(
    OPENAI_STT_CAPABILITIES,
    DEFAULT_OPENAI_RUNTIME_MODEL_ID,
)


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

    This is intentionally table-driven to keep request validation aligned with the
    frozen compatibility contract.
    """
    return model in OPENAI_STT_CAPABILITIES


def is_openai_response_format(response_format: str | None) -> bool:
    """Check if the response format is an OpenAI format."""
    return response_format is not None and response_format in OPENAI_RESPONSE_FORMATS


def get_openai_capabilities(model: str) -> OpenAIModelCapabilities | None:
    """Get capability matrix entry for model."""
    return OPENAI_STT_CAPABILITIES.get(model)


def list_openai_models(endpoint: OpenAIEndpoint | None = None) -> list[str]:
    """List OpenAI model aliases, optionally filtered by endpoint support."""
    if endpoint is None:
        return sorted(OPENAI_STT_CAPABILITIES)

    return sorted(
        model
        for model, capability in OPENAI_STT_CAPABILITIES.items()
        if endpoint in capability.endpoints
    )


def is_openai_model_supported_for_endpoint(
    model: str, endpoint: OpenAIEndpoint
) -> bool:
    """Check model support for a specific OpenAI endpoint."""
    capability = get_openai_capabilities(model)
    return capability is not None and endpoint in capability.endpoints


def map_openai_model(model: str) -> str:
    """Map OpenAI model ID to Dalston runtime.

    Returns: runtime identifier

    Uses explicit mappings from OPENAI_MODEL_MAP if available,
    otherwise falls back to DEFAULT_OPENAI_ENGINE.
    """
    return OPENAI_MODEL_MAP.get(model, DEFAULT_OPENAI_ENGINE)


def map_openai_runtime_model(model: str) -> str | None:
    """Map OpenAI model ID to concrete runtime model variant when configured."""
    return OPENAI_RUNTIME_MODEL_MAP.get(model)


def build_openai_rate_limit_headers(
    limit: int,
    remaining: int,
    reset_seconds: int | None = None,
) -> dict[str, str]:
    """Build OpenAI+legacy rate-limit headers."""
    headers = {
        # Existing Dalston legacy headers (kept for backward compatibility)
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        # OpenAI-style request headers
        "X-RateLimit-Limit-Requests": str(limit),
        "X-RateLimit-Remaining-Requests": str(remaining),
    }
    if reset_seconds is not None:
        headers["X-RateLimit-Reset-Requests"] = str(reset_seconds)
    return headers


def attach_openai_rate_limit_headers(
    payload: Response | dict[str, Any],
    response: Response,
    headers: dict[str, str] | None,
) -> None:
    """Attach OpenAI rate-limit headers to either raw payload or response."""
    if not headers:
        return
    if isinstance(payload, Response):
        payload.headers.update(headers)
    else:
        response.headers.update(headers)


def _estimate_prompt_tokens(prompt: str) -> int:
    """Estimate prompt tokens with tokenizer fallback."""
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(prompt))
    except (ImportError, ModuleNotFoundError):
        # Conservative fallback only when tokenizer support is unavailable.
        logger.warning(
            "openai_prompt_tokenizer_unavailable",
            tokenizer="tiktoken",
            fallback="word_or_char_heuristic",
        )
        return max(len(prompt.split()), math.ceil(len(prompt) / 3))


def validate_openai_prompt(prompt: str | None) -> None:
    """Validate OpenAI prompt token length."""
    if not prompt:
        return

    token_count = _estimate_prompt_tokens(prompt)
    if token_count > OPENAI_PROMPT_MAX_TOKENS:
        raise_openai_error(
            400,
            (
                f"prompt exceeds max {OPENAI_PROMPT_MAX_TOKENS} tokens "
                f"(estimated {token_count})"
            ),
            param="prompt",
            code="invalid_prompt",
        )


def _build_openai_usage(model: str | None, transcript: dict[str, Any]) -> dict | None:
    """Build model-aware usage object."""
    if not model:
        return None

    capability = get_openai_capabilities(model)
    if capability is None:
        return None

    if capability.usage_mode == "audio_seconds":
        metadata = transcript.get("metadata", {})
        duration = metadata.get("duration") or transcript.get("audio_duration")
        if duration is None:
            duration = 0.0
        return {
            "type": "audio",
            "audio_seconds": round(float(duration), 3),
        }

    # Token usage is deferred until exact accounting is available.
    return None


def raise_openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str = "invalid_request",
) -> NoReturn:
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
    *,
    model: str | None = None,
    task: str = "transcribe",
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
            raw_tokens = seg.get("tokens")
            tokens = raw_tokens if isinstance(raw_tokens, list) else []
            temperature = seg.get("temperature")
            avg_logprob = seg.get("avg_logprob")
            compression_ratio = seg.get("compression_ratio")
            no_speech_prob = seg.get("no_speech_prob")
            segments.append(
                OpenAISegment(
                    id=i,
                    seek=0,
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", ""),
                    tokens=tokens,
                    temperature=(
                        float(temperature)
                        if isinstance(temperature, int | float)
                        else 0.0
                    ),
                    avg_logprob=(
                        float(avg_logprob)
                        if isinstance(avg_logprob, int | float)
                        else -0.5
                    ),
                    compression_ratio=(
                        float(compression_ratio)
                        if isinstance(compression_ratio, int | float)
                        else 1.0
                    ),
                    no_speech_prob=(
                        float(no_speech_prob)
                        if isinstance(no_speech_prob, int | float)
                        else 0.02
                    ),
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

        payload = OpenAIVerboseResponse(
            task=task,
            language=language,
            duration=duration,
            text=text,
            segments=segments,
            words=words,
        ).model_dump()
        usage = _build_openai_usage(model, transcript)
        if usage is not None:
            payload["usage"] = usage
        return payload

    # Diarized JSON (speaker-aware)
    if response_format == OpenAIResponseFormat.DIARIZED_JSON.value:
        segments = [
            {
                "speaker": seg.get("speaker"),
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", ""),
            }
            for seg in transcript.get("segments", [])
        ]
        payload: dict[str, Any] = {
            "text": text,
            "segments": segments,
        }
        speakers = transcript.get("speakers")
        if isinstance(speakers, list):
            payload["speakers"] = speakers
        usage = _build_openai_usage(model, transcript)
        if usage is not None:
            payload["usage"] = usage
        return payload

    # Default: simple JSON
    payload = OpenAITranscriptionResponse(text=text).model_dump()
    usage = _build_openai_usage(model, transcript)
    if usage is not None:
        payload["usage"] = usage
    return payload


def validate_openai_request(
    model: str,
    response_format: str | None,
    timestamp_granularities: list[str] | None,
    *,
    endpoint: OpenAIEndpoint = OpenAIEndpoint.TRANSCRIPTIONS,
    prompt: str | None = None,
    known_speaker_names: list[str] | None = None,
    chunking_strategy: dict[str, Any] | None = None,
    include: list[str] | None = None,
) -> None:
    """Validate OpenAI-specific request parameters.

    Raises HTTPException with OpenAI error format if invalid.
    """
    # Validate model alias
    if not is_openai_model(model):
        supported_models = ", ".join(list_openai_models(endpoint))
        raise_openai_error(
            400,
            f"Invalid model: {model}. Supported: {supported_models}.",
            param="model",
            code="model_not_found",
        )

    # Validate endpoint support for selected model
    if not is_openai_model_supported_for_endpoint(model, endpoint):
        raise_openai_error(
            400,
            f"Model {model} does not support endpoint {endpoint.value}",
            param="model",
            code="invalid_model",
        )

    capability = get_openai_capabilities(model)
    if capability is None:
        raise_openai_error(
            400,
            f"Invalid model: {model}.",
            param="model",
            code="model_not_found",
        )

    # Validate response format
    if response_format and response_format not in capability.response_formats:
        raise_openai_error(
            400,
            (
                f"Invalid response_format: {response_format}. Supported: "
                f"{', '.join(sorted(capability.response_formats))}."
            ),
            param="response_format",
            code="invalid_response_format",
        )

    if (
        response_format == OpenAIResponseFormat.DIARIZED_JSON.value
        and not capability.supports_diarized_output
    ):
        raise_openai_error(
            400,
            f"Model {model} does not support diarized_json response format",
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

    if known_speaker_names:
        if not capability.supports_known_speaker_names:
            raise_openai_error(
                400,
                f"Model {model} does not support known_speaker_names",
                param="known_speaker_names",
                code="invalid_request",
            )

    if chunking_strategy is not None:
        if not capability.supports_chunking_strategy:
            raise_openai_error(
                400,
                f"Model {model} does not support chunking_strategy",
                param="chunking_strategy",
                code="invalid_request",
            )
        strategy_type = chunking_strategy.get("type")
        if strategy_type != "auto":
            raise_openai_error(
                400,
                "Unsupported chunking_strategy. Only {'type':'auto'} is currently supported.",
                param="chunking_strategy",
                code="invalid_request",
            )

    if include:
        supported_include = {"item.input_audio_transcription.logprobs"}
        invalid_include = sorted(set(include) - supported_include)
        if invalid_include:
            raise_openai_error(
                400,
                f"Invalid include value(s): {', '.join(invalid_include)}",
                param="include",
                code="invalid_request",
            )
        if not capability.supports_transcription_logprobs:
            raise_openai_error(
                400,
                f"Model {model} does not support include=item.input_audio_transcription.logprobs",
                param="include",
                code="invalid_request",
            )

    validate_openai_prompt(prompt)
