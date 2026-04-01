"""Pyannote 4.0 speaker diarization engine.

Uses pyannote-audio 4.0 with the new Community-1 pipeline featuring
VBx clustering for improved speaker counting and assignment.

Requires HuggingFace token for accessing gated models.
Automatically chunks long audio files to avoid GPU OOM (see M84).
"""

import os
from typing import Any

from dalston.engine_sdk import (
    BatchTaskContext,
    DiarizationResponse,
    Engine,
    SpeakerTurn,
    TaskRequest,
    TaskResponse,
    detect_device,
)
from dalston.engine_sdk.diarize_chunking import (
    DEFAULT_MAX_CHUNK_S,
    get_audio_duration,
    overlap_stats_from_turns,
    run_chunked_diarization,
)


class PyannoteEngine(Engine):
    """Pyannote 4.0 speaker diarization engine.

    Lazily loads the pyannote pipeline on first request. Supports
    optional min/max speaker count hints and exclusive diarization mode.

    New in 4.0:
        - Community-1 pipeline with VBx clustering (improved speaker counting)
        - Exclusive mode: single-speaker output per segment (better Whisper alignment)
        - NumPy 2.0 and modern HuggingFace hub compatibility

    Environment Variables:
        HF_TOKEN: HuggingFace token for accessing gated pyannote models
        DALSTON_DEVICE: Device to use ("cuda", "mps", "cpu", or unset for auto-detect)
    """

    def __init__(self) -> None:
        super().__init__()
        self._pipelines: dict[str, Any] = {}
        self._active_model_id: str | None = None
        self._device = detect_device()
        self._max_chunk_s = float(
            os.environ.get("DALSTON_MAX_DIARIZE_CHUNK_S", DEFAULT_MAX_CHUNK_S)
        )
        self.logger.info(
            "pyannote_4_0_engine_initialized",
            device=self._device,
            max_chunk_s=self._max_chunk_s,
        )

    def _get_hf_token(self, config: dict[str, Any]) -> str:
        """Get HuggingFace token from config or environment.

        Raises:
            RuntimeError: If no HF_TOKEN is configured
        """
        token = config.get("hf_token") or os.environ.get("HF_TOKEN")
        self.logger.info(
            "hf_token_lookup",
            from_config=bool(config.get("hf_token")),
            from_env=bool(os.environ.get("HF_TOKEN")),
            token_present=bool(token),
        )
        if not token:
            raise RuntimeError(
                "HF_TOKEN environment variable is required for pyannote diarization. "
                "Get a token from https://huggingface.co/settings/tokens and accept "
                "the pyannote/speaker-diarization-community-1 model agreement."
            )
        return token

    def _load_pipeline(self, model_id: str, hf_token: str | None) -> Any:
        """Load pyannote pipeline lazily.

        Args:
            model_id: Runtime model identifier to load
            hf_token: HuggingFace token for authentication

        Returns:
            Loaded pyannote Pipeline instance
        """
        if model_id in self._pipelines:
            return self._pipelines[model_id]

        self.logger.info("loading_pyannote_pipeline", model_id=model_id)

        from pyannote.audio import Pipeline

        # Pyannote 4.0 uses 'token' parameter and requires 'revision' keyword
        # Use 'main' revision for latest stable version
        pipeline = Pipeline.from_pretrained(
            model_id,
            token=hf_token,
            revision="main",
        )

        # Move to appropriate device
        if self._device in ("cuda", "mps"):
            import torch

            pipeline = pipeline.to(torch.device(self._device))

        self._pipelines[model_id] = pipeline
        self.logger.info("pyannote_4_0_pipeline_loaded_successfully")
        return pipeline

    def create_http_server(self, port: int = 9100):  # type: ignore[override]  # covariant return
        """Return a ``DiarizeHTTPServer`` with ``POST /v1/diarize``."""
        from dalston.engine_sdk.http_diarize import DiarizeHTTPServer

        return DiarizeHTTPServer(engine=self, port=port)

    def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        """Run speaker diarization on audio file.

        Args:
            task_request: Task input with audio path and config

        Returns:
            TaskResponse with DiarizationResponse containing speakers and turns
        """
        audio_path = task_request.audio_path
        if audio_path is None:
            raise ValueError("audio_path is required for diarization")
        params = task_request.get_diarize_params()

        self.logger.info("processing_diarization", audio_path=str(audio_path))

        loaded_model_id = params.loaded_model_id
        if not loaded_model_id:
            loaded_model_id = os.environ.get(
                "DALSTON_DEFAULT_MODEL",
                "pyannote/speaker-diarization-community-1",
            )

        # Get speaker count hints
        min_speakers = params.min_speakers
        max_speakers = params.max_speakers
        exclusive = params.exclusive

        if min_speakers:
            self.logger.info("min_speakers_hint", min_speakers=min_speakers)
        if max_speakers:
            self.logger.info("max_speakers_hint", max_speakers=max_speakers)
        if exclusive:
            self.logger.info("exclusive_mode_enabled")

        # Load pipeline (lazy)
        hf_token = self._get_hf_token(task_request.config)
        pipeline = self._load_pipeline(loaded_model_id, hf_token)
        self._active_model_id = loaded_model_id
        self._set_runtime_state(loaded_model=loaded_model_id, status="processing")
        try:
            # Build diarization parameters
            diarization_params: dict[str, Any] = {}
            if min_speakers is not None:
                diarization_params["min_speakers"] = min_speakers
            if max_speakers is not None:
                diarization_params["max_speakers"] = max_speakers

            # Check duration and branch to chunked path if needed
            duration = get_audio_duration(audio_path)

            if duration > self._max_chunk_s:
                speakers, turns = run_chunked_diarization(
                    pipeline,
                    audio_path,
                    diarization_params,
                    hf_token=hf_token,
                    device=self._device,
                    convert_annotation=self._convert_annotation,
                    exclusive=bool(exclusive),
                    max_chunk_s=self._max_chunk_s,
                    log=self.logger,
                )
                overlap_duration, overlap_ratio = overlap_stats_from_turns(turns)
            else:
                self.logger.info("running_diarization")
                diarization = pipeline(str(audio_path), **diarization_params)

                # Apply exclusive mode if requested (new in pyannote 4.0)
                if exclusive and hasattr(diarization, "exclusive_speaker_diarization"):
                    diarization = diarization.exclusive_speaker_diarization

                speakers, turns = self._convert_annotation(diarization)
                overlap_duration, overlap_ratio = self._calculate_overlap_stats(
                    diarization
                )

            self.logger.info(
                "diarization_complete",
                speaker_count=len(speakers),
                segment_count=len(turns),
                overlap_ratio=round(overlap_ratio, 3),
            )

            output = DiarizationResponse(
                speakers=speakers,
                turns=turns,
                num_speakers=len(speakers),
                overlap_duration=round(overlap_duration, 3),
                overlap_ratio=round(overlap_ratio, 3),
                engine_id="pyannote-4.0",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskResponse(data=output)
        finally:
            self._set_runtime_state(loaded_model=loaded_model_id, status="idle")

    def _convert_annotation(self, diarization) -> tuple[list[str], list[SpeakerTurn]]:
        """Convert pyannote diarization output to speakers list and turns.

        Args:
            diarization: pyannote Annotation or DiarizationResponse object

        Returns:
            Tuple of (speakers list, speaker turns list)
        """
        speakers_set: set[str] = set()
        turns: list[SpeakerTurn] = []

        # Pyannote 4.0 community pipeline returns DiarizationResponse with .speaker_diarization
        # Fall back to the object itself if it's already an Annotation (3.x compatibility)
        if hasattr(diarization, "speaker_diarization"):
            annotation = diarization.speaker_diarization
        else:
            annotation = diarization

        for turn, _, speaker in annotation.itertracks(yield_label=True):
            speakers_set.add(speaker)
            turns.append(
                SpeakerTurn(
                    start=round(turn.start, 3),
                    end=round(turn.end, 3),
                    speaker=speaker,
                )
            )

        # Sort speakers for consistent ordering
        speakers = sorted(speakers_set)

        # Sort turns by start time
        turns.sort(key=lambda t: t.start)

        return speakers, turns

    def _calculate_overlap_stats(self, diarization) -> tuple[float, float]:
        """Calculate overlap statistics using pyannote's native overlap detection.

        Args:
            diarization: pyannote DiarizationResponse or Annotation object

        Returns:
            Tuple of (overlap_duration, overlap_ratio)
        """
        try:
            # Extract annotation from DiarizationResponse if needed (4.0 format)
            if hasattr(diarization, "speaker_diarization"):
                annotation = diarization.speaker_diarization
            else:
                annotation = diarization

            # Use pyannote's native overlap detection
            # get_overlap() returns a Timeline of overlapping regions
            overlap_timeline = annotation.get_overlap()
            overlap_duration = (
                sum(segment.duration for segment in overlap_timeline)
                if overlap_timeline
                else 0.0
            )

            # Get total duration from annotation
            total_duration = (
                annotation.get_timeline().duration()
                if annotation.get_timeline()
                else 0.0
            )
            overlap_ratio = (
                overlap_duration / total_duration if total_duration > 0 else 0.0
            )

            return overlap_duration, overlap_ratio
        except Exception as e:
            self.logger.warning("failed_to_calculate_overlap", error=str(e))
            return 0.0, 0.0

    def health_check(self) -> dict[str, Any]:
        """Return health status including device and model info."""
        return {
            "status": "healthy",
            "device": self._device,
            "pipeline_loaded": bool(self._pipelines),
            "loaded_models": sorted(self._pipelines.keys()),
            "active_model_id": self._active_model_id,
            "version": "4.0",
        }


if __name__ == "__main__":
    engine = PyannoteEngine()
    engine.run()
