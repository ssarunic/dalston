"""NVIDIA Parakeet ONNX transcription engine (batch mode).

Uses ONNX Runtime via the onnx-asr library for fast, lightweight inference
of Parakeet CTC, TDT, and RNNT models without the full NeMo toolkit. Produces
native word-level timestamps without requiring a separate alignment stage.

Delegates inference to OnnxInference (shared with the RT engine).

Advantages over the NeMo engine_id:
  - ~12x smaller container image (~1GB vs ~12GB)
  - ~3x faster cold start (no NeMo import overhead)
  - Better CPU performance (ONNX Runtime optimizations + INT8 quantization)
  - No PyTorch dependency for CPU inference

Limitations vs NeMo engine:
  - No vocabulary boosting: ONNX Runtime does not expose decoding graph
    manipulation APIs needed for GPU-PB phrase boosting. If vocabulary
    boosting is required, use the NeMo engine with the same Parakeet models.

Supported models:
  - parakeet-onnx-ctc-0.6b: Fastest inference, 600M params
  - parakeet-onnx-ctc-1.1b: Higher accuracy, 1.1B params
  - parakeet-onnx-tdt-0.6b-v2: TDT decoder, 600M params, English-only
  - parakeet-onnx-tdt-0.6b-v3: TDT decoder, 600M params, 25 languages, punctuation + capitalization
  - parakeet-onnx-rnnt-0.6b: RNNT decoder, 600M params, English-only

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID for registration (default: "onnx")
    DALSTON_DEFAULT_MODEL_ID: Default ONNX model ID (default: "parakeet-onnx-ctc-0.6b")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cpu.
    DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
"""

import os
from typing import Any

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineCapabilities,
    EngineInput,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.inference.onnx_inference import OnnxInference

# Decoder type extracted from model ID for alignment method reporting
_DECODER_TYPES = {"ctc", "tdt", "rnnt"}

# Accepted aliases to OnnxModelManager IDs.
_MODEL_ID_ALIASES = {
    # Canonical ONNX runtime IDs
    "parakeet-onnx-ctc-0.6b": "parakeet-onnx-ctc-0.6b",
    "parakeet-onnx-ctc-1.1b": "parakeet-onnx-ctc-1.1b",
    "parakeet-onnx-tdt-0.6b-v2": "parakeet-onnx-tdt-0.6b-v2",
    "parakeet-onnx-tdt-0.6b-v3": "parakeet-onnx-tdt-0.6b-v3",
    "parakeet-onnx-rnnt-0.6b": "parakeet-onnx-rnnt-0.6b",
    # Legacy NVIDIA NeMo repo IDs
    "nvidia/parakeet-ctc-0.6b": "parakeet-onnx-ctc-0.6b",
    "nvidia/parakeet-ctc-1.1b": "parakeet-onnx-ctc-1.1b",
    "nvidia/parakeet-tdt-0.6b-v2": "parakeet-onnx-tdt-0.6b-v2",
    "nvidia/parakeet-tdt-0.6b-v3": "parakeet-onnx-tdt-0.6b-v3",
    "nvidia/parakeet-rnnt-0.6b": "parakeet-onnx-rnnt-0.6b",
    # ONNX model repositories published for onnx-asr
    "istupakov/parakeet-ctc-0.6b-onnx": "parakeet-onnx-ctc-0.6b",
    "istupakov/parakeet-tdt-0.6b-v2-onnx": "parakeet-onnx-tdt-0.6b-v2",
    "istupakov/parakeet-tdt-0.6b-v3-onnx": "parakeet-onnx-tdt-0.6b-v3",
    "istupakov/parakeet-rnnt-0.6b-onnx": "parakeet-onnx-rnnt-0.6b",
}


