"""FluidAudio speaker diarization adapter (M104).

A thin batch engine that forwards audio to ``fluidaudio-server`` (see
``engines/stt-transcribe/fluidaudio/server/``), which runs the pyannote
community-1 pipeline as Core ML models on the Apple Neural Engine.  All of
the Dalston contract lives here; none of the inference does.

Environment variables:
    DALSTON_FLUIDAUDIO_URL: Base URL of fluidaudio-server
        (default: http://127.0.0.1:9200)
    DALSTON_FLUIDAUDIO_TIMEOUT_S: Per-request inference timeout
        (default: 7200)
    DALSTON_DEFAULT_MODEL: Diarization model when a task does not pin one
        (default: FluidInference/speaker-diarization-coreml)

Run on the Mac:
    python engines/stt-diarize/fluidaudio/engine.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dalston.common.pipeline_types import DiarizationResponse, SpeakerTurn
from dalston.engine_sdk import (
    BatchTaskContext,
    Engine,
    EngineCapabilities,
    TaskRequest,
    TaskResponse,
)
from dalston.engine_sdk.diarize_chunking import overlap_stats_from_turns
from dalston.engine_sdk.sidecar_http import SidecarError, SidecarHTTPClient

#: FluidAudio Hugging Face repo id for the pyannote community-1 Core ML
#: bundle; the server's model key and the ``source`` / ``loaded_model_id``
#: of ``models/fluidaudio-speaker-diarization.yaml``.
SPEAKER_DIARIZATION_REPO = "FluidInference/speaker-diarization-coreml"

DEFAULT_SERVER_URL = "http://127.0.0.1:9200"
ENGINE_VERSION = "1.0.0"


def make_client() -> SidecarHTTPClient:
    """Build the sidecar client from the environment."""
    return SidecarHTTPClient.from_env(
        "DALSTON_FLUIDAUDIO_URL",
        default_url=DEFAULT_SERVER_URL,
        timeout_var="DALSTON_FLUIDAUDIO_TIMEOUT_S",
    )


class FluidAudioDiarizeEngine(Engine):
    """Speaker diarization via fluidaudio-server (Core ML pyannote)."""

    ENGINE_ID = "fluidaudio-pyannote"

    def __init__(self, client: SidecarHTTPClient | None = None) -> None:
        super().__init__()
        self._client = client if client is not None else make_client()
        self._default_model = os.environ.get(
            "DALSTON_DEFAULT_MODEL", SPEAKER_DIARIZATION_REPO
        )
        self.logger.info(
            "engine_init",
            engine_id=self.engine_id,
            server_url=self._client.base_url,
            default_model=self._default_model,
        )

    def create_http_server(self, port: int = 9100):  # type: ignore[override]  # covariant return
        from dalston.engine_sdk.http_diarize import DiarizeHTTPServer

        return DiarizeHTTPServer(engine=self, port=port)

    # -- Inference -----------------------------------------------------------

    def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        audio_path = task_request.audio_path
        if audio_path is None:
            raise ValueError("audio_path is required for diarization")
        params = task_request.get_diarize_params()
        model = params.loaded_model_id or self._default_model

        warnings: list[str] = []
        if params.exclusive:
            # FluidAudio's offline pipeline already emits non-overlapping
            # segments, so there is nothing extra to do.
            warnings.append("exclusive=true is implicit for fluidaudio-pyannote")
        if params.max_chunk_s is not None:
            warnings.append("max_chunk_s ignored: fluidaudio-pyannote does not chunk")

        self.logger.info(
            "processing_diarization",
            audio_path=str(audio_path),
            model=model,
            num_speakers=params.num_speakers,
            min_speakers=params.min_speakers,
            max_speakers=params.max_speakers,
        )
        self._set_runtime_state(loaded_model=model, status="processing")
        try:
            result = self._client.post_audio(
                "/v1/diarize",
                Path(audio_path),
                fields={
                    "model": model,
                    "num_speakers": params.num_speakers,
                    "min_speakers": params.min_speakers,
                    "max_speakers": params.max_speakers,
                },
            )
        finally:
            self._set_runtime_state(loaded_model=model, status="idle")

        return TaskResponse(data=self._to_response(result, warnings))

    def _to_response(
        self, result: dict[str, Any], warnings: list[str]
    ) -> DiarizationResponse:
        turns = [
            SpeakerTurn(
                speaker=str(seg["speaker"]),
                start=float(seg["start"]),
                end=float(seg["end"]),
            )
            for seg in result.get("segments", [])
        ]
        speakers = sorted({t.speaker for t in turns})
        overlap_duration, overlap_ratio = overlap_stats_from_turns(turns)
        warnings = [*warnings, *(str(w) for w in result.get("warnings", []))]

        self.logger.info(
            "diarization_complete",
            model=result.get("model"),
            num_speakers=len(speakers),
            num_turns=len(turns),
        )
        return DiarizationResponse(
            turns=turns,
            speakers=speakers,
            num_speakers=len(speakers),
            overlap_duration=overlap_duration,
            overlap_ratio=overlap_ratio,
            engine_id=self.engine_id,
            warnings=warnings,
        )

    # -- Health / capabilities ------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        server = self._client.health()
        healthy = server.get("status") == "ok"
        return {
            "status": "healthy" if healthy else "unhealthy",
            "engine_id": self.engine_id,
            "server": server,
        }

    def get_capabilities(self) -> EngineCapabilities:
        variants: list[str] = [SPEAKER_DIARIZATION_REPO]
        try:
            caps = self._client.get_json("/v1/capabilities")
            served = caps.get("diarizer_models")
            if isinstance(served, list) and served:
                variants = [str(m) for m in served]
        except SidecarError as exc:
            self.logger.warning("server_capabilities_unavailable", error=str(exc))

        return EngineCapabilities(
            engine_id=self.engine_id,
            version=ENGINE_VERSION,
            stages=["diarize"],
            supports_word_timestamps=False,
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
    engine = FluidAudioDiarizeEngine()
    engine.run()
