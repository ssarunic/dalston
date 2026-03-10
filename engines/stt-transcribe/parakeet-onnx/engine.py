"""NVIDIA Parakeet ONNX transcription engine.

Uses ONNX Runtime via the onnx-asr library for fast, lightweight inference
of Parakeet CTC, TDT, and RNNT models without the full NeMo toolkit. Produces
native word-level timestamps without requiring a separate alignment stage.

Delegates inference to ParakeetOnnxCore (shared with the RT engine).

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

import os
from typing import Any

from dalston.engine_sdk import (
    AlignmentMethod,
    BatchTaskContext,
    Engine,
    EngineCapabilities,
    EngineInput,
    EngineOutput,
    Segment,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.cores.parakeet_onnx_core import ParakeetOnnxCore

# Decoder type extracted from model ID for alignment method reporting
_DECODER_TYPES = {"ctc", "tdt", "rnnt"}

# NGC model ID to NeMoOnnxModelManager ID mapping
_NGC_TO_MANAGER_ID = {
    "nvidia/parakeet-ctc-0.6b": "parakeet-onnx-ctc-0.6b",
    "nvidia/parakeet-ctc-1.1b": "parakeet-onnx-ctc-1.1b",
    "nvidia/parakeet-tdt-0.6b-v2": "parakeet-onnx-tdt-0.6b-v2",
    "nvidia/parakeet-tdt-0.6b-v3": "parakeet-onnx-tdt-0.6b-v3",
    "nvidia/parakeet-rnnt-0.6b": "parakeet-onnx-rnnt-0.6b",
}


class ParakeetOnnxEngine(Engine):
    """NVIDIA Parakeet transcription engine using ONNX Runtime.

    Delegates inference to ParakeetOnnxCore, which is shared with the RT
    ONNX engine. The batch adapter handles file path input and output
    formatting to TranscribeOutput.

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

    def __init__(self, core: ParakeetOnnxCore | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared ParakeetOnnxCore. If provided, the engine
                  uses it instead of creating its own.
        """
        super().__init__()
        self._core = core if core is not None else ParakeetOnnxCore.from_env()

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._runtime = os.environ.get("DALSTON_RUNTIME", "nemo-onnx")

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            default_model=self._default_model_id,
            device=self._core.device,
            quantization=self._core.quantization,
            shared_core=core is not None,
        )

    def _normalize_model_id(self, runtime_model_id: str) -> str:
        """Normalize NGC model IDs to NeMoOnnxModelManager format.

        Args:
            runtime_model_id: Model identifier, possibly in NGC format

        Returns:
            Normalized model ID for NeMoOnnxModelManager
        """
        if runtime_model_id in _NGC_TO_MANAGER_ID:
            return _NGC_TO_MANAGER_ID[runtime_model_id]
        return runtime_model_id

    def process(self, engine_input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        """Transcribe audio using Parakeet via ONNX Runtime and shared core.

        Args:
            engine_input: Task input with audio file path and config

        Returns:
            EngineOutput with TranscribeOutput containing text, segments, and words
        """
        audio_path = engine_input.audio_path
        config = engine_input.config
        channel = config.get("channel")

        runtime_model_id = config.get("runtime_model_id", self._default_model_id)
        model_id = self._normalize_model_id(runtime_model_id)
        decoder_type = self._get_decoder_type(runtime_model_id)
        alignment_method = self._alignment_method_for(decoder_type)

        self.logger.info("transcribing", audio_path=str(audio_path))

        # Delegate to shared core
        core_result = self._core.transcribe(str(audio_path), model_id)

        # Convert core result to batch output format
        segments: list[Segment] = []
        all_words: list[Word] = []

        for core_seg in core_result.segments:
            seg_words: list[Word] = []
            for w in core_seg.words:
                word = Word(
                    text=w.word,
                    start=w.start,
                    end=w.end,
                    confidence=w.confidence,
                    alignment_method=alignment_method,
                )
                seg_words.append(word)
                all_words.append(word)

            segments.append(
                Segment(
                    start=core_seg.start,
                    end=core_seg.end,
                    text=core_seg.text,
                    words=seg_words if seg_words else None,
                )
            )

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            word_count=len(all_words),
            char_count=len(core_result.text),
        )

        has_word_timestamps = any(seg.words for seg in segments)
        timestamp_granularity_actual = (
            TimestampGranularity.WORD
            if has_word_timestamps
            else TimestampGranularity.SEGMENT
        )

        output = TranscribeOutput(
            text=core_result.text,
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

        return EngineOutput(data=output)

    @staticmethod
    def _get_decoder_type(runtime_model_id: str) -> str:
        """Extract decoder type (ctc, tdt, rnnt) from a model ID."""
        model_name = runtime_model_id.split("/")[-1]
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

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        model_stats = self._core.get_stats()
        return {
            "status": "healthy",
            "runtime": self._runtime,
            "device": self._core.device,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "quantization": self._core.quantization,
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
