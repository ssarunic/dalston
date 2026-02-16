"""NVIDIA Parakeet transcription engine.

Uses NVIDIA NeMo Parakeet FastConformer RNNT models for fast
English-only speech-to-text transcription with GPU acceleration.

Parakeet produces native word-level timestamps via RNNT alignment,
eliminating the need for a separate alignment stage.

Environment variables:
    DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
"""

import os
from typing import Any

import torch

from dalston.engine_sdk import (
    AlignmentMethod,
    Engine,
    EngineCapabilities,
    Segment,
    TaskInput,
    TaskOutput,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)


class ParakeetEngine(Engine):
    """NVIDIA Parakeet transcription engine.

    Uses FastConformer encoder with RNNT decoder for efficient
    English-only transcription. Automatically produces word-level
    timestamps without requiring separate alignment.

    Supports both GPU (CUDA) and CPU inference. GPU is strongly
    recommended for production use.
    """

    # Model variants (NeMo model identifiers)
    # TDT_CTC models are faster and lighter than RNNT
    MODEL_VARIANTS = {
        # TDT_CTC (lightweight, faster)
        "nvidia/parakeet-tdt_ctc-110m": "nvidia/parakeet-tdt_ctc-110m",
        "parakeet-110m": "nvidia/parakeet-tdt_ctc-110m",  # Alias
        # RNNT (more accurate, heavier)
        "nvidia/parakeet-rnnt-0.6b": "nvidia/parakeet-rnnt-0.6b",
        "parakeet-0.6b": "nvidia/parakeet-rnnt-0.6b",  # Alias
        "nvidia/parakeet-rnnt-1.1b": "nvidia/parakeet-rnnt-1.1b",
        "parakeet-1.1b": "nvidia/parakeet-rnnt-1.1b",  # Alias
    }
    DEFAULT_MODEL = "nvidia/parakeet-tdt_ctc-110m"

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._model_name: str | None = None

        # Determine device from environment or availability
        requested_device = os.environ.get("DEVICE", "").lower()
        cuda_available = torch.cuda.is_available()

        if requested_device == "cpu":
            self._device = "cpu"
            self.logger.warning(
                "using_cpu_device",
                message="Running on CPU - inference will be significantly slower",
            )
        elif requested_device == "cuda" or requested_device == "":
            if cuda_available:
                self._device = "cuda"
                self.logger.info(
                    "cuda_available", device_count=torch.cuda.device_count()
                )
            else:
                self._device = "cpu"
                self.logger.warning(
                    "cuda_not_available",
                    message="CUDA not available, falling back to CPU - inference will be slower",
                )
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

    def _load_model(self, model_name: str) -> None:
        """Load the Parakeet model if not already loaded.

        Args:
            model_name: NeMo model identifier
        """
        # Only reload if model changed
        if self._model is not None and self._model_name == model_name:
            return

        self.logger.info("loading_parakeet_model", model_name=model_name)

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

        self.logger.info("model_loaded_successfully", model_name=model_name)

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Parakeet RNNT.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and words
        """
        audio_path = input.audio_path
        config = input.config
        channel = config.get("channel")  # For per_channel mode

        # Get model configuration
        model_name = config.get("model", self.DEFAULT_MODEL)
        if model_name not in self.MODEL_VARIANTS:
            self.logger.warning(
                "unknown_model_variant",
                requested=model_name,
                using=self.DEFAULT_MODEL,
            )
            model_name = self.DEFAULT_MODEL

        # Load model
        self._load_model(model_name)

        self.logger.info("transcribing", audio_path=str(audio_path))

        # Transcribe with word-level timestamps
        # NeMo RNNT models can return word timestamps via the alignment
        # Use autocast for GPU, no-op context for CPU
        autocast_ctx = (
            torch.cuda.amp.autocast()
            if self._device == "cuda"
            else torch.inference_mode()
        )
        with autocast_ctx:
            # Use transcribe method with timestamps=True for word-level timing
            transcriptions = self._model.transcribe(
                [str(audio_path)],
                batch_size=1,
                return_hypotheses=True,
                timestamps=True,  # Enable word-level timestamps
            )

        # Process the hypothesis
        if not transcriptions:
            return TaskOutput(
                data=TranscribeOutput(
                    text="",
                    segments=[],
                    language="en",
                    language_confidence=1.0,
                    channel=channel,
                    engine_id="parakeet",
                    skipped=False,
                    warnings=[],
                )
            )

        hypothesis = transcriptions[0]

        # Extract text - handle both string and Hypothesis object
        if hasattr(hypothesis, "text"):
            full_text = hypothesis.text
        else:
            full_text = str(hypothesis)

        # Determine alignment method based on model type
        alignment_method = (
            AlignmentMethod.TDT if "tdt" in model_name.lower() else AlignmentMethod.RNNT
        )

        # Build segments with word-level timestamps
        segments: list[Segment] = []
        all_words: list[Word] = []

        # Check for timestamp dict (TDT models with timestamps=True)
        if hasattr(hypothesis, "timestamp") and isinstance(hypothesis.timestamp, dict):
            word_timestamps = hypothesis.timestamp.get("word", [])
            segment_timestamps = hypothesis.timestamp.get("segment", [])

            # Extract word-level data
            for wt in word_timestamps:
                word = Word(
                    text=wt.get("word", ""),
                    start=round(wt.get("start", 0.0), 3),
                    end=round(wt.get("end", 0.0), 3),
                    confidence=None,  # TDT doesn't provide per-word confidence
                    alignment_method=alignment_method,
                )
                all_words.append(word)

            # Use segment timestamps if available, otherwise create from words
            if segment_timestamps:
                for seg in segment_timestamps:
                    seg_start = seg.get("start", 0.0)
                    seg_end = seg.get("end", 0.0)
                    seg_text = seg.get("segment", "")
                    # Find words that fall within this segment
                    seg_words = [
                        w
                        for w in all_words
                        if w.start >= seg_start - 0.01 and w.end <= seg_end + 0.01
                    ]
                    segments.append(
                        Segment(
                            start=round(seg_start, 3),
                            end=round(seg_end, 3),
                            text=seg_text,
                            words=seg_words if seg_words else None,
                        )
                    )
            elif all_words:
                # Create a single segment with all words
                segments.append(
                    Segment(
                        start=all_words[0].start,
                        end=all_words[-1].end,
                        text=full_text.strip(),
                        words=all_words,
                    )
                )

        # Fallback: check for legacy timestep format (RNNT models)
        elif hasattr(hypothesis, "timestep") and hypothesis.timestep is not None:
            timesteps = hypothesis.timestep
            tokens = hypothesis.text.split()
            frame_shift_seconds = 0.01

            current_words: list[Word] = []
            for i, (token, frame_idx) in enumerate(
                zip(tokens, timesteps, strict=False)
            ):
                word_start = frame_idx * frame_shift_seconds
                if i + 1 < len(timesteps):
                    word_end = timesteps[i + 1] * frame_shift_seconds
                else:
                    word_end = word_start + 0.1

                word = Word(
                    text=token,
                    start=round(word_start, 3),
                    end=round(word_end, 3),
                    confidence=None,
                    alignment_method=AlignmentMethod.RNNT,
                )
                current_words.append(word)
                all_words.append(word)

            if current_words:
                segments.append(
                    Segment(
                        start=current_words[0].start,
                        end=current_words[-1].end,
                        text=full_text.strip(),
                        words=current_words,
                    )
                )
        else:
            # Fallback: create segment without word timestamps
            segments.append(
                Segment(
                    start=0.0,
                    end=0.0,
                    text=full_text.strip(),
                )
            )

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            word_count=len(all_words),
            char_count=len(full_text),
        )

        # Determine actual granularity produced
        has_word_timestamps = any(seg.words for seg in segments)
        timestamp_granularity_actual = (
            TimestampGranularity.WORD
            if has_word_timestamps
            else TimestampGranularity.SEGMENT
        )

        output = TranscribeOutput(
            text=full_text.strip(),
            segments=segments,
            language="en",  # Parakeet is English-only
            language_confidence=1.0,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=timestamp_granularity_actual,
            alignment_method=alignment_method,
            channel=channel,
            engine_id="parakeet",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

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
            "status": "healthy",
            "device": self._device,
            "model_loaded": self._model is not None,
            "model_name": self._model_name,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return Parakeet engine capabilities.

        Parakeet is an English-only transcription engine with native
        word-level timestamps via RNNT/TDT alignment.
        """
        return EngineCapabilities(
            engine_id="parakeet",
            version="1.0.0",
            stages=["transcribe"],
            languages=["en"],  # English only
            supports_word_timestamps=True,
            supports_streaming=False,
            model_variants=[
                "nvidia/parakeet-tdt_ctc-110m",
                "nvidia/parakeet-rnnt-0.6b",
                "nvidia/parakeet-rnnt-1.1b",
            ],
            gpu_required=True,
            gpu_vram_mb=4000,
        )


if __name__ == "__main__":
    engine = ParakeetEngine()
    engine.run()
