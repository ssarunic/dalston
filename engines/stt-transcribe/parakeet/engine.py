"""NVIDIA Parakeet transcription engine.

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

Environment variables:
    MODEL_VARIANT: Model variant (ctc-0.6b, ctc-1.1b, tdt-0.6b-v3, tdt-1.1b).
                   Defaults to ctc-0.6b.
    DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
"""

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
    """NVIDIA Parakeet transcription engine.

    Uses FastConformer encoder with CTC or TDT decoder for efficient
    English-only transcription. Automatically produces word-level
    timestamps without requiring separate alignment.

    Decoder types:
    - CTC: Fastest inference, greedy decoding, good for batch processing
    - TDT: Best accuracy, predicts token durations, recommended for quality

    Supports both GPU (CUDA) and CPU inference. GPU is strongly
    recommended for production use.
    """

    # Model variant to NeMo model identifier mapping
    MODEL_VARIANT_MAP = {
        "ctc-0.6b": "nvidia/parakeet-ctc-0.6b",
        "ctc-1.1b": "nvidia/parakeet-ctc-1.1b",
        "tdt-0.6b-v3": "nvidia/parakeet-tdt-0.6b-v3",
        "tdt-1.1b": "nvidia/parakeet-tdt-1.1b",
    }
    DEFAULT_MODEL_VARIANT = "ctc-0.6b"

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._model_name: str | None = None

        # Determine model variant from environment variable (set at container build time)
        model_variant = os.environ.get("MODEL_VARIANT", self.DEFAULT_MODEL_VARIANT)
        if model_variant not in self.MODEL_VARIANT_MAP:
            self.logger.warning(
                "unknown_model_variant",
                requested=model_variant,
                using=self.DEFAULT_MODEL_VARIANT,
            )
            model_variant = self.DEFAULT_MODEL_VARIANT
        self._model_variant = model_variant
        self._nemo_model_id = self.MODEL_VARIANT_MAP[model_variant]

        # Extract decoder type and size from variant
        parts = model_variant.split("-")
        self._decoder_type = parts[0]  # ctc or tdt
        self._model_size = parts[1] if len(parts) > 1 else "0.6b"

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

        self.logger.info(
            "loading_parakeet_model",
            model_name=model_name,
            decoder_type=self._decoder_type,
        )

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

        self.logger.info("model_loaded_successfully", model_name=model_name)

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
            # Different config paths for CTC vs TDT/RNNT models
            if self._decoder_type == "ctc":
                decoding_cfg = self._model.cfg.decoding
                decoding_cfg.strategy = "greedy_batch"
                decoding_cfg.greedy.boosting_tree.key_phrases_file = str(vocab_path)
                decoding_cfg.greedy.boosting_tree.context_score = 1.0
                decoding_cfg.greedy.boosting_tree.depth_scaling = 2.0
                decoding_cfg.greedy.boosting_tree_alpha = boosting_alpha
            else:
                # TDT/RNNT models
                decoding_cfg = self._model.cfg.decoding
                decoding_cfg.strategy = "greedy_batch"
                decoding_cfg.greedy.boosting_tree.key_phrases_file = str(vocab_path)
                decoding_cfg.greedy.boosting_tree.context_score = 1.0
                decoding_cfg.greedy.boosting_tree.depth_scaling = 2.0
                decoding_cfg.greedy.boosting_tree_alpha = boosting_alpha

            # Apply the new decoding strategy
            self._model.change_decoding_strategy(decoding_cfg)

            self.logger.info(
                "vocabulary_boosting_enabled",
                decoder_type=self._decoder_type,
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

        # Use the model configured at container build time
        model_name = self._nemo_model_id

        # Load model (lazy loading)
        self._load_model(model_name)

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
                    engine_id=f"parakeet-{self._model_variant}",
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
        if self._decoder_type == "ctc":
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
            engine_id=f"parakeet-{self._model_variant}",
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
        word-level timestamps via CTC or TDT alignment.
        """
        # VRAM requirements per model size
        vram_mb = 4000 if self._model_size == "0.6b" else 6000

        return EngineCapabilities(
            engine_id=f"parakeet-{self._model_variant}",
            version="1.0.0",
            stages=["transcribe"],
            languages=["en"],  # English only
            supports_word_timestamps=True,
            supports_streaming=False,
            model_variants=[self._nemo_model_id],
            gpu_required=True,
            gpu_vram_mb=vram_mb,
        )


if __name__ == "__main__":
    engine = ParakeetEngine()
    engine.run()
