"""Unified vLLM-ASR runner: one process, one vLLM instance, both interfaces.

This runner creates a single vLLM LLM instance and passes it to both
the batch engine adapter (queue polling) and the realtime engine adapter
(WebSocket server). An AdmissionController gates both paths to prevent
realtime starvation under batch load.

Supports any vLLM-compatible audio model (Voxtral, Qwen2-Audio, etc.).

Environment variables (in addition to each adapter's own env vars):
    DALSTON_RT_RESERVATION: Min slots reserved for realtime (default: 2)
    DALSTON_BATCH_MAX_INFLIGHT: Max concurrent batch tasks (default: 4)
    DALSTON_TOTAL_CAPACITY: Total engine capacity (default: 6)

    DALSTON_DEFAULT_MODEL_ID: HF model ID to preload (default: mistralai/Voxtral-Mini-3B-2507)
    DALSTON_VLLM_GPU_MEMORY_UTILIZATION: GPU memory fraction (default: 0.9)
    DALSTON_VLLM_MAX_MODEL_LEN: Maximum context length (default: 4096)
"""

from __future__ import annotations

import asyncio
import gc
import os
import signal
import tempfile
import threading
from typing import Any

import structlog

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)
from dalston.engine_sdk.vram_budget import (
    VllmAdmissionParams,
    compute_vllm_admission_params,
    get_gpu_name,
    load_vllm_profile,
)

logger = structlog.get_logger()

DEFAULT_MODEL_ID = "mistralai/Voxtral-Mini-3B-2507"


def _create_vllm_instance() -> Any:
    """Create a shared vLLM LLM instance."""
    try:
        from vllm import LLM
    except ImportError as e:
        raise RuntimeError(
            "vLLM not installed. Install with: pip install 'vllm[audio]>=0.6.0'"
        ) from e

    model_id = os.environ.get("DALSTON_DEFAULT_MODEL_ID", DEFAULT_MODEL_ID)

    gpu_memory_utilization = float(
        os.environ.get("DALSTON_VLLM_GPU_MEMORY_UTILIZATION", "0.9")
    )
    max_model_len = int(os.environ.get("DALSTON_VLLM_MAX_MODEL_LEN", "4096"))

    logger.info(
        "loading_shared_vllm_model",
        model_id=model_id,
        gpu_memory_utilization=gpu_memory_utilization,
        max_model_len=max_model_len,
    )

    llm = LLM(
        model=model_id,
        gpu_memory_utilization=gpu_memory_utilization,
        max_model_len=max_model_len,
        limit_mm_per_prompt={"audio": 1},
        allowed_local_media_path=tempfile.gettempdir(),
    )

    logger.info("shared_vllm_model_loaded", model_id=model_id)
    return llm


