"""Shared inference core for NeMo Parakeet batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path.

This module owns the NeMoModelManager and provides a engine_id-neutral
interface for transcription. Each engine adapter (batch / realtime) is
responsible for formatting the raw results into its own output contract.
"""

from __future__ import annotations

import math
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
    ) -> Iterator[NeMoWordResult]:
        """Yield word results incrementally as audio chunks arrive.

        Uses NeMo's ``CacheAwareStreamingConfig`` to run incremental
        inference on RNNT/TDT models. Each audio chunk from *audio_iter*
        is fed to the model's streaming decoder, and any newly emitted
        tokens are yielded as ``NeMoWordResult`` objects.

        Only valid for RNNT and TDT model variants. Raises ``RuntimeError``
        if called on a CTC model.

        Args:
            audio_iter: Iterator of float32 numpy arrays (audio chunks)
            model_id: Model identifier (must be RNNT or TDT)
            chunk_ms: Chunk duration in milliseconds (default 160,
                      one FastConformer encoder chunk)

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
            yield from self._run_streaming_inference(model, audio_iter, chunk_ms)
        finally:
            self._manager.release(model_id)

    def _run_streaming_inference(
        self,
        model: Any,
        audio_iter: Iterator[np.ndarray],
        chunk_ms: int,
    ) -> Iterator[NeMoWordResult]:
        """Run cache-aware streaming inference on an acquired model.

        Uses NeMo 2.x ``CacheAwareStreamingAudioBuffer`` to preprocess audio
        and ``model.conformer_stream_step`` to run the encoder incrementally.

        Audio chunks are fed one at a time (stream_id=0 after the first) so
        the buffer stays at batch-size 1 throughout.  ``keep_all_outputs`` is
        kept False for all intermediate steps — setting it True mid-stream
        (e.g. when the buffer temporarily empties between incoming chunks)
        doubles the cache tensor size and causes a shape mismatch on the next
        step.  A final flush pass after the iterator is exhausted runs with
        ``keep_all_outputs=True`` to drain any remaining pending tokens.

        Args:
            model: Acquired NeMo RNNT/TDT model instance
            audio_iter: Audio chunk iterator (float32 numpy arrays)
            chunk_ms: Target chunk duration in ms. Used to configure a bounded
                      streaming window for RNNT/TDT decode.

        Yields:
            NeMoWordResult for each newly decoded token
        """
        import torch
        from nemo.collections.asr.parts.utils.streaming_utils import (
            CacheAwareStreamingAudioBuffer,
        )

        self._configure_bounded_streaming_params(model, chunk_ms=chunk_ms)
        self._ensure_streaming_positional_capacity(model)

        streaming_buffer = CacheAwareStreamingAudioBuffer(model=model)
        cache_last_channel, cache_last_time, cache_last_channel_len = (
            model.encoder.get_initial_cache_state(batch_size=1)
        )
        previous_hypotheses = None
        previous_text = ""
        step_num = 0

        autocast_ctx = (
            torch.amp.autocast("cuda")
            if self.device == "cuda"
            else torch.inference_mode()
        )

        pred_out_stream = None
        elapsed_audio_s = 0.0

        def _step(chunk_audio: Any, chunk_lengths: Any, keep_all: bool) -> str:
            nonlocal cache_last_channel, cache_last_time, cache_last_channel_len
            nonlocal previous_hypotheses, pred_out_stream, step_num

            result = model.conformer_stream_step(
                processed_signal=chunk_audio,
                processed_signal_length=chunk_lengths,
                cache_last_channel=cache_last_channel,
                cache_last_time=cache_last_time,
                cache_last_channel_len=cache_last_channel_len,
                keep_all_outputs=keep_all,
                previous_hypotheses=previous_hypotheses,
                previous_pred_out=pred_out_stream,
                drop_extra_pre_encoded=(
                    model.encoder.streaming_cfg.drop_extra_pre_encoded
                    if step_num != 0
                    else 0
                ),
                return_transcription=True,
            )
            (
                pred_out_stream,
                transcribed_texts,
                cache_last_channel,
                cache_last_time,
                cache_last_channel_len,
                previous_hypotheses,
            ) = result
            step_num += 1

            # M71: Prefer the cumulative hypothesis text for word-splitting logic.
            # conformer_stream_step often returns 'new-only' tokens in
            # transcribed_texts, which breaks the _emit_new_words logic that
            # expects cumulative input to find new tokens via indexing.
            if previous_hypotheses:
                first = previous_hypotheses[0]
                if hasattr(first, "text"):
                    return str(first.text or "")
                return str(first)

            # Fallback for non-transducer or if hypotheses list is empty
            if not transcribed_texts:
                return ""
            first = transcribed_texts[0]
            if isinstance(first, str):
                return first
            if hasattr(first, "text"):
                return str(first.text or "")
            return str(first)

        def _emit_new_words(
            current_text: str, chunk_audio: Any
        ) -> Iterator[NeMoWordResult]:
            nonlocal previous_text, elapsed_audio_s
            n_frames = (
                chunk_audio.shape[2] if chunk_audio.dim() == 3 else chunk_audio.shape[1]
            )
            step_dur = n_frames * 0.01  # ~10 ms per mel frame at 16 kHz
            step_start = elapsed_audio_s
            elapsed_audio_s += step_dur

            if not current_text or current_text == previous_text:
                return
            prev_words = previous_text.split()
            curr_words = current_text.split()
            new_words = curr_words[len(prev_words) :]
            previous_text = current_text
            if not new_words:
                return
            word_dur = step_dur / len(new_words)
            for i, word in enumerate(new_words):
                t = step_start + i * word_dur
                yield NeMoWordResult(
                    word=word,
                    start=round(t, 3),
                    end=round(t + word_dur, 3),
                )

        def _iter_with_last(
            chunks: Iterator[np.ndarray],
        ) -> Iterator[tuple[np.ndarray, bool]]:
            """Yield (chunk, is_last_input_chunk) with one-element lookahead."""
            iterator = iter(chunks)
            try:
                current = next(iterator)
            except StopIteration:
                return

            for next_chunk in iterator:
                yield current, False
                current = next_chunk
            yield current, True

        min_append_samples = self._streaming_min_append_samples(model, chunk_ms)
        coalesced_audio_iter = self._coalesce_audio_chunks(
            audio_iter, min_append_samples
        )

        stream_id = -1  # -1 creates stream 0; 0 appends to stream 0
        with autocast_ctx:
            for audio_chunk, is_last_input_chunk in _iter_with_last(
                coalesced_audio_iter
            ):
                streaming_buffer.append_audio(audio_chunk, stream_id=stream_id)
                stream_id = 0
                for chunk_audio, chunk_lengths in streaming_buffer:
                    keep_all = (
                        is_last_input_chunk and streaming_buffer.is_buffer_empty()
                    )
                    text = _step(chunk_audio, chunk_lengths, keep_all=keep_all)
                    yield from _emit_new_words(text, chunk_audio)

            # Final flush — drain frames that couldn't fill a full encoder step
            # and emit any remaining pending tokens with keep_all_outputs=True.
            for chunk_audio, chunk_lengths in streaming_buffer:
                text = _step(
                    chunk_audio,
                    chunk_lengths,
                    keep_all=streaming_buffer.is_buffer_empty(),
                )
                yield from _emit_new_words(text, chunk_audio)

    @staticmethod
    def _streaming_min_append_samples(model: Any, chunk_ms: int) -> int:
        """Return minimum raw-sample chunk size before appending to NeMo buffer.

        NeMo's cache-aware buffer can produce empty RNNT hypotheses when fed very
        small append chunks (for example, 100 ms) incrementally. Coalescing input
        chunks to at least one streaming window stabilizes incremental decode.
        """
        configured = int(os.environ.get("DALSTON_RNNT_MIN_APPEND_SAMPLES", "0"))
        if configured > 0:
            return configured

        encoder = getattr(model, "encoder", None)
        streaming_cfg = getattr(encoder, "streaming_cfg", None)
        chunk_size = getattr(streaming_cfg, "chunk_size", None)

        if isinstance(chunk_size, list):
            # Use the subsequent-step size (index 1) when available.
            frames = int(chunk_size[1])
        elif chunk_size is not None:
            frames = int(chunk_size)
        else:
            frames = max(1, int(round(chunk_ms / 10.0)))

        # Mel hop is 10 ms at 16 kHz => 160 raw samples per frame.
        return max(1600, frames * 160)

    @staticmethod
    def _coalesce_audio_chunks(
        chunks: Iterator[np.ndarray],
        min_samples: int,
    ) -> Iterator[np.ndarray]:
        """Coalesce small realtime chunks into larger append units."""
        if min_samples <= 1:
            yield from chunks
            return

        pending: list[np.ndarray] = []
        pending_samples = 0

        for chunk in chunks:
            if chunk.size == 0:
                continue
            if chunk.ndim > 1:
                chunk = chunk.squeeze()
            if chunk.dtype != np.float32:
                chunk = chunk.astype(np.float32)

            pending.append(chunk)
            pending_samples += int(chunk.shape[0])

            if pending_samples >= min_samples:
                yield np.concatenate(pending).astype(np.float32, copy=False)
                pending = []
                pending_samples = 0

        if pending:
            yield np.concatenate(pending).astype(np.float32, copy=False)

    @staticmethod
    def _configure_bounded_streaming_params(model: Any, chunk_ms: int) -> None:
        """Configure bounded cache-aware streaming params for RNNT/TDT.

        Parakeet RNNT configs often use ``att_context_size=[-1, -1]``.
        Calling ``setup_streaming_params()`` with defaults maps this to a very
        large cache window (``last_channel_cache_size=10000``), which can
        destabilize streaming decode in NeMo 2.x.

        We force a bounded window:
        - ``chunk_size`` and ``shift_size`` are derived from ``chunk_ms``
          (10 ms features => ``chunk_ms / 10``).
        - ``left_chunks`` defaults to 2 and can be overridden via
          ``DALSTON_RNNT_LEFT_CHUNKS``.
        """
        encoder = getattr(model, "encoder", None)
        if encoder is None or not hasattr(encoder, "setup_streaming_params"):
            return

        chunk_steps = max(1, int(round(chunk_ms / 10.0)))
        left_chunks = max(1, int(os.environ.get("DALSTON_RNNT_LEFT_CHUNKS", "2")))

        encoder.setup_streaming_params(
            chunk_size=chunk_steps,
            shift_size=chunk_steps,
            left_chunks=left_chunks,
        )

        streaming_cfg = getattr(encoder, "streaming_cfg", None)
        logger.debug(
            "configured_bounded_streaming_params",
            chunk_ms=chunk_ms,
            chunk_steps=chunk_steps,
            left_chunks=left_chunks,
            stream_chunk_size=getattr(streaming_cfg, "chunk_size", None),
            stream_shift_size=getattr(streaming_cfg, "shift_size", None),
            stream_valid_out_len=getattr(streaming_cfg, "valid_out_len", None),
            stream_cache_size=getattr(streaming_cfg, "last_channel_cache_size", None),
        )

    @staticmethod
    def _ensure_streaming_positional_capacity(model: Any) -> None:
        """Ensure positional encoding can cover cache-aware streaming context.

        Some RNNT/TDT configs set an effectively unbounded left context
        (``att_context_size[0] == -1``), which makes
        ``streaming_cfg.last_channel_cache_size`` large (for example 10000).
        If positional encodings are initialized with a smaller
        ``pos_emb_max_len`` (for example 5000), the first
        ``conformer_stream_step()`` can fail with an attention shape mismatch.

        We proactively grow the encoder positional range to at least
        cache_size + max_encoded_chunk_size before stepping.
        """
        encoder = getattr(model, "encoder", None)
        if encoder is None:
            return

        streaming_cfg = getattr(encoder, "streaming_cfg", None)
        if streaming_cfg is None and hasattr(encoder, "setup_streaming_params"):
            encoder.setup_streaming_params()
            streaming_cfg = getattr(encoder, "streaming_cfg", None)
        if streaming_cfg is None:
            return

        cache_size = getattr(streaming_cfg, "last_channel_cache_size", None)
        chunk_size = getattr(streaming_cfg, "chunk_size", None)
        if cache_size is None or chunk_size is None:
            return

        if isinstance(chunk_size, list):
            max_chunk = max(int(v) for v in chunk_size)
        else:
            max_chunk = int(chunk_size)

        subsampling = int(getattr(encoder, "subsampling_factor", 1) or 1)
        # Conservative bound for post-subsampling chunk width seen by attention.
        max_encoded_chunk = max(2, math.ceil(max_chunk / subsampling) + 2)
        required_max_audio_len = int(cache_size) + max_encoded_chunk

        current_max_audio_len = int(getattr(encoder, "max_audio_length", 0) or 0)
        if current_max_audio_len < required_max_audio_len and hasattr(
            encoder, "set_max_audio_length"
        ):
            encoder.set_max_audio_length(required_max_audio_len)
            logger.debug(
                "expanded_streaming_positional_capacity",
                previous_max_audio_length=current_max_audio_len,
                required_max_audio_length=required_max_audio_len,
                cache_size=int(cache_size),
                max_encoded_chunk=max_encoded_chunk,
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
