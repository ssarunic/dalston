"""HTTP server subclass for combined (composite) engines.

Adds ``POST /v1/transcribe_and_diarize`` to the base ``EngineHTTPServer``
endpoints.  Also exposes the individual ``/v1/transcribe`` and ``/v1/diarize``
endpoints so that the combined engine satisfies the interface contract for
both stages independently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile

from dalston.engine_sdk.http_server import (
    EngineHTTPServer,
    resolve_audio,
    run_engine_http,
)
from dalston.engine_sdk.types import TaskRequest

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


class CombinedHTTPServer(EngineHTTPServer):
    """HTTP server for combined transcription + diarization engines.

    Extends the base server with:

    - ``POST /v1/transcribe`` — transcription only
    - ``POST /v1/diarize`` — diarization only
    - ``POST /v1/transcribe_and_diarize`` — both in parallel, merged result
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
            loaded_model_id: Annotated[
                str | None, Form(description="Model to use")
            ] = None,
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

            config: dict = {"_stage": "transcribe"}
            if loaded_model_id:
                config["loaded_model_id"] = loaded_model_id
            if language:
                config["language"] = language
            config["word_timestamps"] = word_timestamps
            if vocabulary:
                config["vocabulary"] = [v.strip() for v in vocabulary.split(",")]
            if channel is not None:
                config["channel"] = channel

            task_request = TaskRequest(
                task_id=str(uuid4()),
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

            config: dict = {"_stage": "diarize"}
            if loaded_model_id:
                config["loaded_model_id"] = loaded_model_id
            if num_speakers is not None:
                config["num_speakers"] = num_speakers
            if min_speakers is not None:
                config["min_speakers"] = min_speakers
            if max_speakers is not None:
                config["max_speakers"] = max_speakers

            task_request = TaskRequest(
                task_id=str(uuid4()),
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

        @app.post("/v1/transcribe_and_diarize")
        async def transcribe_and_diarize(
            file: Annotated[
                UploadFile | None,
                File(description="Audio file to process"),
            ] = None,
            audio_url: Annotated[
                str | None,
                Form(description="URL to audio file (S3 URI or HTTPS)"),
            ] = None,
            loaded_model_id: Annotated[
                str | None,
                Form(description="Transcription model to use"),
            ] = None,
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
            diarize_model_id: Annotated[
                str | None,
                Form(description="Diarization model to use"),
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

            config: dict = {"_stage": "combined"}
            if loaded_model_id:
                config["loaded_model_id"] = loaded_model_id
            if language:
                config["language"] = language
            config["word_timestamps"] = word_timestamps
            if vocabulary:
                config["vocabulary"] = [v.strip() for v in vocabulary.split(",")]
            if channel is not None:
                config["channel"] = channel
            if diarize_model_id:
                config["diarize_model_id"] = diarize_model_id
            if num_speakers is not None:
                config["num_speakers"] = num_speakers
            if min_speakers is not None:
                config["min_speakers"] = min_speakers
            if max_speakers is not None:
                config["max_speakers"] = max_speakers

            task_request = TaskRequest(
                task_id=str(uuid4()),
                job_id="http",
                stage="combined",
                config=config,
                payload={"audio_url": audio_url} if audio_url else {},
                audio_path=audio_path,
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="combined",
            )