class UnifiedVllmAsrRunner:
    """Runs batch + realtime vLLM-ASR adapters in a single process.

    Key properties:
    - ONE vLLM LLM instance (one model in GPU memory)
    - ONE AdmissionController (shared QoS policy)
    - Batch adapter runs in a background thread (sync queue polling)
    - RT adapter runs in the async event loop (WebSocket server)

    The runner owns the lifecycle of both adapters and coordinates
    graceful shutdown across both.
    """

    def __init__(self) -> None:
        # Check GPU availability via pynvml — NOT torch.cuda — to avoid
        # premature CUDA initialization before vLLM forks.
        try:
            import pynvml

            pynvml.nvmlInit()
            pynvml.nvmlDeviceGetCount()
            pynvml.nvmlShutdown()
        except Exception as exc:
            raise RuntimeError(
                "vLLM-ASR requires CUDA. GPU not available on this system."
            ) from exc

        # Create single shared vLLM instance
        self._llm = _create_vllm_instance()

        # Cache GPU name (never changes within a process lifetime)
        self._gpu_name = get_gpu_name()

        # Load calibration profile and compute admission limits
        model_id = os.environ.get("DALSTON_DEFAULT_MODEL_ID", DEFAULT_MODEL_ID)
        self._vllm_params = self._resolve_admission_params(model_id, self._gpu_name)

        # Create admission controller — calibrated profile provides defaults,
        # explicit env vars always take priority.
        self._admission = AdmissionController(
            AdmissionConfig.from_env(
                default_rt_reservation=self._vllm_params.rt_reservation,
                default_batch_max_inflight=self._vllm_params.batch_max_inflight,
                default_total_capacity=self._vllm_params.total_capacity,
            )
        )

        # Adapters (created lazily in run())
        self._batch_engine: Any = None
        self._rt_engine: Any = None
        self._batch_thread: threading.Thread | None = None
        self._running = False

        logger.info(
            "unified_vllm_asr_runner_init",
            admission=self._admission.get_status(),
            vllm_profile_source=self._vllm_params.profile_source,
            cuda_device_count=__import__("torch").cuda.device_count(),
        )

    @staticmethod
    def _resolve_admission_params(
        model_id: str, gpu_name: str | None
    ) -> VllmAdmissionParams:
        """Load vLLM calibration profile and compute admission params."""
        profile = load_vllm_profile(model_id, gpu_name=gpu_name)
        rt_reservation = int(os.environ.get("DALSTON_RT_RESERVATION") or 2)
        params = compute_vllm_admission_params(profile, rt_reservation)

        if profile is not None:
            logger.info(
                "vllm_admission_from_profile",
                model_id=model_id,
                gpu=gpu_name,
                max_safe_concurrency=profile.max_safe_concurrency,
                optimal_concurrency=profile.optimal_concurrency,
                total_capacity=params.total_capacity,
                batch_max_inflight=params.batch_max_inflight,
            )
        else:
            logger.info("vllm_admission_defaults", model_id=model_id)

        return params

    def _on_model_loaded(self, model_id: str) -> None:
        """Reconfigure admission limits after a model swap.

        Called by the batch engine's ``_ensure_model_loaded`` hook.
        Looks up the calibration profile for the new model and
        reconfigures the admission controller's limits.
        """
        params = self._resolve_admission_params(model_id, self._gpu_name)
        new_config = AdmissionConfig.from_env(
            default_rt_reservation=params.rt_reservation,
            default_batch_max_inflight=params.batch_max_inflight,
            default_total_capacity=params.total_capacity,
        )
        self._admission.reconfigure(new_config)
        self._vllm_params = params

    def run(self) -> None:
        """Start the unified runner."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async entry point that starts both adapters."""
        self._running = True

        from batch_engine import VllmAsrBatchEngine
        from rt_engine import VllmAsrRealtimeEngine

        # Create adapters sharing the same vLLM instance
        self._batch_engine = VllmAsrBatchEngine(
            llm=self._llm,
            on_model_loaded=self._on_model_loaded,
        )
        self._rt_engine = VllmAsrRealtimeEngine(llm=self._llm)

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
        logger.info("unified_vllm_asr_runner_shutting_down")

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

        # Release shared vLLM instance
        if self._llm is not None:
            del self._llm
            self._llm = None

            import torch

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

        logger.info(
            "unified_vllm_asr_runner_stopped",
            final_admission_status=self._admission.get_status(),
        )


class BatchRejectedError(TaskDeferredError):
    """Raised when admission controller rejects a batch task.

    Inherits from TaskDeferredError so the EngineRunner skips both the
    failure publish and the stream ACK, leaving the message in the PEL
    for redelivery.
    """


if __name__ == "__main__":
    # Add engine directory to sys.path so sibling modules (batch_engine,
    # rt_engine) can be imported by bare name.  This is only needed when
    # running as a script; the Dockerfile CMD invokes this file directly.
    # Hyphenated directory names prevent proper Python package imports.
    import sys
    from pathlib import Path

    _engine_dir = str(Path(__file__).resolve().parent)
    if _engine_dir not in sys.path:
        sys.path.insert(0, _engine_dir)

    # Force spawn before any CUDA initialization.  vLLM's EngineCore
    # uses subprocesses; the default "fork" method inherits a CUDA
    # context that cannot be re-initialized, causing a fatal error.
    import multiprocessing

    multiprocessing.set_start_method("spawn", force=True)

    runner = UnifiedVllmAsrRunner()
    runner.run()
