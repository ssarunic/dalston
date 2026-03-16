"""Unified HF-ASR runner: one process, one model manager, both interfaces.

This runner creates a single HFTransformersModelManager and passes it to
both the batch engine adapter (queue polling) and the realtime engine
adapter (WebSocket server). An AdmissionController gates both paths to
prevent realtime starvation under batch load.

Supports any model on HuggingFace Hub with
pipeline_tag=automatic-speech-recognition.

Usage:
    python -m engines.stt-unified.hf-asr.runner

Environment variables (in addition to each adapter's own env vars):
    DALSTON_RT_RESERVATION: Min slots reserved for realtime (default: 2)
    DALSTON_BATCH_MAX_INFLIGHT: Max concurrent batch tasks (default: 4)
    DALSTON_TOTAL_CAPACITY: Total engine capacity (default: 6)

    DALSTON_DEFAULT_MODEL_ID: Default HF model ID (default: openai/whisper-large-v3)
    DALSTON_DEVICE: Device for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from typing import Any

import structlog
import torch

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)
from dalston.engine_sdk.managers import HFTransformersModelManager

logger = structlog.get_logger()

DEFAULT_MODEL_ID = "openai/whisper-large-v3"


def _detect_device() -> tuple[str, torch.dtype]:
    """Detect the best available device and dtype."""
    requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

    if requested_device == "cpu":
        return "cpu", torch.float32

    if torch.cuda.is_available():
        return "cuda", torch.float16

    if requested_device == "cuda":
        raise RuntimeError("DALSTON_DEVICE=cuda but CUDA is not available.")

    if requested_device not in ("", "auto"):
        raise ValueError(
            f"Unknown DALSTON_DEVICE value: {requested_device}. Use cuda or cpu."
        )

    return "cpu", torch.float32


def _create_shared_manager() -> HFTransformersModelManager:
    """Create a shared HFTransformersModelManager for both adapters."""
    device, torch_dtype = _detect_device()

    model_storage = None
    s3_bucket = os.environ.get("DALSTON_S3_BUCKET")
    if s3_bucket:
        from dalston.engine_sdk.model_storage import S3ModelStorage

        model_storage = S3ModelStorage.from_env()
        logger.info("s3_model_storage_enabled", bucket=s3_bucket)

    manager = HFTransformersModelManager(
        device=device,
        torch_dtype=torch_dtype,
        model_storage=model_storage,
        ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
        max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
        preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
    )

    logger.info(
        "shared_manager_created",
        device=device,
        torch_dtype=str(torch_dtype),
        ttl_seconds=manager.ttl_seconds,
        max_loaded=manager.max_loaded,
    )
    return manager


class UnifiedHfAsrRunner:
    """Runs batch + realtime HF-ASR adapters in a single process.

    Key properties:
    - ONE HFTransformersModelManager (shared model cache)
    - ONE AdmissionController (shared QoS policy)
    - Batch adapter runs in a background thread (sync queue polling)
    - RT adapter runs in the async event loop (WebSocket server)

    The runner owns the lifecycle of both adapters and coordinates
    graceful shutdown across both.
    """

    def __init__(self) -> None:
        # Create single shared model manager
        self._manager = _create_shared_manager()

        # Create admission controller
        self._admission = AdmissionController(AdmissionConfig.from_env())

        # Adapters (created lazily in run())
        self._batch_engine: Any = None
        self._rt_engine: Any = None
        self._batch_thread: threading.Thread | None = None
        self._running = False

        logger.info(
            "unified_hf_asr_runner_init",
            device=self._manager.device,
            admission=self._admission.get_status(),
        )

    def run(self) -> None:
        """Start the unified runner."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async entry point that starts both adapters."""
        self._running = True

        # Import adapters (sibling modules, on sys.path via _ensure_import_path)
        from batch_engine import HfAsrBatchEngine
        from rt_engine import HfAsrRealtimeEngine

        # Create adapters sharing the same manager
        self._batch_engine = HfAsrBatchEngine(manager=self._manager)
        self._rt_engine = HfAsrRealtimeEngine(manager=self._manager)

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
        logger.info("unified_hf_asr_runner_shutting_down")

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

        # Shutdown shared manager (unloads all models)
        self._manager.shutdown()

        logger.info(
            "unified_hf_asr_runner_stopped",
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
    runner = UnifiedHfAsrRunner()
    runner.run()
