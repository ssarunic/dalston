"""NVIDIA Parakeet transcription engine with runtime model swapping.

Uses NVIDIA NeMo Parakeet FastConformer models with CTC or TDT decoders
for fast English-only speech-to-text transcription with GPU acceleration.

- CTC (Connectionist Temporal Classification): Fastest inference, good accuracy
- TDT (Token-and-Duration Transducer): Best accuracy, 64% faster than RNNT

Parakeet produces native word-level timestamps, eliminating the need for
a separate alignment stage.

Long audio support: Uses local attention (rel_pos_local_attn) instead of full
attention to reduce memory from O(n²) to O(n). This enables transcription of
audio up to 3 hours on T4/A10g GPUs.

Vocabulary boosting: Supports GPU-PB (GPU-accelerated Phrase Boosting) for
biasing recognition toward specific terms without retraining.

Phase 1 (Runtime Model Management):
    This engine now supports loading any Parakeet model variant at runtime.
    The model to load is specified via config["runtime_model_id"] in the task,
    falling back to DALSTON_DEFAULT_MODEL_ID environment variable.

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID for registration (default: "nemo")
    DALSTON_DEFAULT_MODEL_ID: Default NeMo model ID (default: "nvidia/parakeet-tdt-1.1b")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
"""

import gc
import os
import tempfile
from pathlib import Path
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

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._loaded_model_id: str | None = None

        # Get default model from environment, with fallback to class default
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        # Get engine ID from environment for registration (runtime ID, not variant ID)
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "nemo")

        # Determine device from environment or availability
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        cuda_available = torch.cuda.is_available()

        if requested_device == "cpu":
            self._device = "cpu"
            self.logger.warning(
                "using_cpu_device",
                message="Running on CPU - Parakeet inference will be significantly slower",
            )
        elif requested_device in ("", "auto", "cuda"):
            if cuda_available:
                self._device = "cuda"
                self.logger.info(
                    "cuda_available", device_count=torch.cuda.device_count()
                )
            else:
                if requested_device == "cuda":
                    raise RuntimeError(
                        "DEVICE=cuda requested but CUDA is not available. "
                        "Set DEVICE=cpu for CPU inference."
                    )
                self._device = "cpu"
                self.logger.warning(
                    "cuda_not_available",
                    message="CUDA not available, falling back to CPU - inference will be slower",
                )
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            default_model=self._default_model_id,
            device=self._device,
        )

    def _ensure_model_loaded(self, runtime_model_id: str) -> None:
        """Ensure the requested model is loaded, swapping if necessary.

        This method implements the model lifecycle management for Phase 1.
        If the requested model is already loaded, it returns immediately.
        Otherwise, it unloads the current model (with GPU memory cleanup)
        and loads the requested one.

        Args:
            runtime_model_id: NeMo model identifier (e.g., "nvidia/parakeet-tdt-1.1b")

        Raises:
            ValueError: If the runtime_model_id is not in SUPPORTED_MODELS
            RuntimeError: If NeMo toolkit is not installed
        """
        # Fast path: requested model is already loaded
        if runtime_model_id == self._loaded_model_id:
            return

        # Validate the requested model
        if runtime_model_id not in self.SUPPORTED_MODELS:
            raise ValueError(
                f"Unknown model: {runtime_model_id}. "
                f"Supported models: {sorted(self.SUPPORTED_MODELS)}"
            )

        # Unload current model if one is loaded
        if self._model is not None:
            self.logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=runtime_model_id,
            )
            self._set_runtime_state(status="unloading")

            # Delete model reference
            del self._model
            self._model = None
            self._loaded_model_id = None

            # GPU memory cleanup
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
            gc.collect()

            self.logger.info("model_unloaded")

        # Extract decoder type from model ID for logging
        # Format: "nvidia/parakeet-{decoder}-{size}" e.g., "nvidia/parakeet-tdt-1.1b"
        model_parts = runtime_model_id.split("/")[-1].split("-")
        decoder_type = model_parts[1] if len(model_parts) > 1 else "unknown"

        # Load the requested model
        self._set_runtime_state(status="loading")
        self.logger.info(
            "loading_parakeet_model",
            runtime_model_id=runtime_model_id,
            decoder_type=decoder_type,
        )

        # Import NeMo ASR module
        try:
            import nemo.collections.asr as nemo_asr
        except ImportError as e:
            self._set_runtime_state(loaded_model=None, status="error")
            raise RuntimeError(
                "NeMo toolkit not installed. Install with: pip install nemo_toolkit[asr]"
            ) from e

        # Load pre-trained model from NGC/HuggingFace
        # This will download to the cache dir if not already present
        self._model = nemo_asr.models.ASRModel.from_pretrained(runtime_model_id)
        self._model = self._model.to(self._device)
        self._model.eval()
        self._loaded_model_id = runtime_model_id

        # Enable local attention for long audio support (up to 3 hours)
        # This reduces memory from O(n²) to O(n), essential for T4/A10g GPUs
        # See: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
        try:
            self._model.change_attention_model(
                self_attention_model="rel_pos_local_attn",
                att_context_size=[256, 256],
            )
            # Enable subsampling chunking for additional memory savings
            self._model.change_subsampling_conv_chunking_factor(1)
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

        # Update runtime state for heartbeat reporting
        self._set_runtime_state(loaded_model=runtime_model_id, status="idle")
        self.logger.info(
            "model_loaded_successfully",
            runtime_model_id=runtime_model_id,
            device=self._device,
        )

    def _configure_vocabulary_boosting(
        self, vocabulary: list[str], boosting_alpha: float = 0.5
    ) -> Path | None:
        """Configure GPU-PB vocabulary boosting for the model.

        Creates a temporary file with vocabulary terms and configures the model's
        decoding strategy to use GPU-PB (GPU-accelerated Phrase Boosting).

        Args:
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
        if not vocabulary or self._model is None:
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
            decoding_cfg = self._model.cfg.decoding
            decoding_cfg.strategy = "greedy_batch"
            decoding_cfg.greedy.boosting_tree.key_phrases_file = str(vocab_path)
            decoding_cfg.greedy.boosting_tree.context_score = 1.0
            decoding_cfg.greedy.boosting_tree.depth_scaling = 2.0
            decoding_cfg.greedy.boosting_tree_alpha = boosting_alpha

            # Apply the new decoding strategy
            self._model.change_decoding_strategy(decoding_cfg)

            # Extract decoder type from loaded model for logging
            model_parts = (self._loaded_model_id or "").split("/")[-1].split("-")
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

    def _reset_decoding_strategy(self) -> None:
        """Reset decoding strategy to default (no vocabulary boosting)."""
        if self._model is None:
            return

        try:
            decoding_cfg = self._model.cfg.decoding
            decoding_cfg.strategy = "greedy_batch"
            # Clear boosting tree config
            if hasattr(decoding_cfg.greedy, "boosting_tree"):
                decoding_cfg.greedy.boosting_tree.key_phrases_file = None
                decoding_cfg.greedy.boosting_tree_alpha = 0.0

            self._model.change_decoding_strategy(decoding_cfg)
        except Exception as e:
            self.logger.warning(
                "reset_decoding_failed",
                error=str(e),
            )

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Parakeet CTC or TDT.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and words
        """
        audio_path = input.audio_path
        config = input.config
        channel = config.get("channel")  # For per_channel mode
        vocabulary = config.get("vocabulary")  # Terms to boost

        # Get model to use from task config, falling back to default
        # Phase 1: runtime_model_id is the NeMo model identifier (e.g., "nvidia/parakeet-tdt-1.1b")
        runtime_model_id = config.get("runtime_model_id", self._default_model_id)

        # Load model (with swapping if needed)
        self._ensure_model_loaded(runtime_model_id)

        # Extract decoder type from loaded model for alignment method
        model_parts = runtime_model_id.split("/")[-1].split("-")
        decoder_type = model_parts[1] if len(model_parts) > 1 else "ctc"

        # Configure vocabulary boosting if provided
        vocab_file: Path | None = None
        vocabulary_enabled = False
        if vocabulary:
            vocab_file = self._configure_vocabulary_boosting(vocabulary)
            vocabulary_enabled = vocab_file is not None

        self.logger.info(
            "transcribing",
            audio_path=str(audio_path),
            vocabulary_enabled=vocabulary_enabled,
        )

        # Transcribe with word-level timestamps
        # NeMo RNNT models can return word timestamps via the alignment
        # Use autocast for GPU, no-op context for CPU
        autocast_ctx = (
            torch.cuda.amp.autocast()
            if self._device == "cuda"
            else torch.inference_mode()
        )
        try:
            with autocast_ctx:
                # Use transcribe method with timestamps=True for word-level timing
                transcriptions = self._model.transcribe(
                    [str(audio_path)],
                    batch_size=1,
                    return_hypotheses=True,
                    timestamps=True,  # Enable word-level timestamps
                )
        finally:
            # Clean up vocabulary boosting
            if vocab_file is not None:
                try:
                    vocab_file.unlink(missing_ok=True)
                    self._reset_decoding_strategy()
                except Exception:
                    pass  # Best effort cleanup

        # Process the hypothesis
        if not transcriptions:
            return TaskOutput(
                data=TranscribeOutput(
                    text="",
                    segments=[],
                    language="en",
                    language_confidence=1.0,
                    channel=channel,
                    engine_id=self._engine_id,
                    skipped=False,
                    warnings=[],
                )
            )

        # Handle both current and future NeMo API formats:
        # - Current: transcriptions[batch_idx][decode_strategy_idx] - nested lists
        #   where inner index selects greedy (0) vs beam (1) decoding
        # - Future: transcriptions[batch_idx] - list of Hypothesis objects directly
        #   (per deprecation warning for _transcribe_output_processing)
        # See: https://github.com/NVIDIA-NeMo/NeMo/issues/7677
        first_result = transcriptions[0]
        if isinstance(first_result, list):
            # Current format: nested list [batch][strategy]
            hypothesis = first_result[0]
        else:
            # Future format: direct Hypothesis object
            hypothesis = first_result

        # Extract text from Hypothesis object
        full_text = hypothesis.text

        # Determine alignment method based on decoder type
        if decoder_type == "ctc":
            alignment_method = AlignmentMethod.CTC
        else:  # tdt
            alignment_method = AlignmentMethod.TDT

        # Build segments with word-level timestamps
        segments: list[Segment] = []
        all_words: list[Word] = []

        # Check for timestep dict (TDT models with timestamps=True)
        # NeMo returns hypothesis.timestep with 'word', 'segment', 'char' keys
        if hasattr(hypothesis, "timestep") and isinstance(hypothesis.timestep, dict):
            word_timestamps = hypothesis.timestep.get("word", [])
            segment_timestamps = hypothesis.timestep.get("segment", [])

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
                    alignment_method=alignment_method,
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

        # Build warnings list
        warnings: list[str] = []
        if vocabulary and not vocabulary_enabled:
            warnings.append(
                f"Vocabulary boosting ({len(vocabulary)} terms) failed to configure - transcribed without boosting"
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
            engine_id=self._engine_id,
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

        if cuda_available:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        return {
            "status": "healthy",
            "engine_id": self._engine_id,
            "device": self._device,
            "model_loaded": self._model is not None,
            "loaded_model_id": self._loaded_model_id,
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
            engine_id=self._engine_id,
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
