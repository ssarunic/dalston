"""ElevenLabs STT compatibility contract helpers.

This module is the single source of truth for the frozen ElevenLabs STT contract
used by Dalston on:
- POST /v1/speech-to-text
- WS /v1/speech-to-text/realtime
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from fastapi import HTTPException

RequestLocation = Literal["query", "multipart", "ws_query", "message_body"]


class ElevenLabsEndpoint(StrEnum):
    """ElevenLabs STT endpoint identifiers."""

    BATCH = "batch"
    REALTIME = "realtime"


@dataclass(frozen=True)
class ElevenLabsAudioFormatSpec:
    """Realtime input audio format metadata."""

    sample_rate: int
    encoding: str
    decode_ulaw_to_pcm16: bool = False


@dataclass(frozen=True)
class ElevenLabsEndpointCapabilities:
    """Frozen capability row for one ElevenLabs endpoint."""

    models: frozenset[str]
    fields: dict[RequestLocation, frozenset[str]]
    audio_formats: frozenset[str]
    word_fields: frozenset[str]
    supports_single_use_token: bool
    default_commit_strategy: str | None = None


# Contract date pinned by M62.
ELEVENLABS_CONTRACT_DATE = "2026-03-08"

# Docs-backed limits.
ELEVENLABS_MAX_KEYTERMS = 100
ELEVENLABS_MAX_KEYTERM_CHARS = 50
ELEVENLABS_MAX_KEYTERM_WORDS = 5
ELEVENLABS_MAX_UPLOAD_BYTES = 3 * 1024 * 1024 * 1024  # 3 GB
ELEVENLABS_MAX_CLOUD_URL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

ELEVENLABS_REALTIME_AUDIO_FORMATS: dict[str, ElevenLabsAudioFormatSpec] = {
    "pcm_8000": ElevenLabsAudioFormatSpec(sample_rate=8000, encoding="pcm_s16le"),
    "pcm_16000": ElevenLabsAudioFormatSpec(sample_rate=16000, encoding="pcm_s16le"),
    "pcm_22050": ElevenLabsAudioFormatSpec(sample_rate=22050, encoding="pcm_s16le"),
    "pcm_24000": ElevenLabsAudioFormatSpec(sample_rate=24000, encoding="pcm_s16le"),
    "pcm_44100": ElevenLabsAudioFormatSpec(sample_rate=44100, encoding="pcm_s16le"),
    "ulaw_8000": ElevenLabsAudioFormatSpec(
        sample_rate=8000,
        encoding="pcm_s16le",
        decode_ulaw_to_pcm16=True,
    ),
}

ELEVENLABS_STT_CAPABILITIES: dict[
    ElevenLabsEndpoint, ElevenLabsEndpointCapabilities
] = {
    ElevenLabsEndpoint.BATCH: ElevenLabsEndpointCapabilities(
        models=frozenset({"scribe_v1", "scribe_v1_experimental", "scribe_v2"}),
        fields={
            "query": frozenset({"enable_logging"}),
            "multipart": frozenset(
                {
                    "file",
                    "cloud_storage_url",
                    "model_id",
                    "language_code",
                    "diarize",
                    "num_speakers",
                    "timestamps_granularity",
                    "tag_audio_events",
                    "keyterms",
                    "webhook",
                    "file_format",
                    "webhook_id",
                    "webhook_metadata",
                    "entity_detection",
                    "additional_formats",
                    "temperature",
                    "seed",
                    "use_multi_channel",
                }
            ),
            "ws_query": frozenset(),
            "message_body": frozenset(),
        },
        audio_formats=frozenset(
            {
                "mp3",
                "wav",
                "flac",
                "ogg",
                "opus",
                "m4a",
                "aac",
                "webm",
                "aiff",
                "mp4",
            }
        ),
        word_fields=frozenset(
            {"text", "start", "end", "type", "speaker_id", "logprob", "characters"}
        ),
        supports_single_use_token=False,
    ),
    ElevenLabsEndpoint.REALTIME: ElevenLabsEndpointCapabilities(
        models=frozenset({"scribe_v1", "scribe_v2"}),
        fields={
            "query": frozenset(),
            "multipart": frozenset(),
            "ws_query": frozenset(
                {
                    "model_id",
                    "language_code",
                    "audio_format",
                    "commit_strategy",
                    "include_timestamps",
                    "include_language_detection",
                    "keyterms",
                    "previous_text",
                    "vad_threshold",
                    "min_speech_duration_ms",
                    "min_silence_duration_ms",
                    "prefix_padding_ms",
                    "token",
                    "api_key",
                }
            ),
            "message_body": frozenset(
                {
                    "message_type",
                    "audio_base_64",
                    "commit",
                    "close_stream",
                }
            ),
        },
        audio_formats=frozenset(ELEVENLABS_REALTIME_AUDIO_FORMATS),
        word_fields=frozenset(
            {"text", "start", "end", "type", "logprob", "characters"}
        ),
        supports_single_use_token=True,
        default_commit_strategy="manual",
    ),
}


def list_elevenlabs_models(endpoint: ElevenLabsEndpoint) -> list[str]:
    """Return sorted supported model IDs for an endpoint."""
    return sorted(ELEVENLABS_STT_CAPABILITIES[endpoint].models)


def is_elevenlabs_model_supported(model_id: str, endpoint: ElevenLabsEndpoint) -> bool:
    """Check if a model_id is supported for the endpoint contract."""
    return model_id in ELEVENLABS_STT_CAPABILITIES[endpoint].models


def is_request_field_supported(
    endpoint: ElevenLabsEndpoint,
    location: RequestLocation,
    field: str,
) -> bool:
    """Check if a request field is valid at a specific location."""
    capability = ELEVENLABS_STT_CAPABILITIES[endpoint]
    return field in capability.fields[location]


def ensure_model_supported(model_id: str, endpoint: ElevenLabsEndpoint) -> None:
    """Raise HTTP 400 when model_id is unsupported."""
    if is_elevenlabs_model_supported(model_id, endpoint):
        return

    supported = ", ".join(list_elevenlabs_models(endpoint))
    raise HTTPException(
        status_code=400,
        detail=(
            f"Invalid model_id: {model_id}. Supported for {endpoint.value}: {supported}."
        ),
    )


def ensure_field_location_supported(
    endpoint: ElevenLabsEndpoint,
    location: RequestLocation,
    field: str,
) -> None:
    """Raise HTTP 400 when a field is sent in an unsupported location."""
    if is_request_field_supported(endpoint, location, field):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported field location: '{field}' is not allowed in {location} "
            f"for ElevenLabs {endpoint.value}."
        ),
    )


def get_realtime_audio_format_spec(
    audio_format: str,
) -> ElevenLabsAudioFormatSpec | None:
    """Get realtime audio format mapping metadata."""
    return ELEVENLABS_REALTIME_AUDIO_FORMATS.get(audio_format)


def validate_elevenlabs_keyterms(terms: list[Any]) -> None:
    """Validate ElevenLabs keyterms inventory and limits."""
    if len(terms) > ELEVENLABS_MAX_KEYTERMS:
        raise HTTPException(
            status_code=400,
            detail=f"keyterms cannot exceed {ELEVENLABS_MAX_KEYTERMS} terms",
        )

    for term in terms:
        if not isinstance(term, str):
            raise HTTPException(
                status_code=400,
                detail="Each keyterm must be a string",
            )
        if len(term) > ELEVENLABS_MAX_KEYTERM_CHARS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Each keyterm must be at most "
                    f"{ELEVENLABS_MAX_KEYTERM_CHARS} characters, got {len(term)}"
                ),
            )
        word_count = len([w for w in term.split() if w])
        if word_count > ELEVENLABS_MAX_KEYTERM_WORDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Each keyterm may contain at most "
                    f"{ELEVENLABS_MAX_KEYTERM_WORDS} words, got {word_count}"
                ),
            )
