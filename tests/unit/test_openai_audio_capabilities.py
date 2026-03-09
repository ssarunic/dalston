"""Unit tests for OpenAI capability table and request validation helpers."""

from __future__ import annotations

import builtins
import sys

import pytest
from fastapi import HTTPException, Response

from dalston.gateway.api.v1.openai_audio import (
    OpenAIEndpoint,
    _estimate_prompt_tokens,
    attach_openai_rate_limit_headers,
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


def test_attach_openai_rate_limit_headers_updates_response_for_json_payload() -> None:
    response = Response()
    payload = {"text": "ok"}

    attach_openai_rate_limit_headers(
        payload,
        response,
        {"X-RateLimit-Remaining-Requests": "59"},
    )

    assert response.headers["X-RateLimit-Remaining-Requests"] == "59"


def test_attach_openai_rate_limit_headers_updates_payload_response() -> None:
    payload = Response()
    response = Response()

    attach_openai_rate_limit_headers(
        payload,
        response,
        {"X-RateLimit-Remaining-Requests": "42"},
    )

    assert payload.headers["X-RateLimit-Remaining-Requests"] == "42"
    assert "X-RateLimit-Remaining-Requests" not in response.headers


def test_estimate_prompt_tokens_uses_fallback_when_tiktoken_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def _patched_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("tiktoken unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    assert _estimate_prompt_tokens("one two three") >= 3


def test_estimate_prompt_tokens_surfaces_tiktoken_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenTokenizer:
        @staticmethod
        def get_encoding(_name: str):
            raise RuntimeError("broken tokenizer")

    monkeypatch.setitem(sys.modules, "tiktoken", _BrokenTokenizer())
    with pytest.raises(RuntimeError, match="broken tokenizer"):
        _estimate_prompt_tokens("hello")