class OnnxBatchEngine(BaseBatchTranscribeEngine):
    """ONNX Runtime transcription engine for batch processing.

    Delegates inference to OnnxInference, which is shared with the RT
    ONNX engine. The batch adapter handles file path input and output
    formatting to Transcript.

    Supports both GPU (CUDA/TensorRT) and CPU inference. CPU inference with
    INT8 quantization achieves competitive performance for batch processing.
    """

    # Curated model set advertised at registration time.
    # The underlying OnnxModelManager accepts any onnx-asr compatible ID.
    CURATED_MODELS = {
        "parakeet-onnx-ctc-0.6b",
        "parakeet-onnx-ctc-1.1b",
        "parakeet-onnx-tdt-0.6b-v2",
        "parakeet-onnx-tdt-0.6b-v3",
        "parakeet-onnx-rnnt-0.6b",
    }

    DEFAULT_MODEL_ID = "parakeet-onnx-ctc-0.6b"

    def __init__(self, core: OnnxInference | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared OnnxInference. If provided, the engine
                  uses it instead of creating its own.
        """
        super().__init__()
        self._core = core if core is not None else OnnxInference.from_env()

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "onnx")

        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            default_model=self._default_model_id,
            device=self._core.device,
            quantization=self._core.quantization,
            shared_core=core is not None,
        )

    def _normalize_model_id(self, loaded_model_id: str) -> str:
        """Normalize accepted aliases to OnnxModelManager IDs."""
        return _MODEL_ID_ALIASES.get(loaded_model_id, loaded_model_id)

    def transcribe_audio(
        self, engine_input: EngineInput, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio using Parakeet via ONNX Runtime and shared core.

        Args:
            engine_input: Task input with audio file path and config
            ctx: Batch task context

        Returns:
            Transcript with text, segments, and words
        """
        audio_path = engine_input.audio_path
        params = engine_input.get_transcribe_params()
        channel = params.channel

        loaded_model_id = params.loaded_model_id or self._default_model_id
        model_id = self._normalize_model_id(loaded_model_id)
        decoder_type = self._get_decoder_type(loaded_model_id)
        alignment_method = self._alignment_method_for(decoder_type)

        self.logger.info("transcribing", audio_path=str(audio_path))

        # Delegate to shared core
        core_result = self._core.transcribe(str(audio_path), model_id)

        # Convert core result to Transcript
        segments: list[TranscriptSegment] = []
        word_count = 0

        for core_seg in core_result.segments:
            seg_words: list[TranscriptWord] = []
            for w in core_seg.words:
                seg_words.append(
                    self.build_word(
                        text=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence,
                        alignment_method=alignment_method,
                    )
                )
                word_count += 1

            segments.append(
                self.build_segment(
                    start=core_seg.start,
                    end=core_seg.end,
                    text=core_seg.text,
                    words=seg_words if seg_words else None,
                    decoder_type=decoder_type,
                )
            )

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            word_count=word_count,
            char_count=len(core_result.text),
        )

        language = params.language or "en"
        return self.build_transcript(
            text=core_result.text,
            segments=segments,
            language=language if language != "auto" else "en",
            engine_id=self._engine_id,
            language_confidence=1.0 if language != "auto" else 0.5,
            alignment_method=alignment_method,
            channel=channel,
        )

    @staticmethod
    def _get_decoder_type(loaded_model_id: str) -> str:
        """Extract decoder type (ctc, tdt, rnnt) from a model ID."""
        model_name = loaded_model_id.split("/")[-1]
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
            "engine_id": self._engine_id,
            "device": self._core.device,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "quantization": self._core.quantization,
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return ONNX engine capabilities."""
        return EngineCapabilities(
            engine_id=self._engine_id,
            version="1.2.0",
            stages=["transcribe"],
            supports_word_timestamps=True,
            supports_native_streaming=False,
            model_variants=sorted(self.CURATED_MODELS),
            gpu_required=False,
            gpu_vram_mb=2000,
            supports_cpu=True,
            min_ram_gb=4,
            rtf_gpu=0.03,
            rtf_cpu=0.15,
        )


if __name__ == "__main__":
    engine = OnnxBatchEngine()
    engine.run()
