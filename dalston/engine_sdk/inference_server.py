"""Base class for gRPC inference servers (sidecar pattern).

Subclasses provide a Core instance (TranscribeCore or ParakeetCore);
this class handles:
- gRPC server lifecycle
- Admission control (semaphore-based concurrency limiting)
- Health/status endpoint
- Graceful shutdown

This replaces the unified runner pattern (M63) with independent
inference servers that batch and RT engines connect to via gRPC.
"""

from __future__ import annotations

import asyncio
import signal
from abc import ABC, abstractmethod
from concurrent import futures
from pathlib import Path
from typing import Any

import grpc
import numpy as np
import structlog

from dalston.proto import inference_pb2, inference_pb2_grpc

logger = structlog.get_logger()

# Max message size: 512MB to handle large audio files
_MAX_MESSAGE_LENGTH = 512 * 1024 * 1024


class InferenceServer(ABC, inference_pb2_grpc.InferenceServiceServicer):
    """Base class for gRPC inference servers.

    Subclasses provide a Core instance; this class handles:
    - gRPC server lifecycle
    - Admission control (semaphore-based concurrency limiting)
    - Health check endpoint
    - Graceful shutdown
    """

    def __init__(
        self,
        core: Any,
        port: int = 50052,
        max_concurrent: int = 4,
    ) -> None:
        self._core = core
        self._port = port
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_requests = 0
        self._server: grpc.aio.Server | None = None

        logger.info(
            "inference_server_init",
            runtime=self.get_runtime(),
            port=port,
            max_concurrent=max_concurrent,
        )

    @abstractmethod
    def get_runtime(self) -> str:
        """Return the runtime identifier (e.g. 'faster-whisper' or 'parakeet')."""

    def _decode_audio(
        self, audio_bytes: bytes, audio_format: int
    ) -> str | np.ndarray:
        """Decode audio bytes based on the specified format.

        Returns either a file path (for FILE format) or numpy array.
        """
        if audio_format == inference_pb2.FILE:
            # Write to temp file and return path
            import tempfile

            suffix = ".wav"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(audio_bytes)
            tmp.close()
            return tmp.name
        elif audio_format == inference_pb2.PCM_F32LE_16K:
            return np.frombuffer(audio_bytes, dtype=np.float32)
        elif audio_format == inference_pb2.PCM_S16LE_16K:
            pcm_s16 = np.frombuffer(audio_bytes, dtype=np.int16)
            return pcm_s16.astype(np.float32) / 32768.0
        else:
            raise ValueError(f"Unknown audio format: {audio_format}")

    @abstractmethod
    def _do_transcribe(
        self, audio: str | np.ndarray, model_id: str, config: Any
    ) -> inference_pb2.TranscribeResponse:
        """Perform transcription using the concrete core. Implemented by subclass."""

    def _proto_config_to_dict(
        self, config: inference_pb2.TranscribeConfig
    ) -> dict[str, Any]:
        """Convert proto TranscribeConfig to a plain dict."""
        result: dict[str, Any] = {}
        if config.HasField("language"):
            result["language"] = config.language
        result["beam_size"] = config.beam_size if config.beam_size > 0 else 5
        result["vad_filter"] = config.vad_filter
        result["word_timestamps"] = config.word_timestamps
        result["temperature"] = config.temperature if config.temperature > 0 else 0.0
        result["task"] = config.task if config.task else "transcribe"
        if config.HasField("initial_prompt"):
            result["initial_prompt"] = config.initial_prompt
        if config.HasField("hotwords"):
            result["hotwords"] = config.hotwords
        return result

    async def Transcribe(
        self,
        request: inference_pb2.TranscribeRequest,
        context: grpc.aio.ServicerContext,
    ) -> inference_pb2.TranscribeResponse:
        """Handle a Transcribe RPC."""
        acquired = self._semaphore.locked() and self._semaphore._value == 0
        if self._semaphore._value == 0:
            logger.warning(
                "inference_at_capacity",
                active=self._active_requests,
                max_concurrent=self._max_concurrent,
            )
            await context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"Server at capacity ({self._max_concurrent} concurrent requests)",
            )

        async with self._semaphore:
            self._active_requests += 1
            try:
                audio = self._decode_audio(request.audio, request.format)
                config_dict = self._proto_config_to_dict(request.config)

                logger.debug(
                    "transcribe_request",
                    model_id=request.model_id,
                    audio_format=request.format,
                    audio_bytes=len(request.audio),
                )

                response = await asyncio.to_thread(
                    self._do_transcribe, audio, request.model_id, config_dict
                )

                # Clean up temp file if we created one
                if isinstance(audio, str):
                    try:
                        Path(audio).unlink(missing_ok=True)
                    except OSError:
                        pass

                return response
            except Exception as e:
                logger.error("transcribe_error", error=str(e), exc_info=True)
                await context.abort(
                    grpc.StatusCode.INTERNAL, f"Transcription failed: {e}"
                )
            finally:
                self._active_requests -= 1

    @abstractmethod
    def _get_loaded_models(self) -> list[str]:
        """Return list of currently loaded model IDs."""

    async def GetStatus(
        self,
        request: inference_pb2.StatusRequest,
        context: grpc.aio.ServicerContext,
    ) -> inference_pb2.StatusResponse:
        """Handle a GetStatus RPC."""
        return inference_pb2.StatusResponse(
            runtime=self.get_runtime(),
            device=getattr(self._core, "device", "unknown"),
            loaded_models=self._get_loaded_models(),
            total_capacity=self._max_concurrent,
            available_capacity=self._semaphore._value,
            healthy=True,
        )

    async def serve(self) -> None:
        """Start the gRPC server and block until shutdown."""
        self._server = grpc.aio.server(
            futures.ThreadPoolExecutor(max_workers=self._max_concurrent + 2),
            options=[
                ("grpc.max_send_message_length", _MAX_MESSAGE_LENGTH),
                ("grpc.max_receive_message_length", _MAX_MESSAGE_LENGTH),
            ],
        )
        inference_pb2_grpc.add_InferenceServiceServicer_to_server(
            self, self._server
        )
        self._server.add_insecure_port(f"[::]:{self._port}")

        await self._server.start()
        logger.info(
            "inference_server_started",
            runtime=self.get_runtime(),
            port=self._port,
        )

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        await self._server.wait_for_termination()

    async def _shutdown(self) -> None:
        """Graceful shutdown: stop accepting, drain in-flight, close core."""
        if self._server is None:
            return
        logger.info("inference_server_shutting_down")

        # Grace period for in-flight requests
        await self._server.stop(grace=10)

        # Shutdown the core (unloads models)
        if hasattr(self._core, "shutdown"):
            self._core.shutdown()

        logger.info("inference_server_stopped")

    def run(self) -> None:
        """Synchronous entry point — runs the async server."""
        asyncio.run(self.serve())
