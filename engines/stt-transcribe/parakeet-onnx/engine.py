"""NVIDIA Parakeet ONNX transcription engine.

Uses ONNX Runtime via the onnx-asr library for fast, lightweight inference
of Parakeet CTC, TDT, and RNNT models without the full NeMo toolkit. Produces
native word-level timestamps without requiring a separate alignment stage.

Advantages over the NeMo runtime:
  - ~12x smaller container image (~1GB vs ~12GB)
  - ~3x faster cold start (no NeMo import overhead)
  - Better CPU performance (ONNX Runtime optimizations + INT8 quantization)
  - No PyTorch dependency for CPU inference

Supported models:
  - nvidia/parakeet-ctc-0.6b: Fastest inference, 600M params
  - nvidia/parakeet-ctc-1.1b: Higher accuracy, 1.1B params
  - nvidia/parakeet-tdt-0.6b-v2: TDT decoder, 600M params, English-only
  - nvidia/parakeet-tdt-0.6b-v3: TDT decoder, 600M params, punctuation + capitalization
  - nvidia/parakeet-rnnt-0.6b: RNNT decoder, 600M params, English-only

Environment variables:
    DALSTON_RUNTIME: Runtime engine ID for registration (default: "nemo-onnx")
    DALSTON_DEFAULT_MODEL_ID: Default ONNX model ID (default: "nvidia/parakeet-ctc-0.6b")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cpu.
    DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
"""

import gc
import os
from typing import Any

from dalston.engine_sdk import (
    AlignmentMethod,
    BatchTaskContext,
    Engine,
    EngineCapabilities,
    Segment,
    TaskInput,
    TaskOutput,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)

# onnx-asr model name mapping: Dalston runtime_model_id -> onnx-asr model name
_ONNX_ASR_MODEL_MAP = {
    "nvidia/parakeet-ctc-0.6b": "nemo-parakeet-ctc-0.6b",
    "nvidia/parakeet-ctc-1.1b": "nemo-parakeet-ctc-1.1b",
    "nvidia/parakeet-tdt-0.6b-v2": "nemo-parakeet-tdt-0.6b-v2",
    "nvidia/parakeet-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3",
    "nvidia/parakeet-rnnt-0.6b": "nemo-parakeet-rnnt-0.6b",
}

# Decoder type extracted from model ID for alignment method reporting
_DECODER_TYPES = {"ctc", "tdt", "rnnt"}


