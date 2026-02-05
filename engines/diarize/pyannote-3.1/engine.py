"""Pyannote 3.1 speaker diarization engine.

Uses pyannote-audio for speaker diarization - identifying who speaks when.
Requires HuggingFace token for accessing gated models.
"""

import os
from typing import Any

import structlog

from dalston.engine_sdk import Engine, TaskInput, TaskOutput

logger = structlog.get_logger()


class PyannoteEngine(Engine):
    """Pyannote 3.1 speaker diarization engine.

    Lazily loads the pyannote pipeline on first request. Supports
    optional min/max speaker count hints for improved clustering.

    Environment Variables:
        HF_TOKEN: HuggingFace token for accessing gated pyannote models
        DIARIZATION_DISABLED: Set to "true" to skip diarization (returns mock output)
    """

    MODEL_ID = "pyannote/speaker-diarization-3.1"

    def __init__(self) -> None:
        super().__init__()
        self._pipeline = None
        self._device = self._detect_device()
        self._disabled = os.environ.get("DIARIZATION_DISABLED", "").lower() == "true"

        if self._disabled:
            logger.warning("diarization_disabled")
        else:
            logger.info("pyannote_engine_initialized", device=self._device)

    def _detect_device(self) -> str:
        """Detect the best available device (CUDA or CPU)."""
        try:
            import torch

            if torch.cuda.is_available():
                logger.info("cuda_available_using_gpu")
                return "cuda"
        except ImportError:
            pass

        logger.info("cuda_not_available_using_cpu")
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

        logger.info("loading_pyannote_pipeline", model_id=self.MODEL_ID)

        from pyannote.audio import Pipeline

        self._pipeline = Pipeline.from_pretrained(
            self.MODEL_ID,
            use_auth_token=hf_token,
        )

        # Move to appropriate device
        if self._device == "cuda":
            import torch

            self._pipeline = self._pipeline.to(torch.device("cuda"))

        logger.info("pyannote_pipeline_loaded_successfully")
        return self._pipeline

    def process(self, input: TaskInput) -> TaskOutput:
        """Run speaker diarization on audio file.

        Args:
            input: Task input with audio path and config

        Returns:
            TaskOutput with speakers list and diarization_segments
        """
        # Check if diarization is disabled (for local dev/testing)
        if self._disabled:
            logger.info("diarization_disabled_returning_mock_output")
            return self._mock_output()

        audio_path = input.audio_path
        config = input.config

        logger.info("processing_diarization", audio_path=str(audio_path))

        # Get speaker count hints
        min_speakers = config.get("min_speakers")
        max_speakers = config.get("max_speakers")

        if min_speakers:
            logger.info("min_speakers_hint", min_speakers=min_speakers)
        if max_speakers:
            logger.info("max_speakers_hint", max_speakers=max_speakers)

        # Load pipeline (lazy)
        hf_token = self._get_hf_token(config)
        pipeline = self._load_pipeline(hf_token)

        # Run diarization
        diarization_params = {}
        if min_speakers is not None:
            diarization_params["min_speakers"] = min_speakers
        if max_speakers is not None:
            diarization_params["max_speakers"] = max_speakers

        logger.info("running_diarization")
        diarization = pipeline(str(audio_path), **diarization_params)

        # Convert pyannote Annotation to our output format
        speakers, segments = self._convert_annotation(diarization)

        logger.info(
            "diarization_complete",
            speaker_count=len(speakers),
            segment_count=len(segments),
        )

        return TaskOutput(
            data={
                "speakers": speakers,
                "diarization_segments": segments,
            }
        )

    def _convert_annotation(self, annotation) -> tuple[list[str], list[dict]]:
        """Convert pyannote Annotation to speakers list and segments.

        Args:
            annotation: pyannote Annotation object

        Returns:
            Tuple of (speakers list, segments list)
        """
        speakers_set = set()
        segments = []

        for turn, _, speaker in annotation.itertracks(yield_label=True):
            speakers_set.add(speaker)
            segments.append(
                {
                    "start": round(turn.start, 3),
                    "end": round(turn.end, 3),
                    "speaker": speaker,
                }
            )

        # Sort speakers for consistent ordering
        speakers = sorted(speakers_set)

        # Sort segments by start time
        segments.sort(key=lambda s: s["start"])

        return speakers, segments

    def _mock_output(self) -> TaskOutput:
        """Return mock output when diarization is disabled.

        Useful for testing the pipeline without running actual diarization.
        """
        return TaskOutput(
            data={
                "speakers": ["SPEAKER_00"],
                "diarization_segments": [
                    {"start": 0.0, "end": 999999.0, "speaker": "SPEAKER_00"}
                ],
                "warning": {
                    "stage": "diarize",
                    "status": "skipped",
                    "reason": "DIARIZATION_DISABLED=true",
                },
            }
        )

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
