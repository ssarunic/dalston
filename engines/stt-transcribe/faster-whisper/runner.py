"""Unified faster-whisper runner: one process, one model, both interfaces.

This runner creates a single FasterWhisperInference (one loaded model) and passes
it to both the batch engine adapter (queue polling) and the realtime engine
adapter (WebSocket server). An AdmissionController gates both paths to
prevent realtime starvation under batch load.

This is the M63 "unified engine instance" — batch and RT share the same
GPU-resident model instead of loading independent copies.

Usage:
    python -m engines.stt-transcribe.faster-whisper.runner

Environment variables (in addition to each adapter's own env vars):
    DALSTON_RT_RESERVATION: Min slots reserved for realtime (default: 2)
    DALSTON_BATCH_MAX_INFLIGHT: Max concurrent batch tasks (default: 4)
    DALSTON_TOTAL_CAPACITY: Total engine capacity (default: 6)

"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from typing import Any

import structlog

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)
from dalston.engine_sdk.inference.faster_whisper_inference import FasterWhisperInference

logger = structlog.get_logger()


class UnifiedFasterWhisperRunner:
    """Runs batch + realtime faster-whisper adapters in a single process.

    Key properties:
    - ONE FasterWhisperInference instance (one model in GPU memory)
    - ONE AdmissionController (shared QoS policy)
    - Batch adapter runs in a background thread (sync queue polling)
    - RT adapter runs in the async event loop (WebSocket server)

    The runner owns the lifecycle of both adapters and coordinates
    graceful shutdown across both.
    """

    def __init__(self) -> None:
        # Create single shared core
        self._core = FasterWhisperInference.from_env()

        # Create admission controller
        self._admission = AdmissionController(AdmissionConfig.from_env())

        # Adapters (created lazily in run())
        self._batch_engine: Any = None
        self._rt_engine: Any = None
        self._batch_thread: threading.Thread | None = None
        self._running = False

        logger.info(
            "unified_runner_init",
            device=self._core.device,
            compute_type=self._core.compute_type,
            admission=self._admission.get_status(),
        )

    def run(self) -> None:
        """Start the unified runner.

        This is the main entry point. It:
        1. Creates batch + RT engine adapters sharing the same core
        2. Starts batch adapter in a background thread
        3. Runs RT adapter in the main async loop
        4. Coordinates shutdown across both
        """
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async entry point that starts both adapters."""
        self._running = True

        from batch_engine import FasterWhisperBatchEngine
        from rt_engine import FasterWhisperRealtimeEngine

        # Create adapters sharing the same FasterWhisperInference
        self._batch_engine = FasterWhisperBatchEngine(core=self._core)
        self._rt_engine = FasterWhisperRealtimeEngine(core=self._core)

        # Wrap batch engine's process to check admission
        original_process = self._batch_engine.process

        def admitted_process(task_request, ctx):
            if not self._admission.admit_batch():
                logger.info(
                    "batch_task_rejected_by_admission",
                    task_id=task_request.task_id,
                    status=self._admission.get_status(),
                )
                raise BatchRejectedError("Admission controller rejected batch task")
            try:
                return original_process(task_request, ctx)
            finally:
                self._admission.release_batch()

        self._batch_engine.process = admitted_process

        # Wrap RT engine's session acceptance to check admission
        original_handle = self._rt_engine._handle_connection

        async def admitted_handle(websocket):
            if not self._admission.admit_rt():
                from dalston.common.ws_close_codes import WS_CLOSE_TRY_AGAIN_LATER

                logger.info(
                    "rt_session_rejected_by_admission",
                    status=self._admission.get_status(),
                )
                await websocket.close(
                    WS_CLOSE_TRY_AGAIN_LATER,
                    "Engine at capacity (admission control)",
                )
                return
            try:
                await original_handle(websocket)
            finally:
                self._admission.release_rt()

        self._rt_engine._handle_connection = admitted_handle

        # Start the batch engine's HTTP server (TranscribeHTTPServer) on
        # port 9100 BEFORE the RT engine starts.  This gives the FastAPI
        # server the port, so /v1/transcribe is available.  The RT engine's
        # _start_metrics_server will detect the port is in use and skip,
        # which is correct — all M79 endpoints are served by TranscribeHTTPServer.
        metrics_port = int(os.environ.get("DALSTON_METRICS_PORT", "9100"))
        self._http_server = self._batch_engine.create_http_server(port=metrics_port)
        self._http_task = asyncio.create_task(self._http_server.serve())
        logger.info("http_server_started", port=metrics_port)

        # Start batch adapter in background thread
        self._batch_thread = threading.Thread(
            target=self._run_batch,
            name="batch-adapter",
            daemon=True,
        )
        self._batch_thread.start()
        logger.info("batch_adapter_started")

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        # Run RT adapter in the main async loop
        logger.info("starting_rt_adapter")
        try:
            await self._rt_engine.run()
        finally:
            await self._shutdown()

    def _run_batch(self) -> None:
        """Run the batch engine adapter in a background thread."""
        try:
            self._batch_engine.run()
        except Exception as e:
            if self._running:
                logger.error("batch_adapter_error", error=str(e))

    async def _shutdown(self) -> None:
        """Graceful shutdown of both adapters."""
        if not self._running:
            return
        self._running = False
        logger.info("unified_runner_shutting_down")

        # Stop RT adapter
        if self._rt_engine:
            await self._rt_engine.shutdown()

        # Stop batch adapter
        if self._batch_engine and hasattr(self._batch_engine, "_runner"):
            runner = self._batch_engine._runner
            if runner:
                runner.stop()

        # Wait for batch thread
        if self._batch_thread and self._batch_thread.is_alive():
            self._batch_thread.join(timeout=10)

        # Shutdown shared core (unloads models)
        self._core.shutdown()

        logger.info(
            "unified_runner_stopped",
            final_admission_status=self._admission.get_status(),
        )


class BatchRejectedError(TaskDeferredError):
    """Raised when admission controller rejects a batch task.

    Inherits from TaskDeferredError so the EngineRunner skips both the
    failure publish and the stream ACK, leaving the message in the PEL
    for redelivery.
    """


# ---------------------------------------------------------------------------
# Import path: ensure the engine directory is on sys.path so that sibling
# modules (batch_engine, rt_engine) can be imported by name.
# ---------------------------------------------------------------------------


def _ensure_import_path() -> None:
    """Add this file's directory to sys.path for sibling imports."""
    import sys
    from pathlib import Path

    engine_dir = str(Path(__file__).resolve().parent)
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)


_ensure_import_path()


if __name__ == "__main__":
    runner = UnifiedFasterWhisperRunner()
    runner.run()
