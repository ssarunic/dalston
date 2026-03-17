"""HTTP server subclass for transcription engines.

Adds ``POST /v1/transcribe`` to the base ``EngineHTTPServer`` endpoints.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from dalston.engine_sdk.http_server import (
    EngineHTTPServer,
    download_audio,
    run_engine_http,
)
from dalston.engine_sdk.types import TaskRequest


class TranscribeHTTPRequest(BaseModel):
    """HTTP request body for ``POST /v1/transcribe``."""

    task_id: str | None = None
    job_id: str | None = None
    audio_uri: str
    loaded_model_id: str | None = None
    language: str | None = None
    word_timestamps: bool = True
    vocabulary: list[str] | None = None
    channel: int | None = None
    timeout_seconds: int = 300


class TranscribeHTTPServer(EngineHTTPServer):
    """HTTP server for transcription engines.

    Extends the base server with ``POST /v1/transcribe`` which accepts an
    S3 URI, delegates to ``engine.process()``, and returns the result.
    """

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        engine = self._engine
        engine_id = self._engine_id

        @app.post("/v1/transcribe")
        async def transcribe(request: TranscribeHTTPRequest) -> dict:
            task_id = request.task_id or str(uuid4())
            job_id = request.job_id or "http"
            audio_path = await _download(request.audio_uri)

            task_request = TaskRequest(
                task_id=task_id,
                job_id=job_id,
                stage="transcribe",
                config=_build_config(request),
                payload={"audio_uri": request.audio_uri},
                audio_path=audio_path,
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="transcribe",
            )


async def _download(audio_uri: str) -> Path:  # noqa: F821
    """Download audio in a thread (blocking S3 I/O)."""
    import asyncio

    return await asyncio.to_thread(download_audio, audio_uri)


def _build_config(request: TranscribeHTTPRequest) -> dict:
    """Extract engine config from the HTTP request."""
    config: dict = {}
    if request.loaded_model_id:
        config["loaded_model_id"] = request.loaded_model_id
    if request.language:
        config["language"] = request.language
    config["word_timestamps"] = request.word_timestamps
    if request.vocabulary:
        config["vocabulary"] = request.vocabulary
    if request.channel is not None:
        config["channel"] = request.channel
    return config
