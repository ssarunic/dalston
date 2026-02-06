"""NVIDIA Parakeet transcription engine.

Uses NVIDIA NeMo Parakeet FastConformer RNNT models for fast
English-only speech-to-text transcription with GPU acceleration.

Parakeet produces native word-level timestamps via RNNT alignment,
eliminating the need for a separate alignment stage.
"""

from typing import Any

import structlog
import torch

from dalston.engine_sdk import Engine, TaskInput, TaskOutput

logger = structlog.get_logger()


class ParakeetEngine(Engine):
    """NVIDIA Parakeet transcription engine.

    Uses FastConformer encoder with RNNT decoder for efficient
    English-only transcription. Automatically produces word-level
    timestamps without requiring separate alignment.

    Requires NVIDIA GPU with CUDA support.
    """

    # Model variants (NeMo model identifiers)
    MODEL_VARIANTS = {
        "nvidia/parakeet-rnnt-0.6b": "nvidia/parakeet-rnnt-0.6b",
        "nvidia/parakeet-rnnt-1.1b": "nvidia/parakeet-rnnt-1.1b",
    }
    DEFAULT_MODEL = "nvidia/parakeet-rnnt-0.6b"

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._model_name: str | None = None

        # Verify CUDA availability
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Parakeet engine requires NVIDIA GPU with CUDA. "
                "No CUDA device detected."
            )

        self._device = "cuda"
        logger.info("cuda_available", device_count=torch.cuda.device_count())

    def _load_model(self, model_name: str) -> None:
        """Load the Parakeet model if not already loaded.

        Args:
            model_name: NeMo model identifier
        """
        # Only reload if model changed
        if self._model is not None and self._model_name == model_name:
            return

        logger.info("loading_parakeet_model", model_name=model_name)

        # Import NeMo ASR module
        try:
            import nemo.collections.asr as nemo_asr
        except ImportError as e:
            raise RuntimeError(
                "NeMo toolkit not installed. Install with: pip install nemo_toolkit[asr]"
            ) from e

        # Load pre-trained model from NGC
        self._model = nemo_asr.models.ASRModel.from_pretrained(model_name)
        self._model = self._model.to(self._device)
        self._model.eval()
        self._model_name = model_name

        logger.info("model_loaded_successfully", model_name=model_name)

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Parakeet RNNT.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with transcription text, segments, and words
        """
        audio_path = input.audio_path
        config = input.config

        # Get model configuration
        model_name = config.get("model", self.DEFAULT_MODEL)
        if model_name not in self.MODEL_VARIANTS:
            logger.warning(
                "unknown_model_variant",
                requested=model_name,
                using=self.DEFAULT_MODEL,
            )
            model_name = self.DEFAULT_MODEL

        # Load model
        self._load_model(model_name)

        logger.info("transcribing", audio_path=str(audio_path))

        # Transcribe with word-level timestamps
        # NeMo RNNT models can return word timestamps via the alignment
        with torch.cuda.amp.autocast():
            # Use transcribe method which returns text
            # For word timestamps, we use transcribe_with_timestamps
            transcriptions = self._model.transcribe(
                [str(audio_path)],
                batch_size=1,
                return_hypotheses=True,
            )

        # Process the hypothesis
        if not transcriptions:
            return TaskOutput(
                data={
                    "text": "",
                    "segments": [],
                    "language": "en",
                    "language_confidence": 1.0,
                }
            )

        hypothesis = transcriptions[0]

        # Extract text - handle both string and Hypothesis object
        if hasattr(hypothesis, "text"):
            full_text = hypothesis.text
        else:
            full_text = str(hypothesis)

        # Build segments with word-level timestamps
        segments = []
        words = []

        # Check if hypothesis has timesteps (word-level alignment)
        if hasattr(hypothesis, "timestep") and hypothesis.timestep is not None:
            # RNNT provides frame-level alignment that can be converted to timestamps
            # Each timestep corresponds to a token emission
            timesteps = hypothesis.timestep
            tokens = hypothesis.text.split()

            # Convert frame indices to seconds (assuming 10ms frame shift)
            frame_shift_seconds = 0.01

            current_words = []
            for i, (token, frame_idx) in enumerate(zip(tokens, timesteps)):
                word_start = frame_idx * frame_shift_seconds
                # Estimate word end from next token or add small duration
                if i + 1 < len(timesteps):
                    word_end = timesteps[i + 1] * frame_shift_seconds
                else:
                    word_end = word_start + 0.1  # Default duration

                word_data = {
                    "word": token,
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "confidence": 0.95,  # RNNT doesn't provide per-word confidence
                }
                current_words.append(word_data)
                words.append(word_data)

            # Create a single segment with all words
            if current_words:
                segments.append({
                    "start": current_words[0]["start"],
                    "end": current_words[-1]["end"],
                    "text": full_text.strip(),
                    "words": current_words,
                })
        else:
            # Fallback: create segment without word timestamps
            # This happens if the model doesn't support timestamp extraction
            segments.append({
                "start": 0.0,
                "end": 0.0,  # Unknown duration
                "text": full_text.strip(),
            })

        logger.info(
            "transcription_complete",
            segment_count=len(segments),
            word_count=len(words),
            char_count=len(full_text),
        )

        return TaskOutput(
            data={
                "text": full_text.strip(),
                "segments": segments,
                "language": "en",  # Parakeet is English-only
                "language_confidence": 1.0,
            }
        )

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability."""
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0
        cuda_memory_total = 0

        if cuda_available:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        return {
            "status": "healthy" if cuda_available else "degraded",
            "model_loaded": self._model is not None,
            "model_name": self._model_name,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }


if __name__ == "__main__":
    engine = ParakeetEngine()
    engine.run()
