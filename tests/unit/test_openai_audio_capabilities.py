"""Unit tests for OpenAI capability table and request validation helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dalston.gateway.api.v1.openai_audio import (
    OpenAIEndpoint,
    build_openai_rate_limit_headers,
    is_openai_model_supported_for_endpoint,
    map_openai_runtime_model,
    validate_openai_request,
)


def test_model_endpoint_support_is_table_driven() -> None:
    assert is_openai_model_supported_for_endpoint(
        "whisper-1", OpenAIEndpoint.TRANSLATIONS
    )
    assert not is_openai_model_supported_for_endpoint(
        "gpt-4o-transcribe", OpenAIEndpoint.TRANSLATIONS
    )


def test_validate_openai_request_rejects_endpoint_mismatch() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_openai_request(
            model="gpt-4o-transcribe",
            response_format="json",
            timestamp_granularities=None,
            endpoint=OpenAIEndpoint.TRANSLATIONS,
        )

    error = exc_info.value.detail["error"]
    assert error["code"] == "invalid_model"
    assert error["param"] == "model"


def test_validate_openai_request_rejects_prompt_over_limit() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_openai_request(
            model="whisper-1",
            response_format="json",
            timestamp_granularities=None,
            endpoint=OpenAIEndpoint.TRANSCRIPTIONS,
            prompt="token " * 300,
        )

    error = exc_info.value.detail["error"]
    assert error["code"] == "invalid_prompt"
    assert error["param"] == "prompt"


def test_build_openai_rate_limit_headers_includes_openai_names() -> None:
    headers = build_openai_rate_limit_headers(limit=60, remaining=59, reset_seconds=10)

    assert headers["X-RateLimit-Limit"] == "60"
    assert headers["X-RateLimit-Remaining"] == "59"
    assert headers["X-RateLimit-Limit-Requests"] == "60"
    assert headers["X-RateLimit-Remaining-Requests"] == "59"
    assert headers["X-RateLimit-Reset-Requests"] == "10"


def test_validate_openai_request_allows_chunking_auto_for_supported_model() -> None:
    validate_openai_request(
        model="gpt-4o-transcribe",
        response_format="json",
        timestamp_granularities=None,
        endpoint=OpenAIEndpoint.TRANSCRIPTIONS,
        chunking_strategy={"type": "auto"},
    )


def test_validate_openai_request_rejects_chunking_for_unsupported_model() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_openai_request(
            model="whisper-1",
            response_format="json",
            timestamp_granularities=None,
            endpoint=OpenAIEndpoint.TRANSCRIPTIONS,
            chunking_strategy={"type": "auto"},
        )

    error = exc_info.value.detail["error"]
    assert error["code"] == "invalid_request"
    assert error["param"] == "chunking_strategy"


def test_validate_openai_request_rejects_known_speaker_names_when_unsupported() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_openai_request(
            model="gpt-4o-transcribe",
            response_format="json",
            timestamp_granularities=None,
            endpoint=OpenAIEndpoint.TRANSCRIPTIONS,
            known_speaker_names=["Alice", "Bob"],
        )

    error = exc_info.value.detail["error"]
    assert error["code"] == "invalid_request"
    assert error["param"] == "known_speaker_names"


def test_validate_openai_request_accepts_known_speaker_names_for_diarize_model() -> (
    None
):
    validate_openai_request(
        model="gpt-4o-transcribe-diarize",
        response_format="diarized_json",
        timestamp_granularities=None,
        endpoint=OpenAIEndpoint.TRANSCRIPTIONS,
        known_speaker_names=["Alice", "Bob"],
    )


def test_map_openai_runtime_model_returns_configured_default_variant() -> None:
    runtime_model_id = map_openai_runtime_model("whisper-1")
    assert runtime_model_id == "Systran/faster-whisper-base"
