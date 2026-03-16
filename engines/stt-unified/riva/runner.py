"""Unified Riva runner: one process, one gRPC channel, both interfaces.

This runner creates a single RivaClient (one gRPC channel to the NIM
sidecar) and passes it to both the batch engine adapter (queue polling)
and the realtime engine adapter (WebSocket server).  An AdmissionController
gates both paths to prevent realtime starvation under batch load.

Unlike other unified engines (ONNX, faster-whisper) where the shared
resource is a GPU-resident model, here the shared resource is the gRPC
connection to an external NIM sidecar.  The consolidation benefit is
operational consistency -- one container, one deployment unit, one set
of env vars -- rather than GPU memory savings.

Usage:
    python engines/stt-unified/riva/runner.py

Environment variables (in addition to each adapter's own env vars):
    DALSTON_RIVA_URI: gRPC endpoint for Riva NIM (default: localhost:50051)
    DALSTON_RIVA_CHUNK_MS: Chunk size in ms for batch streaming (default: 100)
    DALSTON_RT_RESERVATION: Min slots reserved for realtime (default: 2)
    DALSTON_BATCH_MAX_INFLIGHT: Max concurrent batch tasks (default: 4)
    DALSTON_TOTAL_CAPACITY: Total engine capacity (default: 6)
"""

from __future__ import annotations

import asyncio
import signal
import threading
from typing import Any

import structlog
from riva_client import RivaClient

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)

logger = structlog.get_logger()


class UnifiedRivaRunner:
    """Runs batch + realtime Riva adapters in a single process.

    Key properties:
    - ONE RivaClient instance (one gRPC channel to NIM)
    - ONE AdmissionController (shared QoS policy)
    - Batch adapter runs in a background thread (sync queue polling)
    - RT adapter runs in the async event loop (WebSocket server)
    """

    def __init__(self) -> None:
        self._core = RivaClient.from_env()
        self._admission = AdmissionController(AdmissionConfig.from_env())

        self._batch_engine: Any = None
        self._rt_engine: Any = None
        self._batch_thread: threading.Thread | None = None
        self._running = False

        logger.info(
            "unified_riva_runner_init",
            riva_uri=self._core.uri,
            chunk_ms=self._core.chunk_ms,
            admission=self._admission.get_status(),
        )

    def run(self) -> None:
        """Start the unified runner."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async entry point that starts both adapters."""
        self._running = True

        from batch_engine import RivaBatchEngine
        from rt_engine import RivaRealtimeEngine

        self._batch_engine = RivaBatchEngine(core=self._core)
        self._rt_engine = RivaRealtimeEngine(core=self._core)

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
        logger.info("unified_riva_runner_shutting_down")

        if self._rt_engine:
            await self._rt_engine.shutdown()

        if self._batch_engine and hasattr(self._batch_engine, "_runner"):
            runner = self._batch_engine._runner
            if runner:
                runner.stop()

        if self._batch_thread and self._batch_thread.is_alive():
            self._batch_thread.join(timeout=10)

        self._core.shutdown()

        logger.info(
            "unified_riva_runner_stopped",
            final_admission_status=self._admission.get_status(),
        )


class BatchRejectedError(TaskDeferredError):
    """Raised when admission controller rejects a batch task.

    Inherits from TaskDeferredError so the EngineRunner skips both the
    failure publish and the stream ACK, leaving the message in the PEL
    for redelivery.
    """


def _ensure_import_path() -> None:
    """Ensure this directory is on sys.path for sibling imports."""
    import sys
    from pathlib import Path

    engine_dir = str(Path(__file__).resolve().parent)
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)


_ensure_import_path()


if __name__ == "__main__":
    runner = UnifiedRivaRunner()
    runner.run()
