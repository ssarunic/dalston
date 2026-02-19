"""Mistral Voxtral transcription engine.

Uses Voxtral models for multilingual speech-to-text transcription
with support for multiple languages and word-level timestamps.

Voxtral supports both GPU (recommended) and CPU inference via
Transformers backend.

Environment variables:
    MODEL_VARIANT: Model variant to use (mini-3b, small-24b). Defaults to mini-3b.
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
)


class VoxtralEngine(Engine):
    """Mistral Voxtral transcription engine.

    Uses Voxtral models for multilingual transcription.
    Supports 8 languages: English, Spanish, French, Portuguese,
    Hindi, German, Dutch, Italian.

    GPU inference is strongly recommended for production use.
    """

    MODEL_VARIANT_MAP = {
        "mini-3b": "mistralai/Voxtral-Mini-3B-2507",
        "small-24b": "mistralai/Voxtral-Small-24B-2507",
    }
    DEFAULT_MODEL_VARIANT = "mini-3b"

    SUPPORTED_LANGUAGES = ["en", "es", "fr", "pt", "hi", "de", "nl", "it"]

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._processor = None
        self._model_name: str | None = None

        model_variant = os.environ.get("MODEL_VARIANT", self.DEFAULT_MODEL_VARIANT)
        if model_variant not in self.MODEL_VARIANT_MAP:
            self.logger.warning(
                "unknown_model_variant",
                requested=model_variant,
                using=self.DEFAULT_MODEL_VARIANT,
            )
            model_variant = self.DEFAULT_MODEL_VARIANT
        self._model_variant = model_variant
        self._hf_model_id = self.MODEL_VARIANT_MAP[model_variant]

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
                    message="CUDA not available, falling back to CPU",
                )
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

    def _load_model(self, model_name: str) -> None:
        """Load the Voxtral model if not already loaded.

        Args:
            model_name: HuggingFace model identifier
        """
        if self._model is not None and self._model_name == model_name:
            return

        self.logger.info("loading_voxtral_model", model_name=model_name)

        try:
            from transformers import AutoProcessor, VoxtralForConditionalGeneration
        except ImportError as e:
            raise RuntimeError(
                "Transformers not installed. Install with: pip install transformers"
            ) from e

        self._processor = AutoProcessor.from_pretrained(model_name)

        torch_dtype = torch.bfloat16 if self._device == "cuda" else torch.float32

        self._model = VoxtralForConditionalGeneration.from_pretrained(
            model_name,
            torch_dtype=torch_dtype,
            device_map=self._device if self._device == "cuda" else None,
        )

        if self._device == "cpu":
            self._model = self._model.to(self._device)

        self._model.eval()
        self._model_name = model_name

        self.logger.info("model_loaded_successfully", model_name=model_name)

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Voxtral.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text and segments
        """
        audio_path = input.audio_path
        config = input.config
        channel = config.get("channel")
        language = config.get("language", "en")
        vocabulary = config.get("vocabulary")  # Terms to boost

        # Warn if vocabulary is provided - Voxtral doesn't support vocabulary boosting
        if vocabulary:
            self.logger.warning(
                "vocabulary_not_supported",
                message="Vocabulary boosting is not supported for Voxtral. Terms will be ignored.",
                terms_count=len(vocabulary),
            )

        if language not in self.SUPPORTED_LANGUAGES:
            language = "en"

        self._load_model(self._hf_model_id)

        self.logger.info("transcribing", audio_path=str(audio_path))

        # Use processor's apply_transcription_request for proper audio handling
        inputs = self._processor.apply_transcription_request(
            language=language,
            audio=str(audio_path),
            model_id=self._hf_model_id,
        )

        torch_dtype = torch.bfloat16 if self._device == "cuda" else torch.float32
        inputs = inputs.to(self._device, dtype=torch_dtype)

        with torch.inference_mode():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=4096,
                temperature=0.0,  # Deterministic for transcription
            )

        # Decode only the generated tokens (skip input tokens)
        transcription = self._processor.batch_decode(
            outputs[:, inputs.input_ids.shape[1] :],
            skip_special_tokens=True,
        )[0]

        full_text = transcription.strip()

        # Create a single segment (Voxtral batch doesn't provide word timestamps)
        segments = [
            Segment(
                start=0.0,
                end=0.0,
                text=full_text,
                words=None,
            )
        ]

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            char_count=len(full_text),
        )

        # Build warnings list
        warnings: list[str] = []
        if vocabulary:
            warnings.append(
                f"Vocabulary boosting ({len(vocabulary)} terms) not supported by Voxtral engine"
            )

        output = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=language,
            language_confidence=0.9,
            timestamp_granularity_requested=TimestampGranularity.SEGMENT,
            timestamp_granularity_actual=TimestampGranularity.SEGMENT,
            alignment_method=AlignmentMethod.UNKNOWN,
            channel=channel,
            engine_id=f"voxtral-{self._model_variant}",
            skipped=False,
            skip_reason=None,
            warnings=warnings,
        )

        return TaskOutput(data=output)

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability."""
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0
        cuda_memory_total = 0

        if cuda_available and torch.cuda.device_count() > 0:
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
        """Return Voxtral engine capabilities."""
        vram_mb = 9500 if self._model_variant == "mini-3b" else 55000

        return EngineCapabilities(
            engine_id=f"voxtral-{self._model_variant}",
            version="1.0.0",
            stages=["transcribe"],
            languages=self.SUPPORTED_LANGUAGES,
            supports_word_timestamps=False,  # Batch Voxtral doesn't have word timestamps
            supports_streaming=False,
            model_variants=[self._hf_model_id],
            gpu_required=True,
            gpu_vram_mb=vram_mb,
        )


if __name__ == "__main__":
    engine = VoxtralEngine()
    engine.run()
