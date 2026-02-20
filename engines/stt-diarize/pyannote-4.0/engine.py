"""Pyannote 4.0 speaker diarization engine.

Uses pyannote-audio 4.0 with the new Community-1 pipeline featuring
VBx clustering for improved speaker counting and assignment.

Requires HuggingFace token for accessing gated models.
"""

import os
from typing import Any

from dalston.engine_sdk import (
    DiarizeOutput,
    Engine,
    SpeakerTurn,
    TaskInput,
    TaskOutput,
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
        DIARIZATION_DISABLED: Set to "true" to skip diarization (returns mock output)
    """

    # Pyannote 4.0 community-1 pipeline
    MODEL_ID = "pyannote/speaker-diarization-community-1"

    def __init__(self) -> None:
        super().__init__()
        self._pipeline = None
        self._device = self._detect_device()
        self._disabled = os.environ.get("DIARIZATION_DISABLED", "").lower() == "true"

        if self._disabled:
            self.logger.warning("diarization_disabled")
        else:
            self.logger.info("pyannote_4_0_engine_initialized", device=self._device)

    def _detect_device(self) -> str:
        """Detect the best available device (CUDA or CPU)."""
        try:
            import torch

            if torch.cuda.is_available():
                self.logger.info("cuda_available_using_gpu")
                return "cuda"
        except ImportError:
            pass

        self.logger.info("cuda_not_available_using_cpu")
        return "cpu"

    def _get_hf_token(self, config: dict[str, Any]) -> str:
        """Get HuggingFace token from config or environment.

        Raises:
            RuntimeError: If no HF_TOKEN is configured
        """
        token = config.get("hf_token") or os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "HF_TOKEN environment variable is required for pyannote diarization. "
                "Get a token from https://huggingface.co/settings/tokens and accept "
                "the pyannote/speaker-diarization-community-1 model agreement."
            )
        return token

    def _load_pipeline(self, hf_token: str | None) -> Any:
        """Load pyannote pipeline lazily.

        Args:
            hf_token: HuggingFace token for authentication

        Returns:
            Loaded pyannote Pipeline instance
        """
        if self._pipeline is not None:
            return self._pipeline

        self.logger.info("loading_pyannote_pipeline", model_id=self.MODEL_ID)

        from pyannote.audio import Pipeline

        # Pyannote 4.0 uses 'token' parameter and requires 'revision' keyword
        # Use 'main' revision for latest stable version
        self._pipeline = Pipeline.from_pretrained(
            self.MODEL_ID,
            token=hf_token,
            revision="main",
        )

        # Move to appropriate device
        if self._device == "cuda":
            import torch

            self._pipeline = self._pipeline.to(torch.device("cuda"))

        self.logger.info("pyannote_4_0_pipeline_loaded_successfully")
        return self._pipeline

    def process(self, input: TaskInput) -> TaskOutput:
        """Run speaker diarization on audio file.

        Args:
            input: Task input with audio path and config

        Returns:
            TaskOutput with DiarizeOutput containing speakers and turns
        """
        # Check if diarization is disabled (for local dev/testing)
        if self._disabled:
            self.logger.info("diarization_disabled_returning_mock_output")
            return self._mock_output()

        audio_path = input.audio_path
        config = input.config

        self.logger.info("processing_diarization", audio_path=str(audio_path))

        # Get speaker count hints
        min_speakers = config.get("min_speakers")
        max_speakers = config.get("max_speakers")
        exclusive = config.get("exclusive", False)

        if min_speakers:
            self.logger.info("min_speakers_hint", min_speakers=min_speakers)
        if max_speakers:
            self.logger.info("max_speakers_hint", max_speakers=max_speakers)
        if exclusive:
            self.logger.info("exclusive_mode_enabled")

        # Load pipeline (lazy)
        hf_token = self._get_hf_token(config)
        pipeline = self._load_pipeline(hf_token)

        # Build diarization parameters
        diarization_params = {}
        if min_speakers is not None:
            diarization_params["min_speakers"] = min_speakers
        if max_speakers is not None:
            diarization_params["max_speakers"] = max_speakers

        self.logger.info("running_diarization")
        diarization = pipeline(str(audio_path), **diarization_params)

        # Apply exclusive mode if requested (new in pyannote 4.0)
        # This provides single-speaker output per segment for easier Whisper alignment
        if exclusive and hasattr(diarization, "exclusive_speaker_diarization"):
            diarization = diarization.exclusive_speaker_diarization

        # Convert pyannote output to our format
        speakers, turns = self._convert_annotation(diarization)

        # Calculate overlap statistics using pyannote's native overlap detection
        overlap_duration, overlap_ratio = self._calculate_overlap_stats(diarization)

        self.logger.info(
            "diarization_complete",
            speaker_count=len(speakers),
            segment_count=len(turns),
            overlap_ratio=round(overlap_ratio, 3),
        )

        output = DiarizeOutput(
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

        return TaskOutput(data=output)

    def _convert_annotation(self, diarization) -> tuple[list[str], list[SpeakerTurn]]:
        """Convert pyannote diarization output to speakers list and turns.

        Args:
            diarization: pyannote Annotation or DiarizeOutput object

        Returns:
            Tuple of (speakers list, speaker turns list)
        """
        speakers_set: set[str] = set()
        turns: list[SpeakerTurn] = []

        # Pyannote 4.0 community pipeline returns DiarizeOutput with .speaker_diarization
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
            diarization: pyannote DiarizeOutput or Annotation object

        Returns:
            Tuple of (overlap_duration, overlap_ratio)
        """
        try:
            # Extract annotation from DiarizeOutput if needed (4.0 format)
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

    def _mock_output(self) -> TaskOutput:
        """Return mock output when diarization is disabled.

        Useful for testing the pipeline without running actual diarization.
        """
        output = DiarizeOutput(
            speakers=["SPEAKER_00"],
            turns=[SpeakerTurn(start=0.0, end=999999.0, speaker="SPEAKER_00")],
            num_speakers=1,
            overlap_duration=0.0,
            overlap_ratio=0.0,
            engine_id="pyannote-4.0",
            skipped=True,
            skip_reason="DIARIZATION_DISABLED=true",
            warnings=["Diarization disabled via environment variable"],
        )

        return TaskOutput(data=output)

    def health_check(self) -> dict[str, Any]:
        """Return health status including device and model info."""
        cuda_available = False

        try:
            import torch

            cuda_available = torch.cuda.is_available()
        except ImportError:
            pass

        return {
            "status": "healthy",
            "device": self._device,
            "cuda_available": cuda_available,
            "pipeline_loaded": self._pipeline is not None,
            "diarization_disabled": self._disabled,
            "model_id": self.MODEL_ID,
            "version": "4.0",
        }


if __name__ == "__main__":
    engine = PyannoteEngine()
    engine.run()
