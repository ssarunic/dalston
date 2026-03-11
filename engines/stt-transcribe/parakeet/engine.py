"""NVIDIA Parakeet transcription engine with runtime model swapping.

Uses NVIDIA NeMo Parakeet FastConformer models with CTC or TDT decoders
for fast English-only speech-to-text transcription with GPU acceleration.

- CTC (Connectionist Temporal Classification): Fastest inference, good accuracy
- TDT (Token-and-Duration Transducer): Best accuracy, 64% faster than RNNT

Parakeet produces native word-level timestamps, eliminating the need for
a separate alignment stage.

Long audio support: Uses local attention (rel_pos_local_attn) instead of full
attention to reduce memory from O(n^2) to O(n). This enables transcription of
audio up to 3 hours on T4/A10g GPUs.

Vocabulary boosting: Supports GPU-PB (GPU-accelerated Phrase Boosting) for
biasing recognition toward specific terms without retraining.

Phase 1 (Runtime Model Management):
    This engine now supports loading any Parakeet model variant at runtime.
    The model to load is specified via config["runtime_model_id"] in the task,
    falling back to DALSTON_DEFAULT_MODEL_ID environment variable.

Environment variables:
    DALSTON_RUNTIME: Runtime engine ID for registration (default: "nemo")
    DALSTON_DEFAULT_MODEL_ID: Default NeMo model ID (default: "nvidia/parakeet-tdt-1.1b")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
"""

import os
import tempfile
from pathlib import Path
from typing import Any

import torch

