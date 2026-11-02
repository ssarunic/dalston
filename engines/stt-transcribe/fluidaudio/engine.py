"""FluidAudio transcription adapter (M104).

A thin batch engine that forwards audio to ``fluidaudio-server``, the Swift
sidecar in ``server/`` that runs Parakeet as Core ML models on the Apple
Neural Engine.  All of the Dalston contract (Redis streams, heartbeats, S3,
typed Transcript) lives here; none of the inference does.

Environment variables:
    DALSTON_FLUIDAUDIO_URL: Base URL of fluidaudio-server
        (default: http://127.0.0.1:9200)
    DALSTON_FLUIDAUDIO_TIMEOUT_S: Per-request inference timeout
        (default: 7200)
    DALSTON_FLUIDAUDIO_STARTUP_TIMEOUT_S: How long to wait for the server's
        /health before registering with the control plane. A cold server
        downloads and compiles Core ML models first (default: 1800)
    DALSTON_DEFAULT_MODEL: Model used when a task does not pin one. For the
        transcribe stage the orchestrator passes the registry model's
        ``source``, i.e. the FluidAudio Hugging Face repo id, so this is one
        of those ids too (default: FluidInference/parakeet-tdt-0.6b-v3-coreml).

Run on the Mac:
    python engines/stt-transcribe/fluidaudio/engine.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from dalston.common.pipeline_types import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineCapabilities,
    TaskRequest,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.segments import words_to_segments
from dalston.engine_sdk.sidecar_http import (
    DEFAULT_STARTUP_TIMEOUT_S,
    SidecarError,
    SidecarHTTPClient,
)

#: FluidAudio Hugging Face repo ids. These are the model keys the server
#: understands and the ``source`` / ``loaded_model_id`` of the matching
#: entries in ``models/``. Kept in step with FluidAudio's ``Repo`` enum; the
#: same ids appear in dalston-mobile's FluidTranscriptionEngine.swift.
PARAKEET_V3_REPO = "FluidInference/parakeet-tdt-0.6b-v3-coreml"
PARAKEET_ULTRA_REPO = "FluidInference/parakeet-ultra-coreml"
ASR_MODELS: tuple[str, ...] = (PARAKEET_V3_REPO, PARAKEET_ULTRA_REPO)

DEFAULT_SERVER_URL = "http://127.0.0.1:9200"
ENGINE_VERSION = "1.0.0"

#: FluidAudio's TDT decoder passes SentencePiece's unknown token through as
#: text (``S&P`` comes back as ``S<unk>P.``); NeMo's decoder drops it. Strip
#: it the same way rather than guess which character it stood for.
UNK_TOKEN = "<unk>"
_MULTI_SPACE = re.compile(r" {2,}")


def _strip_unk(text: str) -> str:
    if UNK_TOKEN not in text:
        return text
    return _MULTI_SPACE.sub(" ", text.replace(UNK_TOKEN, "")).strip()


# Parakeet TDT 0.6B v3 languages (25 European). FluidAudio's Language only
# filters tokens by script, so this is advisory, not forcing.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru",
    "uk",
)  # fmt: skip


def make_client() -> SidecarHTTPClient:
    """Build the sidecar client from the environment."""
    return SidecarHTTPClient.from_env(
        "DALSTON_FLUIDAUDIO_URL",
        default_url=DEFAULT_SERVER_URL,
        timeout_var="DALSTON_FLUIDAUDIO_TIMEOUT_S",
    )


def startup_timeout_s() -> float:
    """Seconds to wait for the server before registering (see module doc)."""
    return float(
        os.environ.get(
            "DALSTON_FLUIDAUDIO_STARTUP_TIMEOUT_S", str(DEFAULT_STARTUP_TIMEOUT_S)
        )
    )


class FluidAudioBatchEngine(BaseBatchTranscribeEngine):
    """Batch transcription via fluidaudio-server (Core ML Parakeet)."""

    ENGINE_ID = "fluidaudio"

    def __init__(self, client: SidecarHTTPClient | None = None) -> None:
        super().__init__()
        self._client = client if client is not None else make_client()
        self._default_model = os.environ.get("DALSTON_DEFAULT_MODEL", PARAKEET_V3_REPO)
        self.logger.info(
            "engine_init",
            engine_id=self.engine_id,
            server_url=self._client.base_url,
            default_model=self._default_model,
        )

    # -- Inference -----------------------------------------------------------

    def transcribe_audio(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> Transcript:
        audio_path = task_request.audio_path
        if audio_path is None:
            raise ValueError("audio_path is required for transcription")
        params = task_request.get_transcribe_params()
        model = params.loaded_model_id or self._default_model
        # "auto" means detect, which the server cannot do; send nothing.
        requested_language = (
            params.language if params.language and params.language != "auto" else None
        )

        self.logger.info(
            "transcribing",
            audio_path=str(audio_path),
            model=model,
            language=requested_language,
        )
        self._set_runtime_state(loaded_model=model, status="processing")
        try:
            result = self._client.post_audio(
                "/v1/transcribe",
                Path(audio_path),
                fields={"model": model, "language": requested_language},
            )
        finally:
            self._set_runtime_state(loaded_model=model, status="idle")

        return self._to_transcript(result, requested_language=requested_language)

    def _to_transcript(
        self, result: dict[str, Any], *, requested_language: str | None
    ) -> Transcript:
        text = _strip_unk(str(result.get("text", "")).strip())
        words: list[TranscriptWord] = [
            self.build_word(
                text=word_text,
                start=float(w["start"]),
                end=float(w["end"]),
                confidence=w.get("confidence"),
            )
            for w in result.get("words", [])
            if (word_text := _strip_unk(str(w["text"])))
        ]

        # Parakeet does not identify the language, so the server reports
        # none. With no explicit request, say "und" (ISO 639 undetermined)
        # with no provenance rather than fabricate a code, as the ONNX and
        # NeMo engines do (M92 review R3).
        detected = result.get("language")
        language_source: Literal["requested", "detected"] | None
        if detected:
            language, language_source = str(detected), "detected"
        elif requested_language:
            language, language_source = requested_language, "requested"
        else:
            language, language_source = "und", None

        segments: list[TranscriptSegment] = words_to_segments(
            words,
            word_text=lambda w: w.text,
            word_start=lambda w: w.start,
            word_end=lambda w: w.end,
            make_segment=lambda start, end, seg_text, seg_words: self.build_segment(
                start=start,
                end=end,
                text=seg_text,
                words=seg_words or None,
                language=language,
            ),
            full_text=text,
        )

        duration = result.get("duration")
        self.logger.info(
            "transcription_complete",
            model=result.get("model"),
            word_count=len(words),
            segment_count=len(segments),
            char_count=len(text),
        )
        return self.build_transcript(
            text=text,
            segments=segments,
            language=language,
            language_source=language_source,
            engine_id=self.engine_id,
            duration=float(duration) if duration is not None else None,
            warnings=list(result.get("warnings", [])),
        )

    # -- Lifecycle / health / capabilities -------------------------------------

    def run(self) -> None:
        """Wait for the server, then start the batch loop.

        The runner registers and consumes tasks as soon as it starts and
        heartbeats without consulting ``health_check``, so registering
        before the server answers would route jobs into connection errors
        while it is still compiling models.
        """
        self._client.wait_until_ready(timeout_s=startup_timeout_s())
        super().run()

    def health_check(self) -> dict[str, Any]:
        server = self._client.health()
        healthy = server.get("status") == "ok"
        return {
            "status": "healthy" if healthy else "unhealthy",
            "engine_id": self.engine_id,
            "server": server,
        }

    def get_capabilities(self) -> EngineCapabilities:
        variants: list[str] = list(ASR_MODELS)
        try:
            caps = self._client.get_json("/v1/capabilities")
            served = caps.get("asr_models")
            if isinstance(served, list) and served:
                variants = [str(m) for m in served]
        except SidecarError as exc:
            self.logger.warning("server_capabilities_unavailable", error=str(exc))

        return EngineCapabilities(
            engine_id=self.engine_id,
            version=ENGINE_VERSION,
            stages=["transcribe"],
            supports_word_timestamps=True,
            supports_native_streaming=False,
            model_variants=variants,
            gpu_required=False,
            gpu_vram_mb=0,
            supports_cpu=True,
            min_ram_gb=8,
            # Pessimistic placeholders until measured (M104.5); see engine.yaml.
            rtf_gpu=0.3,
            rtf_cpu=0.3,
            max_concurrency=1,
        )

    def shutdown(self) -> None:
        self.logger.info("engine_shutdown")
        self._client.close()


if __name__ == "__main__":
    engine = FluidAudioBatchEngine()
    engine.run()
