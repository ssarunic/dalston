"""HTTP server subclass for diarization engines.

Adds ``POST /v1/diarize`` to the base ``EngineHTTPServer`` endpoints.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.http_server import EngineHTTPServer
from dalston.engine_sdk.types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


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

        @app.post("/v1/diarize")
        async def diarize(request: DiarizeHTTPRequest) -> dict:
            task_request = _to_task_request(request)
            ctx = BatchTaskContext.for_http(
                task_id=request.task_id or str(uuid4()),
                job_id=request.job_id or "http",
                engine_id=getattr(engine, "_engine_id", "unknown"),
            )

            result: TaskResponse = await asyncio.to_thread(
                engine.process, task_request, ctx
            )

            return _to_http_response(result, engine)


def _to_task_request(request: DiarizeHTTPRequest) -> TaskRequest:
    """Convert an HTTP request into a ``TaskRequest``."""
    config: dict = {}
    if request.loaded_model_id:
        config["loaded_model_id"] = request.loaded_model_id
    if request.num_speakers is not None:
        config["num_speakers"] = request.num_speakers
    if request.min_speakers is not None:
        config["min_speakers"] = request.min_speakers
    if request.max_speakers is not None:
        config["max_speakers"] = request.max_speakers

    return TaskRequest(
        task_id=request.task_id or str(uuid4()),
        job_id=request.job_id or "http",
        stage="diarize",
        config=config,
        payload={"audio_uri": request.audio_uri},
    )


def _to_http_response(result: TaskResponse, engine: Engine) -> dict:
    """Convert a ``TaskResponse`` into an HTTP-friendly dict."""
    data = result.to_dict()
    if "engine_id" not in data:
        data["engine_id"] = getattr(engine, "_engine_id", "unknown")
    return data