from dalston.common.pipeline_types import (
    AlignmentMethod,
    DalstonTranscriptV1,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineCapabilities,
    EngineInput,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.cores.parakeet_core import ParakeetCore


class ParakeetEngine(BaseBatchTranscribeEngine):
    """NVIDIA Parakeet transcription engine with runtime model swapping.

    Uses FastConformer encoder with CTC or TDT decoder for efficient
    English-only transcription. Automatically produces word-level
    timestamps without requiring separate alignment.

    Decoder types:
    - CTC: Fastest inference, greedy decoding, good for batch processing
    - TDT: Best accuracy, predicts token durations, recommended for quality

    Supports both GPU (CUDA) and CPU inference. GPU is strongly
    recommended for production use.

    Phase 1 (Runtime Model Management):
        This engine can load any Parakeet model variant at runtime. The model
        is specified via config["runtime_model_id"] in the task payload. If a
        different model is requested, the current model is unloaded and the
        new one is loaded (with GPU memory cleanup).
    """

    # Valid NeMo model identifiers that this runtime can load
    # Keys are the runtime_model_id values that can be passed in task config
    SUPPORTED_MODELS = {
        "nvidia/parakeet-ctc-0.6b",
        "nvidia/parakeet-ctc-1.1b",
        "nvidia/parakeet-tdt-0.6b-v3",
        "nvidia/parakeet-tdt-1.1b",
    }

    # Default model to load if none specified (multilingual + CPU-capable not applicable
    # for Parakeet since it's English-only, so use the highest quality model)
    DEFAULT_MODEL_ID = "nvidia/parakeet-tdt-1.1b"

    def __init__(self, core: ParakeetCore | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared ParakeetCore. If provided, the engine uses
                  it instead of creating its own. This is how the unified
                  runner shares a single model between batch and RT adapters.
        """
        super().__init__()
        self._core = core if core is not None else ParakeetCore.from_env()

        # Get default model from environment, with fallback to class default
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        # Get engine ID from environment for registration (runtime ID, not variant ID)
        self._runtime = os.environ.get("DALSTON_RUNTIME", "nemo")

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            default_model=self._default_model_id,
            device=self._core.device,
            shared_core=core is not None,
        )

    def _normalize_model_id(self, runtime_model_id: str) -> str:
        """Normalize NGC model IDs to NeMoModelManager format.

        Args:
            runtime_model_id: Model identifier, possibly in NGC format
                (e.g. "nvidia/parakeet-tdt-1.1b")

        Returns:
            Normalized model ID for NeMoModelManager
                (e.g. "parakeet-tdt-1.1b")
        """
        # Strip "nvidia/" prefix if present -- NeMoModelManager uses short IDs
        if "/" in runtime_model_id:
            return runtime_model_id.split("/", 1)[1]
        return runtime_model_id

    def _configure_vocabulary_boosting(
        self, model: Any, vocabulary: list[str], boosting_alpha: float = 0.5
    ) -> Path | None:
        """Configure GPU-PB vocabulary boosting for the model.

        Creates a temporary file with vocabulary terms and configures the model's
        decoding strategy to use GPU-PB (GPU-accelerated Phrase Boosting).

        Args:
            model: The acquired NeMo ASR model to configure.
            vocabulary: List of terms to boost recognition for.
            boosting_alpha: Weight of boosting during decoding (0.0-1.0).
                Higher values = stronger boosting. Default 0.5.

        Returns:
            Path to temporary vocabulary file (caller must clean up), or None if failed.

        Note:
            Parakeet models trained with capitalization (e.g., tdt-0.6b-v3) require
            both uppercase and lowercase variants. This method automatically generates
            common case variants for each term.
        """
        if not vocabulary or model is None:
            return None

        try:
            # Generate case variants for each term
            # Parakeet-tdt-0.6b-v2/v3 was trained with capitalization
            expanded_terms: set[str] = set()
            for term in vocabulary:
                expanded_terms.add(term)  # Original
                expanded_terms.add(term.lower())  # lowercase
                expanded_terms.add(term.upper())  # UPPERCASE
                expanded_terms.add(term.capitalize())  # Capitalized

            # Write vocabulary to temp file (one term per line)
            vocab_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            )
            for term in sorted(expanded_terms):
                vocab_file.write(f"{term}\n")
            vocab_file.close()
            vocab_path = Path(vocab_file.name)

            self.logger.info(
                "vocabulary_file_created",
                path=str(vocab_path),
                original_terms=len(vocabulary),
                expanded_terms=len(expanded_terms),
            )

            # Configure decoding strategy with boosting tree
            # The config path is the same for CTC and TDT models
            decoding_cfg = model.cfg.decoding
            decoding_cfg.strategy = "greedy_batch"
            decoding_cfg.greedy.boosting_tree.key_phrases_file = str(vocab_path)
            decoding_cfg.greedy.boosting_tree.context_score = 1.0
            decoding_cfg.greedy.boosting_tree.depth_scaling = 2.0
            decoding_cfg.greedy.boosting_tree_alpha = boosting_alpha

            # Apply the new decoding strategy
            model.change_decoding_strategy(decoding_cfg)

            # Extract decoder type from model for logging
            model_parts = str(getattr(model, "model_name", "")).split("-")
            decoder_type = model_parts[1] if len(model_parts) > 1 else "unknown"

            self.logger.info(
                "vocabulary_boosting_enabled",
                decoder_type=decoder_type,
                boosting_alpha=boosting_alpha,
                terms_count=len(expanded_terms),
            )

            return vocab_path

        except Exception as e:
            self.logger.warning(
                "vocabulary_boosting_failed",
                error=str(e),
                message="Falling back to standard decoding without vocabulary boosting",
            )
            return None

    @staticmethod
    def _reset_decoding_strategy(model: Any) -> None:
        """Reset decoding strategy to default (no vocabulary boosting)."""
        if model is None:
            return

        try:
            decoding_cfg = model.cfg.decoding
            decoding_cfg.strategy = "greedy_batch"
            # Clear boosting tree config
            if hasattr(decoding_cfg.greedy, "boosting_tree"):
                decoding_cfg.greedy.boosting_tree.key_phrases_file = None
                decoding_cfg.greedy.boosting_tree_alpha = 0.0

            model.change_decoding_strategy(decoding_cfg)
        except Exception:
            pass  # Best-effort reset; model will be released anyway

    def _enable_local_attention(self, model: Any) -> None:
        """Enable local attention for long audio support (up to 3 hours).

        Reduces memory from O(n^2) to O(n), essential for T4/A10g GPUs.
        """
        try:
            model.change_attention_model(
                self_attention_model="rel_pos_local_attn",
                att_context_size=[256, 256],
            )
            model.change_subsampling_conv_chunking_factor(1)
            self.logger.info(
                "local_attention_enabled",
                att_context_size=[256, 256],
                message="Long audio support enabled (up to 3 hours)",
            )
        except Exception as e:
            self.logger.warning(
                "local_attention_failed",
                error=str(e),
                message="Falling back to full attention (limited to ~24min on A100)",
            )

    def transcribe_audio(
        self, engine_input: EngineInput, ctx: BatchTaskContext
    ) -> DalstonTranscriptV1:
        """Transcribe audio using Parakeet CTC or TDT via shared ParakeetCore.

        Args:
            engine_input: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            DalstonTranscriptV1 with text, segments, and words
        """
        audio_path = engine_input.audio_path
        config = engine_input.config
        channel = config.get("channel")
        vocabulary = config.get("vocabulary")

        runtime_model_id = config.get("runtime_model_id", self._default_model_id)
        model_id = self._normalize_model_id(runtime_model_id)

        # Extract decoder type for alignment method
        model_parts = model_id.split("-")
        decoder_type = model_parts[1] if len(model_parts) > 1 else "ctc"

        if decoder_type == "ctc":
            alignment_method = AlignmentMethod.CTC
        else:
            alignment_method = AlignmentMethod.TDT

        # If vocabulary boosting is needed, acquire model directly for
        # decoding strategy modification; otherwise use core.transcribe()
        vocab_file: Path | None = None
        vocabulary_enabled = False

        if vocabulary:
            model = self._core.manager.acquire(model_id)
            try:
                self._enable_local_attention(model)
                vocab_file = self._configure_vocabulary_boosting(model, vocabulary)
                vocabulary_enabled = vocab_file is not None

                self.logger.info(
                    "transcribing",
                    audio_path=str(audio_path),
                    vocabulary_enabled=vocabulary_enabled,
                )

                core_result = self._core.transcribe_with_model(model, str(audio_path))
            finally:
                if vocab_file is not None:
                    try:
                        vocab_file.unlink(missing_ok=True)
                        self._reset_decoding_strategy(model)
                    except Exception:
                        pass
                self._core.manager.release(model_id)
        else:
            self.logger.info(
                "transcribing",
                audio_path=str(audio_path),
                vocabulary_enabled=False,
            )
            core_result = self._core.transcribe(str(audio_path), model_id)

        # Convert core result to DalstonTranscriptV1 format
        segments: list[TranscriptSegment] = []
        all_words: list[TranscriptWord] = []

        for core_seg in core_result.segments:
            seg_words: list[TranscriptWord] = []
            for w in core_seg.words:
                word = self.build_word(
                    text=w.word,
                    start=w.start,
                    end=w.end,
                    confidence=w.confidence,
                    alignment_method=alignment_method,
                )
                seg_words.append(word)
                all_words.append(word)

            segments.append(
                self.build_segment(
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

        warnings: list[str] = []
        if vocabulary and not vocabulary_enabled:
            warnings.append(
                f"Vocabulary boosting ({len(vocabulary)} terms) failed to configure - transcribed without boosting"
            )

        return self.build_transcript(
            text=core_result.text,
            segments=segments,
            language="en",
            runtime=self._runtime,
            language_confidence=1.0,
            alignment_method=alignment_method,
            channel=channel,
            warnings=warnings,
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

        model_stats = self._core.get_stats()

        return {
            "status": "healthy",
            "runtime": self._runtime,
            "device": self._core.device,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return Parakeet engine capabilities.

        Phase 1: This now returns the runtime ID (e.g., "nemo") instead of
        a variant-specific ID. The engine can load any supported Parakeet
        model variant at runtime.

        Parakeet is an English-only transcription engine with native
        word-level timestamps via CTC or TDT alignment.
        """
        # VRAM requirements: report maximum for capability planning
        # Individual model requirements are in the model catalog
        vram_mb = 6000  # Maximum (tdt-1.1b)

        return EngineCapabilities(
            runtime=self._runtime,
            version="1.0.0",
            stages=["transcribe"],
            languages=["en"],  # English only
            supports_word_timestamps=True,
            supports_streaming=False,
            model_variants=sorted(self.SUPPORTED_MODELS),
            gpu_required=True,
            gpu_vram_mb=vram_mb,
        )


if __name__ == "__main__":
    engine = ParakeetEngine()
    engine.run()