class ParakeetOnnxEngine(Engine):
    """NVIDIA Parakeet transcription engine using ONNX Runtime.

    Uses the onnx-asr library for efficient inference of Parakeet CTC, TDT,
    and RNNT models. Automatically handles audio preprocessing, ONNX inference,
    and decoding with word-level timestamp extraction.

    Supports both GPU (CUDA/TensorRT) and CPU inference. CPU inference with
    INT8 quantization achieves competitive performance for batch processing.
    """

    SUPPORTED_MODELS = {
        "nvidia/parakeet-ctc-0.6b",
        "nvidia/parakeet-ctc-1.1b",
        "nvidia/parakeet-tdt-0.6b-v2",
        "nvidia/parakeet-tdt-0.6b-v3",
        "nvidia/parakeet-rnnt-0.6b",
    }

    DEFAULT_MODEL_ID = "nvidia/parakeet-ctc-0.6b"

    def __init__(self) -> None:
        super().__init__()
        self._model: Any = None
        self._loaded_model_id: str | None = None

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._runtime = os.environ.get("DALSTON_RUNTIME", "nemo-onnx")

        # Quantization: "none" or "int8"
        self._quantization = os.environ.get("DALSTON_QUANTIZATION", "none").lower()
        if self._quantization not in ("none", "int8"):
            raise ValueError(
                f"Unknown quantization: {self._quantization}. Use 'none' or 'int8'."
            )

        # Determine device and ONNX execution providers
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        self._providers: list[str | tuple[str, dict]] = []

        if requested_device == "cuda":
            try:
                import onnxruntime as ort

                available = ort.get_available_providers()
                if "CUDAExecutionProvider" in available:
                    self._providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                    self._device = "cuda"
                else:
                    raise RuntimeError(
                        "DALSTON_DEVICE=cuda requested but CUDAExecutionProvider "
                        "is not available in ONNX Runtime."
                    )
            except ImportError as e:
                raise RuntimeError(
                    "onnxruntime not installed. Install with: pip install onnxruntime-gpu"
                ) from e
        elif requested_device in ("", "auto", "cpu"):
            self._device = "cpu"
            self._providers = ["CPUExecutionProvider"]
            if requested_device in ("", "auto"):
                # Try to use CUDA if available
                try:
                    import onnxruntime as ort

                    available = ort.get_available_providers()
                    if "CUDAExecutionProvider" in available:
                        self._providers = [
                            "CUDAExecutionProvider",
                            "CPUExecutionProvider",
                        ]
                        self._device = "cuda"
                except ImportError:
                    pass
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            default_model=self._default_model_id,
            device=self._device,
            quantization=self._quantization,
            providers=[p if isinstance(p, str) else p[0] for p in self._providers],
        )

    def _ensure_model_loaded(self, runtime_model_id: str) -> None:
        """Ensure the requested model is loaded, swapping if necessary.

        Args:
            runtime_model_id: Model identifier (e.g., "nvidia/parakeet-ctc-0.6b")

        Raises:
            ValueError: If the runtime_model_id is not supported
            RuntimeError: If onnx-asr is not installed
        """
        if runtime_model_id == self._loaded_model_id:
            return

        if runtime_model_id not in self.SUPPORTED_MODELS:
            raise ValueError(
                f"Unknown model: {runtime_model_id}. "
                f"Supported models: {sorted(self.SUPPORTED_MODELS)}"
            )

        onnx_asr_name = _ONNX_ASR_MODEL_MAP.get(runtime_model_id)
        if onnx_asr_name is None:
            raise ValueError(f"No onnx-asr mapping for model: {runtime_model_id}")

        # Unload current model if one is loaded
        if self._model is not None:
            self.logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=runtime_model_id,
            )
            self._set_runtime_state(status="unloading")
            del self._model
            self._model = None
            self._loaded_model_id = None
            gc.collect()

        # Load the requested model
        self._set_runtime_state(status="loading")
        self.logger.info(
            "loading_parakeet_onnx_model",
            runtime_model_id=runtime_model_id,
            onnx_asr_name=onnx_asr_name,
            quantization=self._quantization,
        )

        try:
            import onnx_asr
        except ImportError as e:
            self._set_runtime_state(loaded_model=None, status="error")
            raise RuntimeError(
                "onnx-asr not installed. Install with: pip install onnx-asr[cpu,hub]"
            ) from e

        # Load model via onnx-asr with optional quantization and providers
        quantization = self._quantization if self._quantization != "none" else None
        kwargs: dict[str, Any] = {}
        if self._providers:
            kwargs["providers"] = self._providers

        self._model = onnx_asr.load_model(
            onnx_asr_name,
            quantization=quantization,
            **kwargs,
        )

        # Enable word-level timestamps
        self._model = self._model.with_timestamps()

        self._loaded_model_id = runtime_model_id
        self._set_runtime_state(loaded_model=runtime_model_id, status="idle")
        self.logger.info(
            "model_loaded_successfully",
            runtime_model_id=runtime_model_id,
            device=self._device,
        )

    def process(self, input: TaskInput, ctx: BatchTaskContext) -> TaskOutput:
        """Transcribe audio using Parakeet CTC via ONNX Runtime.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and words
        """
        audio_path = input.audio_path
        config = input.config
        channel = config.get("channel")

        runtime_model_id = config.get("runtime_model_id", self._default_model_id)
        self._ensure_model_loaded(runtime_model_id)

        # Determine decoder type from model ID for alignment method
        decoder_type = self._get_decoder_type(runtime_model_id)

        self.logger.info("transcribing", audio_path=str(audio_path))

        # Run transcription via onnx-asr (handles preprocessing + inference + decoding)
        result = self._model.recognize(str(audio_path))

        # Parse the onnx-asr result into Dalston output format
        alignment_method = self._alignment_method_for(decoder_type)
        text, segments, all_words = self._parse_result(result, alignment_method)

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            word_count=len(all_words),
            char_count=len(text),
        )

        has_word_timestamps = any(seg.words for seg in segments)
        timestamp_granularity_actual = (
            TimestampGranularity.WORD
            if has_word_timestamps
            else TimestampGranularity.SEGMENT
        )

        output = TranscribeOutput(
            text=text,
            segments=segments,
            language="en",
            language_confidence=1.0,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=timestamp_granularity_actual,
            alignment_method=alignment_method,
            channel=channel,
            runtime=self._runtime,
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

    @staticmethod
    def _get_decoder_type(runtime_model_id: str) -> str:
        """Extract decoder type (ctc, tdt, rnnt) from a model ID.

        Args:
            runtime_model_id: Model ID like "nvidia/parakeet-ctc-0.6b"

        Returns:
            Decoder type string ("ctc", "tdt", or "rnnt")
        """
        model_name = runtime_model_id.split("/")[-1]  # "parakeet-ctc-0.6b"
        parts = model_name.split("-")
        for part in parts:
            if part in _DECODER_TYPES:
                return part
        return "ctc"

    @staticmethod
    def _alignment_method_for(decoder_type: str) -> AlignmentMethod:
        """Map decoder type to AlignmentMethod enum."""
        if decoder_type == "tdt":
            return AlignmentMethod.TDT
        if decoder_type == "rnnt":
            return AlignmentMethod.RNNT
        return AlignmentMethod.CTC

    def _parse_result(
        self, result: Any, alignment_method: AlignmentMethod = AlignmentMethod.CTC
    ) -> tuple[str, list[Segment], list[Word]]:
        """Parse onnx-asr recognition result into Dalston types.

        The onnx-asr library returns a TimestampedResult with:
          - text: the full transcription
          - tokens: list of token strings (subwords)
          - timestamps: list of floats (start time for each token)

        This method groups tokens into words and creates segments based on
        sentence boundaries (punctuation).

        Args:
            result: onnx-asr recognition result (TimestampedResult or string)
            alignment_method: Alignment method to tag words with

        Returns:
            Tuple of (full_text, segments, all_words)
        """
        # onnx-asr with_timestamps() returns a TimestampedResult with
        # .text, .tokens (list[str]), and .timestamps (list[float])
        if hasattr(result, "text"):
            text = str(result.text).strip()
        else:
            text = str(result).strip()

        if not text:
            return "", [], []

        all_words: list[Word] = []

        # Extract word timestamps from onnx-asr result
        # onnx-asr returns tokens as strings and timestamps as a separate list
        if hasattr(result, "tokens") and hasattr(result, "timestamps"):
            tokens = result.tokens
            timestamps = result.timestamps
            if tokens and timestamps and len(tokens) == len(timestamps):
                all_words = self._tokens_to_words(tokens, timestamps, alignment_method)

        # Create segments from words, splitting on sentence boundaries
        segments = self._words_to_segments(all_words, text)

        return text, segments, all_words

    @staticmethod
    def _is_sentence_ending(word_text: str) -> bool:
        """Check if a word ends with sentence-ending punctuation."""
        return word_text.rstrip().endswith((".", "?", "!", "。", "？", "！"))

    def _words_to_segments(
        self, all_words: list[Word], full_text: str
    ) -> list[Segment]:
        """Group words into segments based on sentence boundaries.

        Splits on sentence-ending punctuation (. ? !) to create natural
        segment breaks that correspond to sentences.

        Args:
            all_words: List of all words with timestamps
            full_text: Full transcription text (for fallback)

        Returns:
            List of Segment objects
        """
        if not all_words:
            if full_text:
                return [
                    Segment(
                        start=0.0,
                        end=0.0,
                        text=full_text,
                    )
                ]
            return []

        segments: list[Segment] = []
        current_words: list[Word] = []

        for word in all_words:
            current_words.append(word)

            # Check if this word ends a sentence
            if self._is_sentence_ending(word.text):
                # Create segment from accumulated words
                seg_text = " ".join(w.text for w in current_words)
                segments.append(
                    Segment(
                        start=current_words[0].start,
                        end=current_words[-1].end,
                        text=seg_text,
                        words=current_words.copy(),
                    )
                )
                current_words = []

        # Handle remaining words (no sentence-ending punctuation at end)
        if current_words:
            seg_text = " ".join(w.text for w in current_words)
            segments.append(
                Segment(
                    start=current_words[0].start,
                    end=current_words[-1].end,
                    text=seg_text,
                    words=current_words,
                )
            )

        return segments

    @staticmethod
    def _is_word_boundary(token_text: str) -> bool:
        """Check if a token marks a word boundary.

        SentencePiece can use either:
          - \\u2581 (LOWER ONE EIGHTH BLOCK, ▁) - the standard marker
          - Regular space ' ' - used by some tokenizers including onnx-asr

        Args:
            token_text: The token string to check

        Returns:
            True if this token starts a new word
        """
        return token_text.startswith("\u2581") or token_text.startswith(" ")

    def _tokens_to_words(
        self,
        tokens: list[str],
        timestamps: list[float],
        alignment_method: AlignmentMethod = AlignmentMethod.CTC,
    ) -> list[Word]:
        """Group subword tokens into words using SentencePiece boundaries.

        SentencePiece marks word boundaries with either \\u2581 or space prefix.
        Tokens starting with these characters begin a new word.

        Args:
            tokens: List of token strings from onnx-asr
            timestamps: List of start times (one per token)
            alignment_method: Alignment method to tag words with

        Returns:
            List of Word objects grouped at word level
        """
        if not tokens or not timestamps:
            return []

        words: list[Word] = []
        current_text_parts: list[str] = []
        current_start: float | None = None
        current_end: float = 0.0

        for i, token_text in enumerate(tokens):
            token_start = timestamps[i]
            # End time is the start of next token, or same as start for last token
            token_end = timestamps[i + 1] if i + 1 < len(timestamps) else token_start

            # Check for word boundary
            if self._is_word_boundary(token_text) and current_text_parts:
                # Flush current word
                word_text = (
                    "".join(current_text_parts)
                    .replace("\u2581", "")
                    .replace(" ", " ")  # Normalize spaces
                    .strip()
                )
                if word_text and current_start is not None:
                    words.append(
                        Word(
                            text=word_text,
                            start=round(current_start, 3),
                            end=round(current_end, 3),
                            confidence=None,
                            alignment_method=alignment_method,
                        )
                    )
                current_text_parts = [token_text]
                current_start = token_start
                current_end = token_end
            else:
                if current_start is None:
                    current_start = token_start
                current_text_parts.append(token_text)
                current_end = token_end

        # Flush last word
        if current_text_parts:
            word_text = (
                "".join(current_text_parts)
                .replace("\u2581", "")
                .replace(" ", " ")
                .strip()
            )
            if word_text and current_start is not None:
                words.append(
                    Word(
                        text=word_text,
                        start=round(current_start, 3),
                        end=round(current_end, 3),
                        confidence=None,
                        alignment_method=alignment_method,
                    )
                )

        return words

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        return {
            "status": "healthy",
            "runtime": self._runtime,
            "device": self._device,
            "model_loaded": self._model is not None,
            "loaded_model_id": self._loaded_model_id,
            "quantization": self._quantization,
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return ONNX engine capabilities."""
        return EngineCapabilities(
            runtime=self._runtime,
            version="1.1.0",
            stages=["transcribe"],
            languages=["en"],
            supports_word_timestamps=True,
            supports_streaming=False,
            model_variants=sorted(self.SUPPORTED_MODELS),
            gpu_required=False,
            gpu_vram_mb=2000,
            supports_cpu=True,
            min_ram_gb=4,
            rtf_gpu=0.0003,
            rtf_cpu=0.15,
        )


if __name__ == "__main__":
    engine = ParakeetOnnxEngine()
    engine.run()
