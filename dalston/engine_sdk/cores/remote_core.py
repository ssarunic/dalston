"""gRPC client that implements the same interface as TranscribeCore/ParakeetCore.

Drop-in replacement: batch and RT engines call .transcribe() the same
way — the only difference is inference happens over the network instead
of in-process.

This enables the sidecar pattern where the inference server owns the GPU
and this client runs in a lightweight CPU-only container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import grpc
import numpy as np
import structlog

from dalston.proto import inference_pb2, inference_pb2_grpc

logger = structlog.get_logger()

# Max message size: 512MB to handle large audio files
_MAX_MESSAGE_LENGTH = 512 * 1024 * 1024


# ---------------------------------------------------------------------------
# Unified result types that work for both faster-whisper and parakeet
# ---------------------------------------------------------------------------


@dataclass
class RemoteWordResult:
    """A word from the remote inference server."""

    word: str
    start: float
    end: float
    probability: float = 0.0
    confidence: float | None = None


@dataclass
class RemoteSegmentResult:
    """A segment from the remote inference server."""

    start: float
    end: float
    text: str
    words: list[RemoteWordResult] = field(default_factory=list)
    tokens: list[int] | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None


@dataclass
class RemoteTranscriptionResult:
    """Complete transcription result from the remote inference server."""

    text: str = ""
    segments: list[RemoteSegmentResult] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 0.0
    duration: float = 0.0


# ---------------------------------------------------------------------------
# Transcription config (mirrors faster_whisper_core.TranscribeConfig)
# ---------------------------------------------------------------------------


@dataclass
class RemoteTranscribeConfig:
    """Parameters for a remote transcription call."""

    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = True
    word_timestamps: bool = True
    temperature: float = 0.0
    task: str = "transcribe"
    initial_prompt: str | None = None
    hotwords: str | None = None


class RemoteTranscribeCore:
    """gRPC client that implements the same interface as TranscribeCore.

    Drop-in replacement: batch and RT engines call .transcribe() the same
    way — the only difference is inference happens over the network instead
    of in-process.
    """

    def __init__(self, uri: str = "localhost:50052") -> None:
        self._uri = uri
        self._channel = grpc.insecure_channel(
            uri,
            options=[
                ("grpc.max_send_message_length", _MAX_MESSAGE_LENGTH),
                ("grpc.max_receive_message_length", _MAX_MESSAGE_LENGTH),
            ],
        )
        self._stub = inference_pb2_grpc.InferenceServiceStub(self._channel)
        logger.info("remote_core_init", uri=uri)

    @property
    def device(self) -> str:
        """Report device as 'remote' — inference runs on the server."""
        return "remote"

    @property
    def compute_type(self) -> str:
        """Report compute type as 'remote'."""
        return "remote"

    @property
    def manager(self) -> RemoteCoreManager:
        """Return a proxy manager for compatibility with engines that read stats."""
        return RemoteCoreManager(self._stub)

    def transcribe(
        self,
        audio: str | Path | np.ndarray,
        model_id: str,
        config: Any | None = None,
    ) -> RemoteTranscriptionResult:
        """Run transcription via the remote gRPC inference server.

        Compatible with both TranscribeCore and ParakeetCore interfaces.

        Args:
            audio: File path string/Path or numpy float32 array (mono, 16kHz)
            model_id: Model identifier (e.g. "large-v3-turbo")
            config: Transcription parameters (TranscribeConfig, RemoteTranscribeConfig, or None)

        Returns:
            RemoteTranscriptionResult with segments, language, and duration.
        """
        # Encode audio based on type
        if isinstance(audio, np.ndarray):
            audio_bytes = audio.astype(np.float32).tobytes()
            fmt = inference_pb2.PCM_F32LE_16K
        elif isinstance(audio, (str, Path)):
            audio_bytes = Path(audio).read_bytes()
            fmt = inference_pb2.FILE
        else:
            raise ValueError(f"Unsupported audio type: {type(audio)}")

        # Build proto config
        proto_config = self._to_proto_config(config)

        request = inference_pb2.TranscribeRequest(
            audio=audio_bytes,
            format=fmt,
            model_id=model_id or "",
            config=proto_config,
        )

        try:
            response = self._stub.Transcribe(request)
        except grpc.RpcError as e:
            logger.error(
                "remote_transcribe_failed",
                uri=self._uri,
                code=e.code().name if hasattr(e, "code") else "UNKNOWN",
                details=e.details() if hasattr(e, "details") else str(e),
            )
            raise

        return self._from_proto_response(response)

    def get_status(self) -> dict[str, Any]:
        """Query the inference server status."""
        try:
            response = self._stub.GetStatus(inference_pb2.StatusRequest())
            return {
                "runtime": response.runtime,
                "device": response.device,
                "loaded_models": list(response.loaded_models),
                "total_capacity": response.total_capacity,
                "available_capacity": response.available_capacity,
                "healthy": response.healthy,
            }
        except grpc.RpcError as e:
            logger.warning("remote_status_failed", error=str(e))
            return {"healthy": False, "error": str(e)}

    def get_stats(self) -> dict[str, Any]:
        """Get model manager statistics (proxied from server)."""
        status = self.get_status()
        return {
            "loaded_models": status.get("loaded_models", []),
            "model_count": len(status.get("loaded_models", [])),
        }

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Not applicable for remote core."""
        return None

    def normalize_model_id(self, model_id: str) -> str:
        """Pass through — model normalization happens on the server."""
        return model_id

    def shutdown(self) -> None:
        """Close the gRPC channel."""
        logger.info("remote_core_shutdown", uri=self._uri)
        self._channel.close()

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _to_proto_config(config: Any) -> inference_pb2.TranscribeConfig:
        """Convert any config object to proto TranscribeConfig."""
        if config is None:
            return inference_pb2.TranscribeConfig(
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                temperature=0.0,
                task="transcribe",
            )

        # Duck-type: works with TranscribeConfig, RemoteTranscribeConfig, or dict
        if isinstance(config, dict):
            return inference_pb2.TranscribeConfig(
                language=config.get("language", ""),
                beam_size=config.get("beam_size", 5),
                vad_filter=config.get("vad_filter", True),
                word_timestamps=config.get("word_timestamps", True),
                temperature=float(config.get("temperature", 0.0)),
                task=config.get("task", "transcribe"),
                initial_prompt=config.get("initial_prompt", ""),
                hotwords=config.get("hotwords", ""),
            )

        # Dataclass-style config objects
        lang = getattr(config, "language", None) or ""
        return inference_pb2.TranscribeConfig(
            language=lang,
            beam_size=getattr(config, "beam_size", 5),
            vad_filter=getattr(config, "vad_filter", True),
            word_timestamps=getattr(config, "word_timestamps", True),
            temperature=float(getattr(config, "temperature", 0.0)),
            task=getattr(config, "task", "transcribe"),
            initial_prompt=getattr(config, "initial_prompt", None) or "",
            hotwords=getattr(config, "hotwords", None) or "",
        )

    @staticmethod
    def _from_proto_response(
        response: inference_pb2.TranscribeResponse,
    ) -> RemoteTranscriptionResult:
        """Convert proto response to RemoteTranscriptionResult."""
        segments = []
        text_parts = []
        for seg in response.segments:
            words = [
                RemoteWordResult(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                    confidence=w.probability,
                )
                for w in seg.words
            ]
            segments.append(
                RemoteSegmentResult(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    words=words,
                    avg_logprob=(
                        seg.avg_logprob if seg.HasField("avg_logprob") else None
                    ),
                    compression_ratio=(
                        seg.compression_ratio
                        if seg.HasField("compression_ratio")
                        else None
                    ),
                    no_speech_prob=(
                        seg.no_speech_prob
                        if seg.HasField("no_speech_prob")
                        else None
                    ),
                )
            )
            text_parts.append(seg.text)

        return RemoteTranscriptionResult(
            text=" ".join(text_parts),
            segments=segments,
            language=response.language,
            language_probability=response.language_probability,
            duration=response.duration,
        )


class RemoteCoreManager:
    """Proxy manager that fetches stats from the remote inference server.

    Provides the minimal interface expected by engine adapters (get_stats,
    ttl_seconds, max_loaded, model_storage) without requiring actual model
    management — that's handled by the inference server.
    """

    def __init__(self, stub: inference_pb2_grpc.InferenceServiceStub) -> None:
        self._stub = stub

    @property
    def ttl_seconds(self) -> int:
        return 0  # Not applicable for remote

    @property
    def max_loaded(self) -> int:
        try:
            response = self._stub.GetStatus(inference_pb2.StatusRequest())
            return response.total_capacity
        except grpc.RpcError:
            return 0

    @property
    def model_storage(self) -> None:
        return None

    def get_stats(self) -> dict[str, Any]:
        try:
            response = self._stub.GetStatus(inference_pb2.StatusRequest())
            return {
                "loaded_models": list(response.loaded_models),
                "model_count": len(response.loaded_models),
                "max_loaded": response.total_capacity,
            }
        except grpc.RpcError:
            return {"loaded_models": [], "model_count": 0, "max_loaded": 0}
