"""HTTP server subclass for diarization engines.

Adds ``POST /v1/diarize`` to the base ``EngineHTTPServer`` endpoints.
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


class DiarizeHTTPRequest(BaseModel):
    """HTTP request body for ``POST /v1/diarize``."""

    task_id: str | None = None
    job_id: str | None = None
    audio_uri: str
    loaded_model_id: str | None = None
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    timeout_seconds: int = 180


class DiarizeHTTPServer(EngineHTTPServer):
    """HTTP server for diarization engines.

    Extends the base server with ``POST /v1/diarize`` which accepts an
    S3 URI, delegates to ``engine.process()``, and returns the result.
    """

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        engine = self._engine
        engine_id = self._engine_id

        @app.post("/v1/diarize")
        async def diarize(request: DiarizeHTTPRequest) -> dict:
            task_id = request.task_id or str(uuid4())
            job_id = request.job_id or "http"
            audio_path = await _download(request.audio_uri)

            task_request = TaskRequest(
                task_id=task_id,
                job_id=job_id,
                stage="diarize",
                config=_build_config(request),
                payload={"audio_uri": request.audio_uri},
                audio_path=audio_path,
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="diarize",
            )


async def _download(audio_uri: str) -> Path:  # noqa: F821
    """Download audio in a thread (blocking S3 I/O)."""
    import asyncio

    return await asyncio.to_thread(download_audio, audio_uri)


def _build_config(request: DiarizeHTTPRequest) -> dict:
    """Extract engine config from the HTTP request."""
    config: dict = {}
    if request.loaded_model_id:
        config["loaded_model_id"] = request.loaded_model_id
    if request.num_speakers is not None:
        config["num_speakers"] = request.num_speakers
    if request.min_speakers is not None:
        config["min_speakers"] = request.min_speakers
    if request.max_speakers is not None:
        config["max_speakers"] = request.max_speakers
    return config
