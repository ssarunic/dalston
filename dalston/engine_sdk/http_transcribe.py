"""HTTP server subclass for transcription engines.

Adds ``POST /v1/transcribe`` to the base ``EngineHTTPServer`` endpoints.
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


class TranscribeHTTPServer(EngineHTTPServer):
    """HTTP server for transcription engines.

    Extends the base server with ``POST /v1/transcribe`` which accepts
    either a file upload or an audio URL, delegates to
    ``engine.process()``, and returns the result.
    """

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        engine = self._engine
        engine_id = self._engine_id

        @app.post("/v1/transcribe")
        async def transcribe(
            file: Annotated[
                UploadFile | None,
                File(description="Audio file to transcribe"),
            ] = None,
            audio_url: Annotated[
                str | None,
                Form(description="URL to audio file (S3 URI or HTTPS)"),
            ] = None,
            model: Annotated[str | None, Form(description="Model to use")] = None,
            language: Annotated[str | None, Form(description="Language code")] = None,
            word_timestamps: Annotated[
                bool, Form(description="Include word-level timestamps")
            ] = True,
            vocabulary: Annotated[
                str | None,
                Form(description="Comma-separated vocabulary terms"),
            ] = None,
            channel: Annotated[
                int | None, Form(description="Audio channel to transcribe")
            ] = None,
        ) -> dict:
            audio_path = await resolve_audio(file, audio_url)

            config: dict = {}
            if model:
                config["loaded_model_id"] = model
            if language:
                config["language"] = language
            config["word_timestamps"] = word_timestamps
            if vocabulary:
                config["vocabulary"] = [v.strip() for v in vocabulary.split(",")]
            if channel is not None:
                config["channel"] = channel

            task_id = str(uuid4())
            task_request = TaskRequest(
                task_id=task_id,
                job_id="http",
                stage="transcribe",
                config=config,
                payload={"audio_url": audio_url} if audio_url else {},
                audio_path=audio_path,
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="transcribe",
            )
