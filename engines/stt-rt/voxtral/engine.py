"""Real-time Voxtral transcription engine backed by vLLM.

This engine runs Voxtral Realtime models via vLLM and keeps the existing
Dalston realtime transcribe contract.

Notes:
- vLLM audio generation primarily returns text-only output.
- If timestamp tokens are present in the model output (e.g. <|0.00|>word),
  they are parsed into word timestamps.
- If word timestamps are requested but not present, output falls back to
  segment-level timing with a warning.
"""

from __future__ import annotations

import gc
import os
import re
from typing import Any

import numpy as np
import structlog
import torch

from dalston.common.pipeline_types import AlignmentMethod, TranscribeInput, Transcript
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine
from dalston.vllm_asr.adapters import ADAPTER_REGISTRY
from dalston.vllm_asr.inference import transcribe_audio_array

logger = structlog.get_logger()


class VoxtralRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription using Voxtral models on vLLM."""

    MODEL_VARIANT_MAP = {
        "mini-4b": "mistralai/Voxtral-Mini-4B-Realtime-2602",
    }
    DEFAULT_MODEL_VARIANT = "mini-4b"

    SUPPORTED_LANGUAGES = [
        "en",
        "zh",
        "hi",
        "es",
        "ar",
        "fr",
        "pt",
        "ru",
        "de",
        "ja",
        "ko",
        "it",
        "nl",
    ]

    _TIMESTAMP_PATTERN = re.compile(r"<\|(\d+\.\d+)\|>")

    def __init__(self) -> None:
        super().__init__()

        self._llm = None
        self._loaded_model_id: str | None = None
        self._runtime = os.environ.get("DALSTON_RUNTIME", "vllm-asr")

        self._gpu_memory_utilization = float(
            os.environ.get("DALSTON_VLLM_GPU_MEMORY_UTILIZATION", "0.9")
        )
        self._max_model_len = int(os.environ.get("DALSTON_VLLM_MAX_MODEL_LEN", "4096"))

        model_variant = os.environ.get(
            "DALSTON_MODEL_VARIANT", self.DEFAULT_MODEL_VARIANT
        )
        if model_variant not in self.MODEL_VARIANT_MAP:
            logger.warning(
                "unknown_model_variant",
                requested=model_variant,
                using=self.DEFAULT_MODEL_VARIANT,
            )
            model_variant = self.DEFAULT_MODEL_VARIANT

        self._model_variant = model_variant
        self._default_model_id = self.MODEL_VARIANT_MAP[model_variant]

        if not torch.cuda.is_available():
            raise RuntimeError(
                "Voxtral realtime on vLLM requires CUDA. "
                "No GPU detected on this system."
            )

        self._device = "cuda"

        logger.info(
            "voxtral_rt_engine_init",
            runtime=self._runtime,
            default_model_id=self._default_model_id,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            cuda_device_count=torch.cuda.device_count(),
        )

    def _resolve_runtime_model_id(self, requested: str | None) -> str:
        """Resolve user/runtime model identifiers to a HF model ID."""
        if not requested:
            return self._default_model_id

        if requested in self.MODEL_VARIANT_MAP:
            return self.MODEL_VARIANT_MAP[requested]

        if requested.startswith("voxtral-"):
            variant = requested.removeprefix("voxtral-")
            if variant in self.MODEL_VARIANT_MAP:
                return self.MODEL_VARIANT_MAP[variant]

        return requested

    def _unload_model(self) -> None:
        if self._llm is None:
            return

        del self._llm
        self._llm = None
        self._loaded_model_id = None

        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        gc.collect()

    def _ensure_model_loaded(self, model_id: str) -> None:
        """Ensure the requested model is loaded in vLLM."""
        if model_id == self._loaded_model_id and self._llm is not None:
            return

        if model_id not in ADAPTER_REGISTRY:
            raise ValueError(
                f"No adapter for model: {model_id}. "
                f"Supported models: {sorted(ADAPTER_REGISTRY.keys())}"
            )

        if self._llm is not None:
            logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=model_id,
            )
            self._unload_model()

        logger.info("loading_vllm_model", model_id=model_id)
        try:
            from vllm import LLM
        except ImportError as e:
            raise RuntimeError(
                "vLLM not installed. Install with: pip install 'vllm[audio]>=0.6.0'"
            ) from e

        self._llm = LLM(
            model=model_id,
            trust_remote_code=True,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            limit_mm_per_prompt={"audio": 1},
        )
        self._loaded_model_id = model_id

        logger.info("model_loaded", model_id=model_id)

    def load_models(self) -> None:
        """Load default model on startup."""
        self._ensure_model_loaded(self._default_model_id)

    def _parse_streaming_output(self, text: str) -> tuple[str, list]:
        """Parse optional timestamp token output into clean text + words."""
        # No timestamp markers: treat as plain text output.
        if not self._TIMESTAMP_PATTERN.search(text):
            return text.strip(), []

        clean_text = self._TIMESTAMP_PATTERN.sub("", text).strip()

        words = []
        parts = re.split(self._TIMESTAMP_PATTERN.pattern, text)

        current_time = 0.0
        for i, part in enumerate(parts):
            if not part or not part.strip():
                continue

            try:
                current_time = float(part)
                continue
            except ValueError:
                pass

            word_text = part.strip()
            if not word_text:
                continue

            next_time = current_time + 0.08
            for j in range(i + 1, len(parts)):
                try:
                    next_time = float(parts[j])
                    break
                except ValueError:
                    continue

            words.append(
                self.build_word(
                    text=word_text,
                    start=round(current_time, 3),
                    end=round(next_time, 3),
                    confidence=0.95,
                    alignment_method=AlignmentMethod.ATTENTION,
                )
            )

        # Timestamp tokens often omit spaces between words in raw output.
        if words:
            clean_text = " ".join(word.text for word in words).strip()

        return clean_text, words

    def transcribe_v1(self, audio: np.ndarray, params: TranscribeInput) -> Transcript:
        """Transcribe one audio window using vLLM."""
        model_id = self._resolve_runtime_model_id(params.runtime_model_id)
        self._ensure_model_loaded(model_id)

        language = params.language
        if language == "" or language == "auto":
            language = None

        warnings: list[str] = []
        if params.vocabulary:
            logger.debug(
                "vocabulary_not_supported",
                terms_count=len(params.vocabulary),
            )
            warnings.append(
                f"Vocabulary boosting ({len(params.vocabulary)} terms) is not supported by Voxtral realtime"
            )

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        raw_text, adapter_transcript = transcribe_audio_array(
            llm=self._llm,
            runtime_model_id=model_id,
            audio=audio,
            language=language,
            sample_rate=16000,
        )

        clean_text, parsed_words = self._parse_streaming_output(raw_text)
        text = clean_text or adapter_transcript.text

        requested_language = params.language or "auto"
        detected_language = requested_language if requested_language != "auto" else "en"
        if detected_language not in self.SUPPORTED_LANGUAGES:
            detected_language = "en"

        include_words = bool(params.word_timestamps and parsed_words)
        if params.word_timestamps and not parsed_words:
            warnings.append(
                "Word timestamps requested but model output did not include timestamp tokens"
            )

        words = parsed_words if include_words else None
        seg_start = words[0].start if words else 0.0
        seg_end = words[-1].end if words else 0.0

        segments = [
            self.build_segment(
                start=seg_start,
                end=seg_end,
                text=text,
                words=words,
            )
        ]

        transcript = self.build_transcript(
            text=text,
            segments=segments,
            language=detected_language,
            runtime=self._runtime,
            language_confidence=0.95,
            alignment_method=(
                AlignmentMethod.ATTENTION if include_words else AlignmentMethod.UNKNOWN
            ),
            warnings=warnings,
        )

        transcript.channel = params.channel
        return transcript

    def supports_streaming(self) -> bool:
        """Enable periodic partial transcripts during speech."""
        return True

    def get_models(self) -> list[str]:
        """Return external model identifiers accepted by this engine."""
        return [f"voxtral-{self._model_variant}"]

    def get_languages(self) -> list[str]:
        return self.SUPPORTED_LANGUAGES

    def get_runtime(self) -> str:
        return self._runtime

    def get_gpu_memory_usage(self) -> str:
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            return f"{used:.1f}GB"
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        base_health = super().health_check()

        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0.0
        cuda_memory_total = 0.0

        if cuda_available and cuda_device_count > 0:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        return {
            **base_health,
            "model_loaded": self._llm is not None,
            "loaded_model_id": self._loaded_model_id,
            "runtime": self._runtime,
            "device": self._device,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }

    def shutdown(self) -> None:
        logger.info("voxtral_rt_shutdown")
        self._unload_model()
        super().shutdown()


if __name__ == "__main__":
    import asyncio

    engine = VoxtralRealtimeEngine()
    asyncio.run(engine.run())
