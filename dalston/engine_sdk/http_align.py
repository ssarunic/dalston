"""HTTP server subclass for alignment engines.

Adds ``POST /v1/align`` to the base ``EngineHTTPServer`` endpoints.

Alignment requires both audio and the transcript from a prior
transcription stage.  The transcript is accepted as a JSON string
in the ``transcript`` form field.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from dalston.engine_sdk.http_server import (
    EngineHTTPServer,
    resolve_audio,
    run_engine_http,
)
from dalston.engine_sdk.types import TaskRequest


class AlignHTTPServer(EngineHTTPServer):
    """HTTP server for alignment engines.

    Extends the base server with ``POST /v1/align`` which accepts
    audio (file upload or URL) plus a transcript JSON from the prior
    transcription stage, delegates to ``engine.process()``, and
    returns the alignment result.
    """

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        engine = self._engine
        engine_id = self._engine_id

        @app.post("/v1/align")
        async def align(
            file: Annotated[
                UploadFile | None,
                File(description="Audio file to align"),
            ] = None,
            audio_url: Annotated[
                str | None,
                Form(description="URL to audio file (S3 URI or HTTPS)"),
            ] = None,
            loaded_model_id: Annotated[
                str | None, Form(description="Alignment model to use")
            ] = None,
            transcript: Annotated[
                str | None,
                Form(
                    description=(
                        "Transcript JSON from prior transcription stage. "
                        "Must contain 'text', 'segments', and 'language' fields."
                    )
                ),
            ] = None,
            return_char_alignments: Annotated[
                bool,
                Form(description="Return character-level alignments"),
            ] = False,
        ) -> dict:
            if not transcript:
                raise HTTPException(
                    400,
                    "The 'transcript' field is required — pass the JSON output "
                    "from the transcription stage.",
                )

            try:
                transcript_data = json.loads(transcript)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    400,
                    f"Invalid JSON in 'transcript' field: {e}",
                ) from None

            audio_path = await resolve_audio(file, audio_url)

            config: dict = {}
            if loaded_model_id:
                config["loaded_model_id"] = loaded_model_id
            config["return_char_alignments"] = return_char_alignments

            task_id = str(uuid4())
            task_request = TaskRequest(
                task_id=task_id,
                job_id="http",
                stage="align",
                config=config,
                payload={},
                audio_path=audio_path,
                previous_responses={"transcribe": transcript_data},
            )
            return await run_engine_http(
                engine=engine,
                engine_id=engine_id,
                task_request=task_request,
                stage="align",
            )
