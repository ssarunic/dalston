"""HTTP server subclass for diarization engines.

Adds ``POST /v1/diarize`` to the base ``EngineHTTPServer`` endpoints.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile

from dalston.engine_sdk.http_server import (
    EngineHTTPServer,
    resolve_audio,
    run_engine_http,
)
from dalston.engine_sdk.types import TaskRequest


class DiarizeHTTPServer(EngineHTTPServer):
    """HTTP server for diarization engines.

    Extends the base server with ``POST /v1/diarize`` which accepts
    either a file upload or an audio URL, delegates to
    ``engine.process()``, and returns the result.
    """

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        engine = self._engine
        engine_id = self._engine_id

        @app.post("/v1/diarize")
        async def diarize(
            file: Annotated[
                UploadFile | None,
                File(description="Audio file to diarize"),
            ] = None,
            audio_url: Annotated[
                str | None,
                Form(description="URL to audio file (S3 URI or HTTPS)"),
            ] = None,
            loaded_model_id: Annotated[
                str | None, Form(description="Model to use")
            ] = None,
            num_speakers: Annotated[
                int | None, Form(description="Exact number of speakers")
            ] = None,
            min_speakers: Annotated[
                int | None, Form(description="Minimum speakers")
            ] = None,
            max_speakers: Annotated[
                int | None, Form(description="Maximum speakers")
            ] = None,
        ) -> dict:
            audio_path = await resolve_audio(file, audio_url)

            config: dict = {}
            if loaded_model_id:
                config["loaded_model_id"] = loaded_model_id
            if num_speakers is not None:
                config["num_speakers"] = num_speakers
            if min_speakers is not None:
                config["min_speakers"] = min_speakers
            if max_speakers is not None:
                config["max_speakers"] = max_speakers

            task_id = str(uuid4())
            task_request = TaskRequest(
                task_id=task_id,
                job_id="http",
                stage="diarize",
                config=config,
                payload={"audio_url": audio_url} if audio_url else {},
                audio_path=audio_path,
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="diarize",
            )
