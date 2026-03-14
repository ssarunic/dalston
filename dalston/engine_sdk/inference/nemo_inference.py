"""Shared inference core for NeMo Parakeet batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path.

This module owns the NeMoModelManager and provides a engine_id-neutral
interface for transcription. Each engine adapter (batch / realtime) is
responsible for formatting the raw results into its own output contract.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.managers import NeMoModelManager

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result types — engine_id-neutral, no dependency on batch or RT SDK types
# ---------------------------------------------------------------------------


@dataclass
class NeMoWordResult:
    """A single word with timing from NeMo hypothesis."""

    word: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class NeMoSegmentResult:
    """A transcription segment from NeMo hypothesis."""

    start: float
    end: float
    text: str
    words: list[NeMoWordResult] = field(default_factory=list)


@dataclass
class NeMoTranscriptionResult:
    """Complete transcription result from a single NeMo inference call."""

    text: str = ""
    segments: list[NeMoSegmentResult] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 1.0


# ---------------------------------------------------------------------------
# NemoInference — shared inference logic
# ---------------------------------------------------------------------------


class NemoInference:
    """Shared inference logic for NeMo Parakeet batch and realtime.

    Owns the NeMoModelManager and provides a unified transcription interface.
    Both batch and realtime adapters delegate inference here while keeping
    their own I/O contracts and output formatting.

    For advanced features like vocabulary boosting (GPU-PB) that require
    direct model access, adapters can use the ``manager`` property to
    acquire/release models and call ``transcribe_with_model()`` directly.
    """

    SUPPORTED_MODELS = list(NeMoModelManager.SUPPORTED_MODELS.keys())

    def __init__(
        self,
        device: str = "cuda",
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ) -> None:
        self._manager = NeMoModelManager(
            device=device,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )

        logger.info(
            "nemo_inference_init",
            device=device,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
        )

    # -- Properties ----------------------------------------------------------

    @property
    def device(self) -> str:
        return self._manager.device

    @property
    def manager(self) -> NeMoModelManager:
        """Expose manager for stats, shutdown, and direct model access."""
        return self._manager

    # -- Core transcription --------------------------------------------------

    def transcribe(
        self,
        audio: str | np.ndarray | list,
        model_id: str,
    ) -> NeMoTranscriptionResult:
        """Run transcription on audio input.

        Works with both file paths (batch) and numpy arrays (realtime).

        Args:
            audio: File path string, numpy float32 array, or list of either
            model_id: Model identifier (e.g. "parakeet-tdt-1.1b")

        Returns:
            NeMoTranscriptionResult with text, segments, and words.
        """
        model = self._manager.acquire(model_id)
        try:
            return self.transcribe_with_model(model, audio)
        finally:
            self._manager.release(model_id)

    def transcribe_with_model(
        self,
        model: Any,
        audio: str | np.ndarray | list,
    ) -> NeMoTranscriptionResult:
        """Run transcription with an already-acquired model.

        Use this when you need direct model access (e.g., for vocabulary
        boosting) and manage the acquire/release lifecycle yourself.

        Args:
            model: An acquired NeMo ASRModel instance
            audio: File path string, numpy float32 array, or list of either

        Returns:
            NeMoTranscriptionResult with text, segments, and words.
        """
        import torch

        # Normalize audio input to list format expected by NeMo
        if isinstance(audio, str | np.ndarray):
            audio_list = [audio]
        else:
            audio_list = audio

        # Prepare numpy arrays
        prepared = []
        for item in audio_list:
            if isinstance(item, np.ndarray):
                if item.dtype != np.float32:
                    item = item.astype(np.float32)
                if item.ndim > 1:
                    item = item.squeeze()
            prepared.append(item)

        # Run inference with appropriate context manager
        autocast_ctx = (
            torch.amp.autocast("cuda")
            if self.device == "cuda"
            else torch.inference_mode()
        )
        with autocast_ctx:
            transcriptions = model.transcribe(
                prepared,
                batch_size=1,
                return_hypotheses=True,
                timestamps=True,
            )

        if not transcriptions:
            return NeMoTranscriptionResult()

        # Handle NeMo API: transcriptions[batch][strategy] or transcriptions[batch]
        first_result = transcriptions[0]
        if isinstance(first_result, list):
            hypothesis = first_result[0]
        else:
            hypothesis = first_result

        full_text = hypothesis.text if hasattr(hypothesis, "text") else str(hypothesis)

        # Parse timestamps from hypothesis
        segments, all_words = self._parse_hypothesis(hypothesis, full_text)

        return NeMoTranscriptionResult(
            text=full_text.strip(),
            segments=segments,
            language="en",
            language_probability=1.0,
        )

    # -- Decoder type detection -----------------------------------------------

    # Decoder types that support cache-aware streaming inference
    STREAMING_DECODER_TYPES = frozenset({"rnnt", "tdt"})

    def decoder_type(self, model_id: str) -> str:
        """Return the decoder architecture for a model ID.

        Args:
            model_id: Model identifier (e.g. "parakeet-rnnt-1.1b")

        Returns:
            Decoder type string: "rnnt", "ctc", or "tdt"
        """
        return self._manager.get_architecture(model_id)

    def is_cache_aware_streaming(self, model_id: str) -> bool:
        """Return True if model supports per-chunk BatchedFrameASRRNNT streaming.

        Delegates to NeMoModelManager.is_cache_aware_streaming().
        """
        return self._manager.is_cache_aware_streaming(model_id)

    def supports_native_streaming_decode(self, model_id: str) -> bool:
        """Check whether a model supports cache-aware streaming inference.

        RNNT and TDT decoders emit tokens frame-by-frame; CTC requires
        the full sequence and cannot stream.

        Args:
            model_id: Model identifier

        Returns:
            True if the model's decoder supports streaming
        """
        return self.decoder_type(model_id) in self.STREAMING_DECODER_TYPES

    # -- Streaming transcription ---------------------------------------------

    def transcribe_streaming(
        self,
        audio_iter: Iterator[np.ndarray],
        model_id: str,
        chunk_ms: int = 160,
        buffer_secs: float = 4.0,  # unused; kept for API compatibility
    ) -> Iterator[NeMoWordResult]:
        """Yield word results from streaming inference.

        Dispatches to one of two paths based on whether the model was trained
        for cache-aware per-chunk streaming:

        - **Cache-aware** (``nemotron-streaming-rnnt-0.6b``): uses
          ``model.conformer_stream_step`` to emit tokens as each chunk arrives,
          carrying encoder attention cache and RNNT decoder state across chunks.
          Results are yielded during the stream with no pre-fill buffer.
        - **Offline RNNT/TDT** (``parakeet-rnnt-*``, ``parakeet-tdt-*``): collects
          all chunks, runs a single batch transcription, then yields words. Results
          are emitted after the stream ends.

        Only valid for RNNT and TDT model variants. Raises ``RuntimeError``
        if called on a CTC model.

        Args:
            audio_iter: Iterator of float32 numpy arrays (audio chunks)
            model_id: Model identifier (must be RNNT or TDT)
            chunk_ms: Chunk duration in milliseconds (default 160)
            buffer_secs: Unused; retained for call-site compatibility

        Yields:
            NeMoWordResult for each decoded token

        Raises:
            RuntimeError: If called on a CTC model
        """
        decoder = self.decoder_type(model_id)
        if decoder not in self.STREAMING_DECODER_TYPES:
            raise RuntimeError(
                f"Streaming inference is not supported for {decoder!r} "
                f"decoder (model {model_id!r}). Only RNNT and TDT "
                f"decoders support cache-aware streaming."
            )

        model = self._manager.acquire(model_id)
        try:
            if self._manager.is_cache_aware_streaming(model_id):
                yield from self._run_cache_aware_streaming(model, audio_iter, chunk_ms)
            else:
                yield from self._run_streaming_inference(model, audio_iter, chunk_ms)
        finally:
            self._manager.release(model_id)

    def _run_streaming_inference(
        self,
        model: Any,
        audio_iter: Iterator[np.ndarray],
        chunk_ms: int,
    ) -> Iterator[NeMoWordResult]:
        """Run streaming inference by collecting all audio then calling batch transcribe.

        Offline RNNT/TDT models (parakeet-rnnt-*, parakeet-tdt-*) were not
        trained with limited right context, so they require the full audio
        to produce accurate transcriptions. This method collects all chunks
        from *audio_iter*, concatenates them, runs a single batch transcription
        via ``transcribe_with_model``, and yields one ``NeMoWordResult`` per
        word with timestamps from the hypothesis.

        Word results are emitted after the stream ends (not chunk-by-chunk).
        This is the correct latency trade-off for offline models.

        Args:
            model: Acquired NeMo RNNT/TDT model instance
            audio_iter: Audio chunk iterator (float32 numpy arrays, 16 kHz)
            chunk_ms: Unused; retained for API compatibility

        Yields:
            NeMoWordResult for each decoded word
        """
        chunks: list[np.ndarray] = []
        for chunk in audio_iter:
            if chunk.size == 0:
                continue
            arr = chunk.squeeze() if chunk.ndim > 1 else chunk
            chunks.append(arr.astype(np.float32, copy=False))

        if not chunks:
            return

        full_audio = np.concatenate(chunks)
        result = self.transcribe_with_model(model, full_audio)

        for seg in result.segments:
            if seg.words:
                for w in seg.words:
                    yield NeMoWordResult(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence,
                    )
            elif seg.text.strip():
                # No word-level timestamps: emit one result per word with segment timing
                words = seg.text.split()
                if not words:
                    continue
                word_dur = (
                    (seg.end - seg.start) / len(words) if seg.end > seg.start else 0.1
                )
                for i, word in enumerate(words):
                    t = seg.start + i * word_dur
                    yield NeMoWordResult(
                        word=word,
                        start=round(t, 3),
                        end=round(t + word_dur, 3),
                    )

    def _run_cache_aware_streaming(
        self,
        model: Any,
        audio_iter: Iterator[np.ndarray],
        chunk_ms: int,
    ) -> Iterator[NeMoWordResult]:
        """Per-chunk cache-aware streaming via CacheAwareStreamingAudioBuffer.

        Uses ``CacheAwareStreamingAudioBuffer`` to preprocess audio with
        dithering disabled and no padding, which is required for correct
        streaming inference. The buffer's preprocessor produces clean mel
        features per chunk; ``conformer_stream_step`` then carries both
        encoder attention cache and RNNT decoder state across chunks.

        Args:
            model: Acquired NeMo RNNT model with cache-aware encoder (Nemotron)
            audio_iter: Audio chunk iterator (float32 numpy arrays, 16 kHz)
            chunk_ms: Chunk duration in milliseconds

        Yields:
            NeMoWordResult for each newly decoded word
        """
        import torch
        from nemo.collections.asr.parts.utils.streaming_utils import (
            CacheAwareStreamingAudioBuffer,
        )

        chunk_dur = chunk_ms / 1000.0

        # CacheAwareStreamingAudioBuffer creates a dedicated preprocessor with
        # dither=0.0 and pad_to=0 — required for streaming inference accuracy.
        streaming_buffer = CacheAwareStreamingAudioBuffer(model=model)

        # Initialise encoder cache to correct shapes (vs None which could
        # cause shape mismatches on the first step).
        cache_last_channel, cache_last_time, cache_last_channel_len = (
            model.encoder.get_initial_cache_state(batch_size=1)
        )

        previous_hypotheses: Any = None
        prev_text = ""
        step_start = 0.0
        stream_id = -1  # -1 creates stream 0; subsequent calls use stream 0

        logger.debug("cache_aware_stream_start", chunk_ms=chunk_ms)

        model.eval()
        with torch.inference_mode():
            for chunk in audio_iter:
                if chunk.size == 0:
                    continue
                arr = chunk.squeeze() if chunk.ndim > 1 else chunk
                arr = arr.astype(np.float32, copy=False)

                # Preprocess via buffer (dither=0, pad_to=0, online norm off)
                processed_signal, processed_signal_length, stream_id = (
                    streaming_buffer.append_audio(arr, stream_id=stream_id)
                )

                # Skip inference if no new features produced for this chunk
                if processed_signal_length.numel() == 0 or (
                    processed_signal_length.dim() > 0
                    and processed_signal_length.max() == 0
                ):
                    step_start += chunk_dur
                    continue

                # Buffer returns a 0-dim scalar; encoder expects shape [batch]
                if processed_signal_length.dim() == 0:
                    processed_signal_length = processed_signal_length.unsqueeze(0)

                # Cache-aware encoder step + RNNT decode
                (
                    _greedy_predictions,
                    _all_hyps,
                    cache_last_channel,
                    cache_last_time,
                    cache_last_channel_len,
                    best_hyp,
                ) = model.conformer_stream_step(
                    processed_signal=processed_signal,
                    processed_signal_length=processed_signal_length,
                    cache_last_channel=cache_last_channel,
                    cache_last_time=cache_last_time,
                    cache_last_channel_len=cache_last_channel_len,
                    previous_hypotheses=previous_hypotheses,
                    keep_all_outputs=True,
                )

                if best_hyp:
                    # Update RNNT decoder state only when we have a valid hypothesis
                    previous_hypotheses = best_hyp

                    hyp = best_hyp[0]
                    curr_text = ""
                    if hasattr(hyp, "text") and hyp.text:
                        curr_text = str(hyp.text)
                    elif hasattr(hyp, "y_sequence") and hyp.y_sequence is not None:
                        # Fallback: decode token IDs when .text wasn't populated
                        tokens = (
                            hyp.y_sequence.tolist()
                            if hasattr(hyp.y_sequence, "tolist")
                            else list(hyp.y_sequence)
                        )
                        curr_text = model.tokenizer.ids_to_text(tokens)

                    if curr_text:
                        yield from self._emit_new_words(
                            curr_text, prev_text, step_start, chunk_dur
                        )
                        prev_text = curr_text

                step_start += chunk_dur

    @staticmethod
    def _emit_new_words(
        current_text: str,
        previous_text: str,
        step_start: float,
        chunk_dur: float,
    ) -> Iterator[NeMoWordResult]:
        """Yield word results for the new portion of a cumulative hypothesis.

        Diffs *current_text* against *previous_text* to find newly emitted
        tokens and distributes them uniformly across the chunk window.

        Args:
            current_text: Cumulative hypothesis text after this step
            previous_text: Cumulative text before this step
            step_start: Start timestamp of this chunk (seconds)
            chunk_dur: Duration of this chunk (seconds)

        Yields:
            NeMoWordResult for each new word token
        """
        if not current_text or current_text == previous_text:
            return

        if current_text.startswith(previous_text):
            new_text = current_text[len(previous_text) :].lstrip()
        else:
            # Non-linear update (e.g. beam correction): emit the full new text.
            new_text = current_text

        new_words = new_text.split()
        if not new_words:
            return

        word_dur = chunk_dur / len(new_words)
        for i, word in enumerate(new_words):
            t = step_start + i * word_dur
            yield NeMoWordResult(
                word=word,
                start=round(t, 3),
                end=round(t + word_dur, 3),
            )

    # -- Hypothesis parsing --------------------------------------------------

    @staticmethod
    def _parse_hypothesis(
        hypothesis: Any, full_text: str
    ) -> tuple[list[NeMoSegmentResult], list[NeMoWordResult]]:
        """Parse a NeMo hypothesis into segments and words.

        Handles three timestep formats:
        1. Dict with 'word'/'segment' keys (TDT models with timestamps=True)
        2. List of frame indices (RNNT legacy format)
        3. No timestep data (fallback)

        Args:
            hypothesis: NeMo Hypothesis object
            full_text: Full transcription text

        Returns:
            Tuple of (segments, all_words)
        """
        segments: list[NeMoSegmentResult] = []
        all_words: list[NeMoWordResult] = []

        # Case 1: TDT dict format
        if hasattr(hypothesis, "timestep") and isinstance(hypothesis.timestep, dict):
            word_timestamps = hypothesis.timestep.get("word", [])
            segment_timestamps = hypothesis.timestep.get("segment", [])

            for wt in word_timestamps:
                all_words.append(
                    NeMoWordResult(
                        word=wt.get("word", ""),
                        start=round(wt.get("start", 0.0), 3),
                        end=round(wt.get("end", 0.0), 3),
                    )
                )

            if segment_timestamps:
                for seg in segment_timestamps:
                    seg_start = seg.get("start", 0.0)
                    seg_end = seg.get("end", 0.0)
                    seg_text = seg.get("segment", "")
                    seg_words = [
                        w
                        for w in all_words
                        if w.start >= seg_start - 0.01 and w.end <= seg_end + 0.01
                    ]
                    segments.append(
                        NeMoSegmentResult(
                            start=round(seg_start, 3),
                            end=round(seg_end, 3),
                            text=seg_text,
                            words=seg_words if seg_words else [],
                        )
                    )
            elif all_words:
                segments.append(
                    NeMoSegmentResult(
                        start=all_words[0].start,
                        end=all_words[-1].end,
                        text=full_text.strip(),
                        words=all_words,
                    )
                )

        # Case 2: RNNT legacy list format
        elif hasattr(hypothesis, "timestep") and hypothesis.timestep is not None:
            timesteps = hypothesis.timestep
            tokens = full_text.split()
            frame_shift_seconds = 0.01

            for i, (token, frame_idx) in enumerate(
                zip(tokens, timesteps, strict=False)
            ):
                word_start = frame_idx * frame_shift_seconds
                if i + 1 < len(timesteps):
                    word_end = timesteps[i + 1] * frame_shift_seconds
                else:
                    word_end = word_start + 0.1

                all_words.append(
                    NeMoWordResult(
                        word=token,
                        start=round(word_start, 3),
                        end=round(word_end, 3),
                    )
                )

            if all_words:
                segments.append(
                    NeMoSegmentResult(
                        start=all_words[0].start,
                        end=all_words[-1].end,
                        text=full_text.strip(),
                        words=all_words,
                    )
                )

        # Case 3: No timestamp data
        else:
            if full_text.strip():
                segments.append(
                    NeMoSegmentResult(
                        start=0.0,
                        end=0.0,
                        text=full_text.strip(),
                    )
                )

        return segments, all_words

    # -- Lifecycle -----------------------------------------------------------

    def get_stats(self) -> dict:
        """Get model manager statistics."""
        return self._manager.get_stats()

    def shutdown(self) -> None:
        """Shutdown core and release all models."""
        logger.info("nemo_inference_shutdown")
        self._manager.shutdown()

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_env(cls) -> NemoInference:
        """Create a NemoInference configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
        """
        # Auto-detect device
        device = os.environ.get("DALSTON_DEVICE", "").lower()
        if not device or device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        return cls(
            device=device,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )
