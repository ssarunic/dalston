"""Pyannote 3.1 speaker diarization engine.

Uses pyannote-audio for speaker diarization - identifying who speaks when.
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
    """Pyannote 3.1 speaker diarization engine.

    Lazily loads the pyannote pipeline on first request. Supports
    optional min/max speaker count hints for improved clustering.

    Environment Variables:
        HF_TOKEN: HuggingFace token for accessing gated pyannote models
        DIARIZATION_DISABLED: Set to "true" to skip diarization (returns mock output)
        DEVICE: Device to use ("cuda", "cpu", or unset for auto-detect)
    """

    MODEL_ID = "pyannote/speaker-diarization-3.1"

    def __init__(self) -> None:
        super().__init__()
        self._pipeline = None
        self._device = self._detect_device()
        self._disabled = (
            os.environ.get("DALSTON_DIARIZATION_DISABLED", "").lower() == "true"
        )

        if self._disabled:
            self.logger.warning("diarization_disabled")
        else:
            self.logger.info("pyannote_engine_initialized", device=self._device)

    def _detect_device(self) -> str:
        """Resolve inference device from DEVICE env with auto-detect fallback."""
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        try:
            import torch

            cuda_available = torch.cuda.is_available()
        except ImportError:
            cuda_available = False

        if requested_device == "cpu":
            self.logger.info("device_forced_cpu")
            return "cpu"

        if requested_device == "cuda":
            if not cuda_available:
                raise RuntimeError(
                    "DEVICE=cuda but CUDA is not available for pyannote-3.1."
                )
            self.logger.info("cuda_available_using_gpu")
            return "cuda"

        if requested_device in ("", "auto"):
            if cuda_available:
                self.logger.info("cuda_available_using_gpu")
                return "cuda"

            self.logger.info("cuda_not_available_using_cpu")
            return "cpu"

        raise ValueError(f"Unknown DEVICE value: {requested_device}. Use cuda or cpu.")

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
                "the pyannote/speaker-diarization-3.1 model agreement."
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

        self._pipeline = Pipeline.from_pretrained(
            self.MODEL_ID,
            use_auth_token=hf_token,
        )

        # Move to appropriate device
        if self._device == "cuda":
            import torch

            self._pipeline = self._pipeline.to(torch.device("cuda"))

        self.logger.info("pyannote_pipeline_loaded_successfully")
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

        if min_speakers:
            self.logger.info("min_speakers_hint", min_speakers=min_speakers)
        if max_speakers:
            self.logger.info("max_speakers_hint", max_speakers=max_speakers)

        # Load pipeline (lazy)
        hf_token = self._get_hf_token(config)
        pipeline = self._load_pipeline(hf_token)

        # Run diarization
        diarization_params = {}
        if min_speakers is not None:
            diarization_params["min_speakers"] = min_speakers
        if max_speakers is not None:
            diarization_params["max_speakers"] = max_speakers

        self.logger.info("running_diarization")
        diarization = pipeline(str(audio_path), **diarization_params)

        # Convert pyannote Annotation to our output format
        speakers, turns = self._convert_annotation(diarization)

        # Calculate overlap statistics using pyannote's native detection
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
            engine_id="pyannote-3.1",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

    def _convert_annotation(self, annotation) -> tuple[list[str], list[SpeakerTurn]]:
        """Convert pyannote Annotation to speakers list and turns.

        Args:
            annotation: pyannote Annotation object

        Returns:
            Tuple of (speakers list, speaker turns list)
        """
        speakers_set: set[str] = set()
        turns: list[SpeakerTurn] = []

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

    def _calculate_overlap_stats(self, annotation) -> tuple[float, float]:
        """Calculate overlap statistics using pyannote's native overlap detection.

        Args:
            annotation: pyannote Annotation object

        Returns:
            Tuple of (overlap_duration, overlap_ratio)
        """
        try:
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
            engine_id="pyannote-3.1",
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
        }


if __name__ == "__main__":
    engine = PyannoteEngine()
    engine.run()
