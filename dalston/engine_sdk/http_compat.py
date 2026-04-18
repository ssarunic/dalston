"""OpenAI- and ElevenLabs-compatible HTTP endpoints for naked engines.

When an engine exposes ``TranscribeHTTPServer``, it also serves:

- ``POST /v1/audio/transcriptions`` — OpenAI Audio Transcription contract
- ``POST /v1/speech-to-text``       — ElevenLabs Speech-to-Text contract

This lets a single engine container answer OpenAI / ElevenLabs SDK calls
directly, without the gateway. The adapters are intentionally minimal:
sync only, single channel, no persistence, no webhooks, no rate limits.
Features that require gateway-side state (async jobs, webhooks, exports,
multi-channel, additional_formats, diarized/speaker_id) are not supported
here — use the gateway if you need them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile

from dalston.engine_sdk.http_server import resolve_audio, run_engine_http
from dalston.engine_sdk.types import TaskRequest

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


_OPENAI_RESPONSE_FORMATS = {"json", "text", "verbose_json"}


def register_compat_endpoints(
    app: FastAPI,
    engine: Engine,
    engine_id: str,
) -> None:
    """Attach OpenAI + ElevenLabs compatible POST routes to ``app``."""
    _register_openai_transcriptions(app, engine, engine_id)
    _register_elevenlabs_speech_to_text(app, engine, engine_id)


# ---------------------------------------------------------------------------
# OpenAI: POST /v1/audio/transcriptions
# ---------------------------------------------------------------------------


def _register_openai_transcriptions(
    app: FastAPI,
    engine: Engine,
    engine_id: str,
) -> None:
    @app.post("/v1/audio/transcriptions", response_model=None)
    async def openai_transcriptions(
        file: Annotated[UploadFile | None, File()] = None,
        model: Annotated[str | None, Form()] = None,
        language: Annotated[str | None, Form()] = None,
        prompt: Annotated[str | None, Form()] = None,  # noqa: ARG001
        response_format: Annotated[str, Form()] = "json",
        temperature: Annotated[float | None, Form()] = None,  # noqa: ARG001
        timestamp_granularities: Annotated[list[str] | None, Form()] = None,
        timestamp_granularities_bracket: Annotated[
            list[str] | None, Form(alias="timestamp_granularities[]")
        ] = None,
    ) -> Response | dict[str, Any]:
        # OpenAI SDK sends ``timestamp_granularities[]=``; curl sends the
        # unbracketed repeated field. Accept both.
        granularities = timestamp_granularities or timestamp_granularities_bracket
        if response_format not in _OPENAI_RESPONSE_FORMATS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": (
                            f"Invalid response_format: {response_format}. "
                            f"Supported: {sorted(_OPENAI_RESPONSE_FORMATS)}."
                        ),
                        "type": "invalid_request_error",
                        "param": "response_format",
                        "code": "invalid_response_format",
                    }
                },
            )
        if granularities and response_format != "verbose_json":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": (
                            "timestamp_granularities requires "
                            "response_format=verbose_json"
                        ),
                        "type": "invalid_request_error",
                        "param": "timestamp_granularities",
                        "code": "invalid_request",
                    }
                },
            )

        audio_path = await resolve_audio(file, None)

        config: dict[str, Any] = {"word_timestamps": True}
        if language:
            config["language"] = language

        task_request = TaskRequest(
            task_id=str(uuid4()),
            job_id="http",
            stage="transcribe",
            config=config,
            payload={},
            audio_path=audio_path,
        )
        transcript = await run_engine_http(
            engine=engine,
            engine_id=engine_id,
            task_request=task_request,
            stage="transcribe",
        )
        return _format_openai(
            transcript,
            response_format=response_format,
            timestamp_granularities=granularities,
            requested_model=model,
        )


def _format_openai(
    transcript: dict[str, Any],
    *,
    response_format: str,
    timestamp_granularities: list[str] | None,
    requested_model: str | None,
) -> Response | dict[str, Any]:
    text = str(transcript.get("text", ""))

    if response_format == "text":
        return Response(content=text, media_type="text/plain")

    if response_format == "json":
        return {"text": text}

    # verbose_json
    language = _first_str(
        transcript.get("language"),
        _nested(transcript, "metadata", "language"),
        default="en",
    )
    duration = _first_float(
        transcript.get("duration"),
        _nested(transcript, "metadata", "duration"),
        default=0.0,
    )

    segments_out: list[dict[str, Any]] = []
    raw_segments = transcript.get("segments") or []
    for i, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            continue
        meta = seg.get("metadata") or {}
        segments_out.append(
            {
                "id": i,
                "seek": 0,
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": str(seg.get("text", "")),
                "tokens": list(meta.get("tokens") or []),
                "temperature": _coerce_float(meta.get("temperature"), 0.0),
                "avg_logprob": _coerce_float(meta.get("avg_logprob"), -0.5),
                "compression_ratio": _coerce_float(meta.get("compression_ratio"), 1.0),
                "no_speech_prob": _coerce_float(meta.get("no_speech_prob"), 0.0),
            }
        )

    payload: dict[str, Any] = {
        "task": "transcribe",
        "language": language,
        "duration": duration,
        "text": text,
        "segments": segments_out,
    }

    if timestamp_granularities and "word" in timestamp_granularities:
        words_out: list[dict[str, Any]] = []
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            for w in seg.get("words") or []:
                if not isinstance(w, dict):
                    continue
                words_out.append(
                    {
                        "word": str(w.get("text", w.get("word", ""))),
                        "start": float(w.get("start", 0.0)),
                        "end": float(w.get("end", 0.0)),
                    }
                )
        payload["words"] = words_out

    if requested_model:
        payload.setdefault("model", requested_model)

    return payload


# ---------------------------------------------------------------------------
# ElevenLabs: POST /v1/speech-to-text
# ---------------------------------------------------------------------------


def _register_elevenlabs_speech_to_text(
    app: FastAPI,
    engine: Engine,
    engine_id: str,
) -> None:
    @app.post("/v1/speech-to-text")
    async def elevenlabs_speech_to_text(
        file: Annotated[UploadFile | None, File()] = None,
        cloud_storage_url: Annotated[str | None, Form()] = None,
        model_id: Annotated[str, Form()] = "scribe_v1",  # noqa: ARG001
        language_code: Annotated[str | None, Form()] = None,
        timestamps_granularity: Annotated[str, Form()] = "word",
        tag_audio_events: Annotated[bool, Form()] = True,  # noqa: ARG001
        diarize: Annotated[bool, Form()] = False,
        num_speakers: Annotated[int | None, Form()] = None,  # noqa: ARG001
        keyterms: Annotated[str | None, Form()] = None,
        webhook: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        if diarize:
            raise HTTPException(
                status_code=400,
                detail=(
                    "diarize=true is not supported on the naked engine. "
                    "Run the gateway with a diarize stage to enable it."
                ),
            )
        if webhook:
            raise HTTPException(
                status_code=400,
                detail=(
                    "webhook mode is not supported on the naked engine. "
                    "Use the gateway for async/webhook delivery."
                ),
            )

        audio_path = await resolve_audio(file, cloud_storage_url)

        config: dict[str, Any] = {
            "word_timestamps": timestamps_granularity != "none",
        }
        if language_code:
            config["language"] = language_code
        if keyterms is not None:
            try:
                parsed = json.loads(keyterms)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid JSON in keyterms: {e}",
                ) from e
            if not isinstance(parsed, list) or not all(
                isinstance(t, str) for t in parsed
            ):
                raise HTTPException(
                    status_code=400,
                    detail="keyterms must be a JSON array of strings",
                )
            if parsed:
                config["vocabulary"] = parsed

        task_request = TaskRequest(
            task_id=str(uuid4()),
            job_id="http",
            stage="transcribe",
            config=config,
            payload={},
            audio_path=audio_path,
        )
        transcript = await run_engine_http(
            engine=engine,
            engine_id=engine_id,
            task_request=task_request,
            stage="transcribe",
        )
        return _format_elevenlabs(transcript)


def _format_elevenlabs(transcript: dict[str, Any]) -> dict[str, Any]:
    language = _first_str(
        transcript.get("language"),
        _nested(transcript, "metadata", "language"),
        default="und",
    )
    language_probability = _first_float(
        transcript.get("language_confidence"),
        _nested(transcript, "metadata", "language_confidence"),
        default=0.0,
    )

    words_out: list[dict[str, Any]] = []
    for seg in transcript.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            text_value = str(w.get("text", w.get("word", "")))
            meta = w.get("metadata") or {}
            words_out.append(
                {
                    "text": text_value,
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "type": (
                        "spacing" if text_value and text_value.isspace() else "word"
                    ),
                    "speaker_id": None,
                    "logprob": _coerce_float(meta.get("logprob"), 0.0),
                    "characters": None,
                }
            )

    return {
        "language_code": language,
        "language_probability": language_probability,
        "text": str(transcript.get("text", "")),
        "words": words_out,
        "entities": None,
        "additional_formats": None,
        "transcription_id": str(uuid4()),
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _nested(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _first_str(*values: Any, default: str) -> str:
    for v in values:
        if isinstance(v, str) and v:
            return v
    return default


def _first_float(*values: Any, default: float) -> float:
    for v in values:
        if isinstance(v, int | float):
            return float(v)
    return default


def _coerce_float(value: Any, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    return default
