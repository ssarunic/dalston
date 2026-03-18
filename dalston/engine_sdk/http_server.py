"""Base HTTP server exposing the engine interface contract.

Replaces the bare ``_MetricsHandler`` (``http.server.HTTPServer`` serving only
``/metrics`` and static ``/health``) with a FastAPI-based server that serves
the full engine interface contract.  The ``/metrics`` endpoint is preserved
for Prometheus compatibility.

Started by the ``EngineRunner`` in a background thread on port 9100
(configurable via ``DALSTON_HTTP_PORT`` / ``DALSTON_METRICS_PORT``).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response

import dalston.metrics
import dalston.telemetry
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    from dalston.engine_sdk.base import Engine


class EngineHTTPServer:
    """Lightweight HTTP server exposing the engine interface contract.

    Wraps an Engine instance and serves:

    - ``GET  /health``          → ``engine.health_check()``
    - ``GET  /metrics``         → ``prometheus_client``
    - ``GET  /v1/capabilities`` → ``engine.get_capabilities()``

    Subclasses register stage-specific POST endpoints (e.g.
    ``/v1/transcribe``, ``/v1/diarize``) via ``_register_stage_endpoints``.
    """

    def __init__(
        self,
        engine: Engine,
        port: int = 9100,
        host: str = "0.0.0.0",
    ) -> None:
        self._engine = engine
        self._port = port
        self._host = host
        self._engine_id = engine.get_capabilities().engine_id
        self._app = self._build_app()

    @property
    def app(self) -> FastAPI:
        """Expose the FastAPI app (useful for testing)."""
        return self._app

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title=f"Dalston Engine: {self._engine_id}",
            docs_url=None,
            redoc_url=None,
        )

        engine = self._engine

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return await asyncio.to_thread(engine.health_check)

        @app.get("/metrics")
        async def metrics() -> Response:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

            content = generate_latest()
            return Response(content=content, media_type=CONTENT_TYPE_LATEST)

        @app.get("/v1/capabilities")
        async def capabilities() -> dict[str, Any]:
            caps = engine.get_capabilities()
            return caps.model_dump(mode="json")

        self._register_stage_endpoints(app)

        return app

    def _register_stage_endpoints(self, app: FastAPI) -> None:
        """Register stage-specific POST endpoints.

        Override in subclasses to add ``/v1/transcribe``, ``/v1/diarize``,
        etc.  The default implementation is a no-op so that engines without
        a stage-specific HTTP endpoint still get ``/health``, ``/metrics``,
        and ``/v1/capabilities``.
        """

    async def serve(self) -> None:
        """Run the HTTP server (called as asyncio task by runner)."""
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()


# ---------------------------------------------------------------------------
# Shared helpers for stage-specific HTTP servers
# ---------------------------------------------------------------------------

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


async def resolve_audio(
    file: UploadFile | None,
    audio_url: str | None,
) -> Path:
    """Resolve audio input to a local file path.

    Accepts either a multipart file upload or a URL (S3 URI or HTTPS).
    Exactly one must be provided.  Returns the local path; caller must
    clean up via ``cleanup_audio(path)``.
    """
    if file and audio_url:
        raise HTTPException(400, "Provide either 'file' or 'audio_url', not both")
    if not file and not audio_url:
        raise HTTPException(400, "Provide 'file' or 'audio_url'")

    if file:
        return await _save_upload(file)
    assert audio_url is not None
    return await asyncio.to_thread(_download_url, audio_url)


async def _save_upload(file: UploadFile) -> Path:
    """Save an uploaded file to a temp directory (streaming, not buffered)."""
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    temp_dir = Path(tempfile.mkdtemp(prefix="dalston_http_"))
    dest = temp_dir / f"audio{suffix}"
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
                f.write(chunk)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return dest


def _download_url(url: str) -> Path:
    """Download audio from an S3 URI or HTTPS URL to a temp directory."""
    temp_dir = Path(tempfile.mkdtemp(prefix="dalston_http_"))
    dest = temp_dir / "audio.wav"

    try:
        if url.startswith("s3://"):
            from dalston.engine_sdk import io

            return io.download_file(url, dest)

        # HTTPS / HTTP URL — stream to disk with timeout
        resp = urllib.request.urlopen(url, timeout=60)  # noqa: S310
        with dest.open("wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return dest


def cleanup_audio(audio_path: Path | None) -> None:
    """Remove the temp directory created by ``resolve_audio``."""
    if audio_path and audio_path.parent.name.startswith("dalston_http_"):
        shutil.rmtree(audio_path.parent, ignore_errors=True)


def to_http_response(result: TaskResponse, engine_id: str) -> dict:
    """Convert a ``TaskResponse`` into an HTTP-friendly dict."""
    data = result.to_dict()
    if "engine_id" not in data:
        data["engine_id"] = engine_id
    return data


async def run_engine_http(
    *,
    engine: Engine,
    engine_id: str,
    task_request: TaskRequest,
    stage: str,
) -> dict:
    """Shared handler body: process a task request and return a response dict.

    Calls ``engine.process()`` in a thread, cleans up the temp directory,
    and formats the response.  Wraps the call in a root span so that
    inference sub-spans (M76) have a parent in Jaeger.
    """
    endpoint = f"/v1/{stage}"
    start = time.monotonic()
    status_code = 200

    ctx = BatchTaskContext.for_http(
        task_id=task_request.task_id,
        job_id=task_request.job_id,
        engine_id=engine_id,
        stage=stage,
    )
    try:
        with dalston.telemetry.create_span(
            f"engine.{engine_id}.http_process",
            attributes={
                "dalston.engine_id": engine_id,
                "dalston.stage": stage,
                "dalston.task_id": task_request.task_id,
                "dalston.transport": "http",
            },
        ):
            result: TaskResponse = await asyncio.to_thread(
                engine.process, task_request, ctx
            )
            return to_http_response(result, engine_id)
    except Exception:
        status_code = 500
        raise
    finally:
        cleanup_audio(task_request.audio_path)
        duration = time.monotonic() - start
        dalston.metrics.observe_engine_direct_request(
            engine_id, endpoint, status_code, duration
        )
        dalston.metrics.inc_engine_direct_requests(engine_id, endpoint, status_code)
