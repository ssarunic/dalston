"""Abstract base class for real-time transcription engines.

Provides the foundation for building real-time transcription workers
that integrate with the Dalston real-time infrastructure.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import structlog
import yaml
from aiohttp import web
from websockets.asyncio.server import ServerConnection, serve

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.audio_defaults import (
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_MIN_SPEECH_MS,
    DEFAULT_RESAMPLE_QUALITY,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VAD_THRESHOLD,
    MAX_SAMPLE_RATE,
    MIN_SAMPLE_RATE,
    RESAMPLE_QUALITY_PROFILES,
)
from dalston.common.registry import EngineRecord, UnifiedEngineRegistry
from dalston.common.timeouts import WS_PING_INTERVAL, WS_PING_TIMEOUT
from dalston.common.ws_close_codes import (
    WS_CLOSE_POLICY_VIOLATION,
    WS_CLOSE_PROTOCOL_ERROR,
    WS_CLOSE_TRY_AGAIN_LATER,
)
from dalston.engine_sdk.types import EngineCapabilities
from dalston.realtime_sdk.assembler import TranscribeResult
from dalston.realtime_sdk.model_manager import AsyncModelManager
from dalston.realtime_sdk.session import SessionConfig, SessionHandler

logger = structlog.get_logger()

# Type alias for model manager (any model type)
ModelManagerType = AsyncModelManager[Any]

# Paths for engine.yaml (container path first, local fallback second)
ENGINE_YAML_PATHS = [
    Path("/etc/dalston/engine.yaml"),
    Path("engine.yaml"),
]


class RealtimeEngine(ABC):
    """Abstract base class for real-time transcription engines.

    Engine implementations subclass this and implement:
    - load_models(): Load ASR models into memory
    - transcribe(): Transcribe an audio segment

    The SDK handles:
    - WebSocket server lifecycle
    - Session management
    - Worker registration and heartbeat
    - Signal handling for graceful shutdown

    Example:
        class MyRealtimeEngine(RealtimeEngine):
            def load_models(self) -> None:
                from faster_whisper import WhisperModel
                self.model = WhisperModel("large-v3", device="cuda")

            def transcribe(
                self,
                audio: np.ndarray,
                language: str,
                model_variant: str,
            ) -> TranscribeResult:
                segments, info = self.model.transcribe(
                    audio,
                    language=None if language == "auto" else language,
                    word_timestamps=True,
                )

                words = []
                text_parts = []
                for segment in segments:
                    text_parts.append(segment.text.strip())
                    for word in segment.words or []:
                        words.append(Word(
                            word=word.word,
                            start=word.start,
                            end=word.end,
                            confidence=word.probability,
                        ))

                return TranscribeResult(
                    text=" ".join(text_parts),
                    words=words,
                    language=info.language,
                    confidence=info.language_probability,
                )

        if __name__ == "__main__":
            import asyncio
            engine = MyRealtimeEngine()
            asyncio.run(engine.run())

    Environment variables:
        DALSTON_INSTANCE: Unique identifier for this instance (required)
        DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
        DALSTON_WORKER_ENDPOINT: WebSocket endpoint URL for registration (auto-detected)
        DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 2)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    """

    def __init__(self) -> None:
        """Initialize the engine."""
        self.instance = os.environ.get("DALSTON_INSTANCE", "realtime-worker")
        self.port = int(os.environ.get("DALSTON_WORKER_PORT", "9000"))
        self.max_sessions = int(os.environ.get("DALSTON_MAX_SESSIONS", "2"))
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.metrics_port = int(os.environ.get("DALSTON_METRICS_PORT", "9100"))

        # Worker endpoint for registration - use env var or detect hostname
        self._worker_endpoint = os.environ.get("DALSTON_WORKER_ENDPOINT")
        if not self._worker_endpoint:
            # Auto-detect: use hostname in Docker, localhost otherwise
            import socket

            hostname = socket.gethostname()
            self._worker_endpoint = f"ws://{hostname}:{self.port}"

        self._sessions: dict[str, SessionHandler] = {}
        self._unified_registry: UnifiedEngineRegistry | None = None
        self._running = False
        self._server = None
        self._metrics_runner: web.AppRunner | None = None

        # M43: Dynamic model loading support
        self._model_manager: ModelManagerType | None = None

    @abstractmethod
    def load_models(self) -> None:
        """Load ASR models into memory.

        Called once on startup before accepting connections.
        Implement to load your models (e.g., Whisper, VAD).

        Example:
            def load_models(self) -> None:
                from faster_whisper import WhisperModel
                self.model = WhisperModel("large-v3", device="cuda")
        """
        raise NotImplementedError

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment.

        Called by SessionHandler when VAD detects an utterance endpoint.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (e.g., "en") or "auto" for detection
            model_variant: Model name (e.g., "faster-whisper-large-v3")
            vocabulary: List of terms to boost recognition (hotwords/bias)

        Returns:
            TranscribeResult with text, words, language, confidence

        Example:
            def transcribe(self, audio, language, model_variant, vocabulary=None):
                # Apply vocabulary boosting if supported
                kwargs = {}
                if vocabulary:
                    kwargs["hotwords"] = " ".join(vocabulary)

                segments, info = self.model.transcribe(
                    audio,
                    language=None if language == "auto" else language,
                    word_timestamps=True,
                    **kwargs,
                )
                # Process segments...
                return TranscribeResult(text=text, words=words, ...)
        """
        raise NotImplementedError

    def supports_streaming(self) -> bool:
        """Whether this engine supports native streaming with partial results.

        Override to return True for engines like Parakeet that support
        incremental transcription. When True, SessionHandler will send
        partial results during speech.

        Returns:
            True if engine supports streaming partials. Default: False
        """
        return False

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers.

        Override to report available models (e.g., ["faster-whisper-large-v3"]).
        These are the exact model names clients use when requesting this engine.
        Used when registering with Session Router.

        Returns:
            List of model names. Default: [] (must be overridden)
        """
        return []

    def get_languages(self) -> list[str]:
        """Return list of supported languages.

        Override to report supported languages.
        Used when registering with Session Router.

        Returns:
            List of language codes. Default: ["auto"]
        """
        return ["auto"]

    def get_runtime(self) -> str:
        """Return the inference framework identifier.

        Override to report the runtime (e.g., "faster-whisper", "parakeet").
        Used when registering with Session Router.

        Returns:
            Runtime string. Default: "unknown"
        """
        return "unknown"

    def get_supports_vocabulary(self) -> bool:
        """Return whether this engine supports vocabulary boosting.

        Override to return True for engines that support vocabulary/hotwords
        (e.g., faster-whisper via hotwords parameter).
        Used when registering with Session Router.

        Returns:
            True if vocabulary boosting is supported. Default: False
        """
        return False

    def get_loaded_models(self) -> list[str] | None:
        """Return list of currently loaded model IDs.

        For engines with dynamic model loading (M43), this returns the list
        of models currently loaded in memory. Used in heartbeat to enable
        warm routing (Session Router prefers workers with model already loaded).

        Default implementation returns None (static models defined in get_models()).
        Override or set _model_manager to enable dynamic reporting.

        Returns:
            List of loaded model IDs, or None if using static model list
        """
        if self._model_manager is not None:
            return self._model_manager.loaded_models()
        return None

    def get_streaming_decode_fn(self, model_variant: str | None = None) -> Any:
        """Return a streaming decode callback for the given model, or None.

        Override in engines that support cache-aware streaming decode
        (M71). When a non-None callback is returned, the SessionHandler
        will feed audio chunks directly to the engine's streaming
        decoder, bypassing VAD-gated accumulation.

        The callback signature is:
            (audio_iter: Iterator[np.ndarray], language: str, model: str)
            -> Iterator[TranscribeResult]

        Args:
            model_variant: Model name from the session config.

        Returns:
            Streaming decode callback, or None for VAD-accumulate path.
        """
        return None

    def get_gpu_memory_usage(self) -> str:
        """Return GPU memory usage string.

        Override to report actual GPU usage for monitoring.

        Returns:
            GPU memory usage string (e.g., "4.2GB"). Default: "0GB"
        """
        try:
            import torch

            if torch.cuda.is_available():
                used = torch.cuda.memory_allocated() / 1e9
                return f"{used:.1f}GB"
        except ImportError:
            pass
        return "0GB"

    def get_capabilities(self) -> EngineCapabilities:
        """Return engine capabilities for registration and routing.

        Loads capabilities from engine.yaml if available, otherwise falls back
        to values from getter methods. The engine.yaml is expected at
        /etc/dalston/engine.yaml in containers, or ./engine.yaml for local dev.

        Returns:
            EngineCapabilities describing what this engine can do
        """
        card = self._load_engine_yaml()
        if card is None:
            # Fallback for engines without engine.yaml
            return EngineCapabilities(
                runtime=self.get_runtime(),
                version="unknown",
                stages=["transcribe"],
                languages=self.get_languages() or None,
                supports_streaming=self.supports_streaming(),
                max_concurrency=self.max_sessions,
            )

        # Extract capabilities from engine.yaml
        caps = card.get("capabilities", {})
        hardware = card.get("hardware", {})
        performance = card.get("performance", {})

        # Determine GPU requirement from profile-specific metadata.
        # container.gpu is authoritative when present; otherwise infer from
        # hardware metadata for non-container profiles.
        container = card.get("container", {})
        gpu_field = container.get("gpu")
        if gpu_field is None:
            min_vram_gb = hardware.get("min_vram_gb")
            supports_cpu = hardware.get("supports_cpu", True)
            gpu_required = bool(min_vram_gb and not supports_cpu)
        else:
            gpu_required = gpu_field == "required"

        # Languages: convert ["all"] to None (meaning all languages)
        languages = caps.get("languages")
        if languages == ["all"]:
            languages = None

        # Stages: derive from stage field
        stage = card.get("stage")
        stages = [stage] if stage else ["transcribe"]

        return EngineCapabilities(
            runtime=card.get("runtime") or card.get("id", self.get_runtime()),
            version=card.get("version", "unknown"),
            stages=stages,
            languages=languages,
            supports_word_timestamps=caps.get("word_timestamps", False),
            supports_streaming=caps.get("streaming", self.supports_streaming()),
            model_variants=None,
            gpu_required=gpu_required,
            gpu_vram_mb=(
                hardware.get("min_vram_gb", 0) * 1024
                if hardware.get("min_vram_gb")
                else None
            ),
            supports_cpu=hardware.get("supports_cpu", True),
            min_ram_gb=hardware.get("min_ram_gb"),
            rtf_gpu=performance.get("rtf_gpu"),
            rtf_cpu=performance.get("rtf_cpu"),
            max_concurrency=caps.get("max_concurrency", self.max_sessions),
        )

    def _load_engine_yaml(self) -> dict[str, Any] | None:
        """Load engine.yaml from known paths.

        Returns:
            Parsed engine.yaml dict, or None if not found
        """
        for path in ENGINE_YAML_PATHS:
            if path.exists():
                try:
                    with open(path) as f:
                        return yaml.safe_load(f)
                except Exception as e:
                    logger.warning(
                        "failed_to_load_engine_yaml",
                        path=str(path),
                        error=str(e),
                    )
        return None

    def health_check(self) -> dict[str, Any]:
        """Return health status for monitoring.

        Override to provide engine-specific health information.

        Returns:
            Dictionary with at least a "status" key
        """
        return {
            "status": "healthy",
            "instance": self.instance,
            "active_sessions": len(self._sessions),
            "capacity": self.max_sessions,
            "gpu_memory": self.get_gpu_memory_usage(),
        }

    async def run(self) -> None:
        """Start the engine.

        This method:
        1. Loads models via load_models()
        2. Registers with Session Router
        3. Starts heartbeat loop
        4. Starts WebSocket server
        5. Runs until shutdown signal

        Call this from your engine's main:
            if __name__ == "__main__":
                engine = MyEngine()
                asyncio.run(engine.run())
        """
        # Configure unified structured logging for this worker
        dalston.logging.configure(f"realtime-{self.instance}")

        # Configure distributed tracing (M19)
        dalston.telemetry.configure_tracing(f"dalston-realtime-{self.instance}")

        # Configure Prometheus metrics (M20)
        dalston.metrics.configure_metrics(f"realtime-{self.instance}")

        # Bind instance to logging context for all subsequent log calls
        structlog.contextvars.bind_contextvars(instance=self.instance)

        logger.info("starting_realtime_engine")

        # Load models
        logger.info("loading_models")
        self.load_models()
        logger.info("models_loaded")

        # M50: Get structured capabilities from engine.yaml
        capabilities = self.get_capabilities()

        # Register with unified engine registry
        import redis.asyncio as aioredis

        unified_redis = aioredis.from_url(
            self.redis_url, encoding="utf-8", decode_responses=True
        )
        self._unified_registry = UnifiedEngineRegistry(unified_redis)
        await self._unified_registry.register(
            EngineRecord(
                instance=self.instance,
                runtime=capabilities.runtime,
                stage="transcribe",
                status="ready",
                interfaces=["realtime"],
                capacity=self.max_sessions,
                endpoint=self._worker_endpoint,
                models_loaded=self.get_models(),
                languages=self.get_languages(),
                capabilities=capabilities,
                supports_word_timestamps=capabilities.supports_word_timestamps,
                includes_diarization=capabilities.includes_diarization,
            )
        )
        logger.info("engine_registered", instance=self.instance)

        self._running = True

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start metrics HTTP server (M20)
        await self._start_metrics_server()

        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Start WebSocket server
        logger.info("starting_websocket_server", port=self.port)

        def capture_request_path(connection, request):
            """Capture request path for use in handler (websockets v16+ API)."""
            connection.request_path = request.path
            return None  # Continue with default handling

        async with serve(
            self._handle_connection,
            "0.0.0.0",
            self.port,
            ping_interval=WS_PING_INTERVAL,
            ping_timeout=WS_PING_TIMEOUT,
            process_request=capture_request_path,
        ) as server:
            self._server = server
            logger.info("realtime_engine_ready", instance=self.instance)

            # Wait until shutdown
            while self._running:
                await asyncio.sleep(1)

        # Cleanup
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        dalston.telemetry.shutdown_tracing()
        logger.info("engine_stopped")

    async def shutdown(self) -> None:
        """Graceful shutdown.

        Stops accepting new sessions, waits for active sessions to complete,
        unregisters from Session Router.
        """
        if not self._running:
            return

        logger.info("shutting_down")
        self._running = False

        if self._unified_registry:
            try:
                await self._unified_registry.deregister(self.instance)
                await self._unified_registry._redis.close()
            except Exception:
                pass  # Best effort cleanup
            self._unified_registry = None

        # M43: Shutdown model manager and unload models
        if self._model_manager is not None:
            await self._model_manager.shutdown()

        # Stop metrics server
        await self._stop_metrics_server()

        # Close server (stops accepting new connections)
        if self._server:
            self._server.close()

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to Session Router."""
        while self._running:
            try:
                active = len(self._sessions)
                status = "ready" if active < self.max_sessions else "busy"
                loaded_models = self.get_loaded_models()
                gpu_mem = self.get_gpu_memory_usage()

                if self._unified_registry:
                    try:
                        await self._unified_registry.heartbeat(
                            self.instance,
                            status=status,
                            active_realtime=active,
                            models_loaded=loaded_models,
                            gpu_memory_used=gpu_mem,
                        )
                    except Exception as e:
                        logger.warning("unified_heartbeat_failed", error=str(e))
            except Exception as e:
                logger.error("heartbeat_error", error=str(e))

            await asyncio.sleep(10)

    async def _start_metrics_server(self) -> None:
        """Start metrics HTTP server for Prometheus scraping (M20)."""
        if not dalston.metrics.is_metrics_enabled():
            logger.debug("metrics_disabled_skipping_server")
            return

        try:
            app = web.Application()
            app.router.add_get("/metrics", self._handle_metrics)
            app.router.add_get("/health", self._handle_health_http)

            self._metrics_runner = web.AppRunner(app)
            await self._metrics_runner.setup()
            site = web.TCPSite(self._metrics_runner, "0.0.0.0", self.metrics_port)
            await site.start()
            logger.info("metrics_server_started", port=self.metrics_port)
        except Exception as e:
            logger.warning("metrics_server_failed", error=str(e))

    async def _stop_metrics_server(self) -> None:
        """Stop the metrics HTTP server."""
        if self._metrics_runner:
            await self._metrics_runner.cleanup()
            self._metrics_runner = None
            logger.debug("metrics_server_stopped")

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Handle /metrics endpoint for Prometheus scraping."""
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        # aiohttp requires charset to be separate from content_type
        # So we set the full Content-Type header directly
        return web.Response(
            body=generate_latest(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    async def _handle_health_http(self, request: web.Request) -> web.Response:
        """Handle /health endpoint via HTTP."""
        return web.json_response(self.health_check())

    async def _handle_connection(
        self,
        websocket: ServerConnection,
    ) -> None:
        """Handle new WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        # Get path stored by process_request callback (websockets v16+ API)
        path = getattr(websocket, "request_path", "/")

        # Handle health check endpoint
        if path == "/health" or path.startswith("/health?"):
            await websocket.send(json.dumps(self.health_check()))
            return

        # Only accept /session path
        if not path.startswith("/session"):
            await websocket.close(WS_CLOSE_POLICY_VIOLATION, "Invalid path")
            return

        # Check capacity
        if len(self._sessions) >= self.max_sessions:
            await websocket.close(WS_CLOSE_TRY_AGAIN_LATER, "Server at capacity")
            return

        # Parse query parameters
        try:
            config = self._parse_connection_params(path)
        except ValueError as e:
            await websocket.close(WS_CLOSE_PROTOCOL_ERROR, f"Invalid parameter: {e}")
            return

        # M71: Check if this model supports streaming decode
        streaming_decode_fn = self.get_streaming_decode_fn(config.model)

        # Create session handler
        handler = SessionHandler(
            websocket=websocket,
            config=config,
            transcribe_fn=self.transcribe,
            on_session_end=self._on_session_end,
            supports_streaming=self.supports_streaming(),
            streaming_decode_fn=streaming_decode_fn,
        )

        # Track session
        self._sessions[config.session_id] = handler

        # Bind session_id to logging context for this session
        structlog.contextvars.bind_contextvars(session_id=config.session_id)

        # Resolve runtime from capabilities
        capabilities = self.get_capabilities()
        session_runtime = capabilities.runtime or ""
        session_model = config.model or ""

        # Create span for session lifetime (M19)
        with dalston.telemetry.create_span(
            "realtime.session",
            attributes={
                "dalston.session_id": config.session_id,
                "dalston.runtime": session_runtime,
                "dalston.model": session_model,
                "dalston.instance": self.instance,
                "dalston.language": config.language,
            },
        ):
            try:
                # Run session
                await handler.run()
            finally:
                # Remove from tracking
                del self._sessions[config.session_id]
                # Unbind session_id from context
                structlog.contextvars.unbind_contextvars("session_id")

    async def _on_session_end(
        self,
        session_id: str,
        duration: float,
        status: str,
    ) -> None:
        """Callback when session ends.

        Args:
            session_id: Session identifier
            duration: Session duration in seconds
            status: End status ("completed" or "error")
        """
        # Resolve runtime and model for metrics
        capabilities = self.get_capabilities()
        rt = capabilities.runtime or ""
        # Get model from session config if still tracked
        session = self._sessions.get(session_id)
        model = session.config.model if session and hasattr(session, "config") else ""

        # Record session metrics (M20)
        dalston.metrics.observe_realtime_session_duration(rt, model or "", duration)
        dalston.metrics.inc_session_router_sessions(status)

    def _parse_connection_params(self, path: str) -> SessionConfig:
        """Parse query parameters from connection path.

        Args:
            path: Request path with query string

        Returns:
            SessionConfig with parsed parameters
        """
        import uuid

        # Parse query string
        parsed = urlparse(path)
        params = parse_qs(parsed.query)

        def get_param(name: str, default: str) -> str:
            values = params.get(name, [default])
            return values[0] if values else default

        def get_bool_param(name: str, default: bool) -> bool:
            value = get_param(name, str(default).lower())
            return value.lower() in ("true", "1", "yes")

        def get_int_param(
            name: str,
            default: int,
            min_val: int | None = None,
            max_val: int | None = None,
        ) -> int:
            try:
                value = int(get_param(name, str(default)))
            except ValueError:
                return default
            if min_val is not None and value < min_val:
                raise ValueError(f"{name} must be >= {min_val}, got {value}")
            if max_val is not None and value > max_val:
                raise ValueError(f"{name} must be <= {max_val}, got {value}")
            return value

        def get_float_param(
            name: str,
            default: float,
            min_val: float | None = None,
            max_val: float | None = None,
        ) -> float:
            try:
                value = float(get_param(name, str(default)))
            except ValueError:
                return default
            if min_val is not None and value < min_val:
                raise ValueError(f"{name} must be >= {min_val}, got {value}")
            if max_val is not None and value > max_val:
                raise ValueError(f"{name} must be <= {max_val}, got {value}")
            return value

        # Use session_id from Gateway if provided, otherwise generate one
        # (Gateway passes session_id for coordination with Session Router)
        session_id = get_param("session_id", "")
        if not session_id:
            session_id = f"sess_{uuid.uuid4().hex[:16]}"

        # Model parameter: empty string or missing means None (any worker)
        model_param = get_param("model", "")
        model_value = model_param if model_param else None

        # Vocabulary parameter: JSON array of terms to boost, or None
        vocabulary_param = get_param("vocabulary", "")
        vocabulary_value: list[str] | None = None
        if vocabulary_param:
            try:
                parsed = json.loads(vocabulary_param)
                if isinstance(parsed, list) and all(isinstance(t, str) for t in parsed):
                    vocabulary_value = parsed if parsed else None
            except json.JSONDecodeError:
                # Invalid JSON - ignore and use None
                pass

        lag_warning_seconds = get_float_param(
            "lag_warning_seconds",
            float(os.environ.get("DALSTON_REALTIME_LAG_WARNING_SECONDS", "3.0")),
            min_val=0.001,
        )
        lag_hard_seconds = get_float_param(
            "lag_hard_seconds",
            float(os.environ.get("DALSTON_REALTIME_LAG_HARD_SECONDS", "5.0")),
            min_val=0.001,
        )
        lag_hard_grace_seconds = get_float_param(
            "lag_hard_grace_seconds",
            float(os.environ.get("DALSTON_REALTIME_LAG_HARD_GRACE_SECONDS", "2.0")),
            min_val=0.001,
        )
        debug_chunk_sleep_initial_seconds = get_float_param(
            "debug_chunk_sleep_initial_seconds",
            float(
                os.environ.get(
                    "DALSTON_REALTIME_DEBUG_CHUNK_SLEEP_INITIAL_SECONDS",
                    "0.0",
                )
            ),
            min_val=0.0,
        )
        debug_chunk_sleep_increment_seconds = get_float_param(
            "debug_chunk_sleep_increment_seconds",
            float(
                os.environ.get(
                    "DALSTON_REALTIME_DEBUG_CHUNK_SLEEP_INCREMENT_SECONDS",
                    "0.0",
                )
            ),
            min_val=0.0,
        )

        if lag_hard_seconds <= lag_warning_seconds:
            raise ValueError(
                "lag_hard_seconds must be greater than lag_warning_seconds "
                f"(got warning={lag_warning_seconds}, hard={lag_hard_seconds})"
            )

        sample_rate = get_int_param(
            "sample_rate",
            DEFAULT_SAMPLE_RATE,
            min_val=MIN_SAMPLE_RATE,
            max_val=MAX_SAMPLE_RATE,
        )
        client_sample_rate = get_int_param(
            "client_sample_rate",
            sample_rate,
            min_val=MIN_SAMPLE_RATE,
            max_val=MAX_SAMPLE_RATE,
        )

        resample_quality = get_param(
            "resample_quality",
            os.environ.get("DALSTON_REALTIME_RESAMPLE_QUALITY", DEFAULT_RESAMPLE_QUALITY),
        )
        if resample_quality not in RESAMPLE_QUALITY_PROFILES:
            raise ValueError(
                f"resample_quality must be one of {list(RESAMPLE_QUALITY_PROFILES)}, "
                f"got '{resample_quality}'"
            )

        return SessionConfig(
            session_id=session_id,
            language=get_param("language", "auto"),
            model=model_value,
            encoding=get_param("encoding", "pcm_s16le"),
            client_sample_rate=client_sample_rate,
            sample_rate=sample_rate,
            resample_quality=resample_quality,
            channels=get_int_param("channels", 1, min_val=1, max_val=2),
            enable_vad=get_bool_param("enable_vad", True),
            interim_results=get_bool_param("interim_results", True),
            word_timestamps=get_bool_param("word_timestamps", False),
            vocabulary=vocabulary_value,
            max_utterance_duration=get_float_param(
                "max_utterance_duration",
                float(
                    os.environ.get(
                        "DALSTON_REALTIME_MAX_UTTERANCE_DURATION",
                        str(DEFAULT_MAX_UTTERANCE_SECONDS),
                    )
                ),
                min_val=0.0,
                max_val=300.0,
            ),
            # VAD tuning parameters (ElevenLabs-compatible)
            vad_threshold=get_float_param(
                "vad_threshold", DEFAULT_VAD_THRESHOLD, min_val=0.0, max_val=1.0
            ),
            min_speech_duration_ms=get_int_param(
                "min_speech_duration_ms",
                DEFAULT_MIN_SPEECH_MS,
                min_val=50,
                max_val=2000,
            ),
            min_silence_duration_ms=get_int_param(
                "min_silence_duration_ms",
                int(
                    os.environ.get(
                        "DALSTON_REALTIME_MIN_SILENCE_DURATION_MS",
                        str(DEFAULT_MIN_SILENCE_MS),
                    )
                ),
                min_val=50,
                max_val=2000,
            ),
            prefix_padding_ms=get_int_param(
                "prefix_padding_ms",
                300,
                min_val=0,
                max_val=5000,
            ),
            # Storage options (S3 config read from Settings)
            store_audio=get_bool_param("store_audio", True),
            store_transcript=get_bool_param("store_transcript", True),
            lag_warning_seconds=lag_warning_seconds,
            lag_hard_seconds=lag_hard_seconds,
            lag_hard_grace_seconds=lag_hard_grace_seconds,
            debug_chunk_sleep_initial_seconds=debug_chunk_sleep_initial_seconds,
            debug_chunk_sleep_increment_seconds=debug_chunk_sleep_increment_seconds,
        )
