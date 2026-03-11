"""Session handler for real-time transcription.

Manages a single WebSocket transcription session, coordinating
audio buffering, VAD, ASR, and transcript assembly.
"""

from __future__ import annotations

import asyncio
import time

try:
    import audioop  # Python < 3.13
except ImportError:
    import audioop_lts as audioop  # Python >= 3.13 (PEP 594)
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import soxr
import structlog

from dalston.common.audio_defaults import (
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_MIN_SPEECH_MS,
    DEFAULT_PRE_SPEECH_PAD_MS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VAD_THRESHOLD,
)
from dalston.common.ws_close_codes import WS_CLOSE_LAG_EXCEEDED
from dalston.realtime_sdk.assembler import (
    TranscribeResult,
    TranscriptAssembler,
    Word,
)
from dalston.realtime_sdk.context import S3SessionStorage, SessionStorage
from dalston.realtime_sdk.protocol import (
    ClearMessage,
    ConfigUpdateMessage,
    EndMessage,
    ErrorCode,
    ErrorMessage,
    FlushMessage,
    ProcessingLagWarningMessage,
    SegmentInfo,
    SessionBeginMessage,
    SessionConfigInfo,
    SessionEndMessage,
    SessionTerminatedMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    VADSpeechEndMessage,
    VADSpeechStartMessage,
    WordInfo,
    parse_client_message,
)
from dalston.realtime_sdk.vad import VADConfig, VADProcessor

if TYPE_CHECKING:
    from websockets import WebSocketServerProtocol

logger = structlog.get_logger()


LAG_MONITOR_TICK_SECONDS = 0.25
LAG_WARNING_RATE_LIMIT_SECONDS = 1.0


@dataclass
class SessionConfig:
    """Session configuration from connection parameters.

    Attributes:
        session_id: Unique session identifier
        language: Language code or "auto"
        model: Model name (e.g., "faster-whisper-large-v3") or None for any
        encoding: Audio encoding
        sample_rate: Expected sample rate
        channels: Number of audio channels
        enable_vad: Whether VAD events are sent to client
        interim_results: Whether partial transcripts are sent
        word_timestamps: Whether word-level timing is included
        vocabulary: List of terms to boost recognition (for hotwords/bias)
        max_utterance_duration: Max seconds before forcing utterance end (0=unlimited)
        vad_threshold: VAD speech probability threshold (0.0-1.0)
        min_speech_duration_ms: Min speech duration before valid utterance (ms)
        min_silence_duration_ms: Silence duration to trigger endpoint (ms)
        store_audio: Whether to record audio to S3 (uses S3_BUCKET from env)
        store_transcript: Whether to save transcript to S3 (uses S3_BUCKET from env)
        lag_warning_seconds: Lag threshold for warning event emission
        lag_hard_seconds: Lag threshold for hard termination window
        lag_hard_grace_seconds: Continuous hard-lag grace window before termination
        debug_chunk_sleep_initial_seconds: Test-only sleep applied per chunk (starts here)
        debug_chunk_sleep_increment_seconds: Test-only increment added after each chunk
    """

    session_id: str
    language: str = "auto"
    model: str | None = None
    encoding: str = "pcm_s16le"
    client_sample_rate: int | None = None
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = 1
    enable_vad: bool = True
    interim_results: bool = True
    word_timestamps: bool = False
    vocabulary: list[str] | None = None  # Terms to boost recognition
    max_utterance_duration: float = DEFAULT_MAX_UTTERANCE_SECONDS  # Force utterance end
    # VAD tuning parameters (ElevenLabs-compatible naming)
    vad_threshold: float = DEFAULT_VAD_THRESHOLD  # Speech detection threshold (0.0-1.0)
    min_speech_duration_ms: int = DEFAULT_MIN_SPEECH_MS  # Min speech duration (ms)
    min_silence_duration_ms: int = (
        DEFAULT_MIN_SILENCE_MS  # Silence to trigger endpoint (ms)
    )
    prefix_padding_ms: int = DEFAULT_PRE_SPEECH_PAD_MS
    # Storage options (S3 bucket/endpoint read from Settings env vars)
    store_audio: bool = True
    store_transcript: bool = True
    # Realtime lag budget controls (M53)
    lag_warning_seconds: float = 3.0
    lag_hard_seconds: float = 5.0
    lag_hard_grace_seconds: float = 2.0
    # Debug/testing controls (default-off)
    debug_chunk_sleep_initial_seconds: float = 0.0
    debug_chunk_sleep_increment_seconds: float = 0.0


class AudioBuffer:
    """Buffers incoming audio and extracts processing chunks.

    Handles encoding conversion to float32 numpy arrays suitable for
    VAD and ASR processing.

    Supported encodings:
    - pcm_s16le: 16-bit signed PCM, little-endian (default)
    - pcm_f32le: 32-bit float PCM, little-endian
    - mulaw: μ-law encoded (8-bit, telephony)
    - alaw: A-law encoded (8-bit, telephony)
    """

    SUPPORTED_ENCODINGS = ["pcm_s16le", "pcm_f32le", "mulaw", "alaw"]

    def __init__(
        self,
        sample_rate: int,
        encoding: str,
        client_sample_rate: int | None = None,
        channels: int = 1,
        chunk_duration_ms: int = 100,
    ) -> None:
        """Initialize audio buffer.

        Args:
            sample_rate: Expected sample rate
            encoding: Audio encoding (see SUPPORTED_ENCODINGS)
            channels: Number of channels
            chunk_duration_ms: Chunk size for VAD processing in milliseconds
        """
        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding: {encoding}. "
                f"Supported: {self.SUPPORTED_ENCODINGS}"
            )

        self.sample_rate = sample_rate
        self.client_sample_rate = client_sample_rate or sample_rate
        self.encoding = encoding
        self.channels = max(1, channels)
        self.chunk_duration_ms = chunk_duration_ms

        # Calculate chunk size in samples across all channels
        self.chunk_samples = int(sample_rate * chunk_duration_ms / 1000) * self.channels

        # Buffer for accumulated samples (float32)
        self._buffer: list[float] = []
        self._total_samples = 0

    def add(self, data: bytes) -> None:
        """Add raw audio bytes to buffer.

        Decodes to float32 and appends to internal buffer.

        Args:
            data: Raw audio bytes in configured encoding
        """
        samples = self._decode_audio(data)
        samples = self._resample_if_needed(samples)
        self._buffer.extend(samples.tolist())
        self._total_samples += len(samples)

    def get_chunk(self) -> np.ndarray | None:
        """Extract next processing chunk if available.

        Returns:
            Float32 numpy array of chunk_samples length, or None if not enough data
        """
        if len(self._buffer) < self.chunk_samples:
            return None

        chunk = np.array(self._buffer[: self.chunk_samples], dtype=np.float32)
        self._buffer = self._buffer[self.chunk_samples :]
        return chunk

    def flush(self) -> np.ndarray | None:
        """Return all remaining buffered audio.

        Returns:
            Float32 numpy array of remaining samples, or None if empty
        """
        if not self._buffer:
            return None

        chunk = np.array(self._buffer, dtype=np.float32)
        self._buffer.clear()
        return chunk

    def clear(self) -> float:
        """Clear (discard) remaining buffered audio without returning it.

        Used for OpenAI-compatible input_audio_buffer.clear operation.

        Returns:
            Discarded audio duration in seconds.
        """
        discarded_duration = self.get_buffered_duration()
        self._buffer.clear()
        return discarded_duration

    def get_total_duration(self) -> float:
        """Total audio duration received in seconds."""
        return self._total_samples / (self.sample_rate * self.channels)

    def get_buffered_duration(self) -> float:
        """Buffered (unprocessed) audio duration in seconds."""
        return len(self._buffer) / (self.sample_rate * self.channels)

    def _decode_audio(self, data: bytes) -> np.ndarray:
        """Decode raw bytes to float32 numpy array.

        Args:
            data: Raw audio bytes

        Returns:
            Float32 numpy array normalized to [-1, 1]
        """
        if self.encoding == "pcm_s16le":
            # 16-bit signed PCM, little-endian
            samples = np.frombuffer(data, dtype=np.int16)
            return samples.astype(np.float32) / 32768.0

        elif self.encoding == "pcm_f32le":
            # 32-bit float PCM, little-endian (already normalized)
            return np.frombuffer(data, dtype=np.float32).copy()

        elif self.encoding == "mulaw":
            # μ-law to 16-bit PCM, then normalize
            pcm_data = audioop.ulaw2lin(data, 2)
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            return samples.astype(np.float32) / 32768.0

        elif self.encoding == "alaw":
            # A-law to 16-bit PCM, then normalize
            pcm_data = audioop.alaw2lin(data, 2)
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            return samples.astype(np.float32) / 32768.0

        else:
            raise ValueError(f"Unsupported encoding: {self.encoding}")

    def _resample_if_needed(self, samples: np.ndarray) -> np.ndarray:
        """Resample client audio to worker processing sample rate if needed.

        Uses libsoxr for high-quality polyphase resampling with anti-aliasing
        filter, avoiding the spectral aliasing that linear interpolation causes
        when downsampling (e.g. 48 kHz → 16 kHz).
        """
        if self.client_sample_rate == self.sample_rate or len(samples) == 0:
            return samples

        return soxr.resample(
            samples,
            self.client_sample_rate,
            self.sample_rate,
            quality="HQ",
        ).astype(np.float32)


# Type alias for transcribe callback
TranscribeCallback = Callable[
    [np.ndarray, str, str, list[str] | None],  # audio, language, model, vocabulary
    TranscribeResult,
]

# M71: Type alias for streaming decode callback.
# Takes an Iterator[np.ndarray] of audio chunks, language, and model,
# and yields TranscribeResult for each decoded word.
StreamingDecodeCallback = Callable[
    [Iterator[np.ndarray], str, str],
    Iterator[TranscribeResult],
]


class SessionHandler:
    """Handles one WebSocket transcription session.

    Coordinates the pipeline: Audio → Buffer → VAD → ASR → Assembler → Output

    Example:
        async def transcribe(audio, language, model):
            # Call ASR engine
            return TranscribeResult(...)

        handler = SessionHandler(
            websocket=ws,
            config=SessionConfig(session_id="sess_123"),
            transcribe_fn=transcribe,
        )

        await handler.run()
    """

    # Number of chunks between partial results for streaming models
    PARTIAL_RESULT_INTERVAL_CHUNKS = 5  # ~500ms at 100ms chunks

    def __init__(
        self,
        websocket: WebSocketServerProtocol,
        config: SessionConfig,
        transcribe_fn: TranscribeCallback,
        on_session_end: Callable[[str, float, str], Awaitable[None]] | None = None,
        supports_streaming: bool = False,
        streaming_decode_fn: StreamingDecodeCallback | None = None,
    ) -> None:
        """Initialize session handler.

        Args:
            websocket: WebSocket connection
            config: Session configuration
            transcribe_fn: Callback to engine's transcribe method
            on_session_end: Optional async callback when session ends
            supports_streaming: Whether engine supports streaming partial results
            streaming_decode_fn: M71 callback for cache-aware streaming decode.
                When set, audio chunks are fed directly to the engine's
                streaming decoder, bypassing VAD accumulation. VAD still
                runs for endpoint detection (to know when to flush and
                send final results).
        """
        self.websocket = websocket
        self.config = config
        self._transcribe_fn = transcribe_fn
        self._on_session_end = on_session_end
        self._supports_streaming = supports_streaming
        self._streaming_decode_fn = streaming_decode_fn

        # Initialize components
        self._buffer = AudioBuffer(
            sample_rate=config.sample_rate,
            encoding=config.encoding,
            client_sample_rate=config.client_sample_rate,
            channels=config.channels,
        )
        # Only initialize VAD if enabled - when disabled, use time-based chunking
        if config.enable_vad:
            self._vad: VADProcessor | None = VADProcessor(
                VADConfig(
                    sample_rate=config.sample_rate,
                    speech_threshold=config.vad_threshold,
                    min_speech_duration=config.min_speech_duration_ms / 1000.0,
                    min_silence_duration=config.min_silence_duration_ms / 1000.0,
                    lookback_chunks=max(
                        1,
                        int(round(config.prefix_padding_ms / 100.0)),
                    ),
                )
            )
        else:
            self._vad = None
        self._assembler = TranscriptAssembler()

        # Session state
        self._started_at = time.time()
        self._error: str | None = None
        self._ended = False
        self._lag_terminated = False

        # Streaming partial results state
        self._chunks_since_partial = 0
        self._speech_audio_buffer: list[np.ndarray] = []

        # Lag accounting state (M53)
        self._received_audio_seconds = 0.0
        self._processed_audio_seconds = 0.0
        self._discarded_audio_seconds = 0.0
        self._lag_hard_exceeded_since: float | None = None
        self._last_lag_warning_at: float | None = None
        self._lag_monitor_task: asyncio.Task[None] | None = None
        self._lag_eval_lock = asyncio.Lock()
        self._next_debug_chunk_sleep_seconds = config.debug_chunk_sleep_initial_seconds

        # M71: Streaming decode state
        self._streaming_decode_active = streaming_decode_fn is not None
        self._streaming_chunk_queue: asyncio.Queue[np.ndarray | None] | None = None
        self._streaming_decode_task: asyncio.Task[None] | None = None
        self._streaming_word_count = 0
        self._streaming_session_text_parts: list[str] = []
        self._streaming_word_results: list[Word] = []

        # Storage adapter (initialized in run() when storage is enabled)
        self._session_storage: SessionStorage | None = None
        self._raw_audio_buffer: list[bytes] = []  # Buffer until S3 client ready
        self._raw_audio_buffer_bytes = 0  # Track buffer size
        # Max 10MB buffer - if storage init fails, stop buffering to prevent OOM
        self._max_raw_audio_buffer_bytes = 10 * 1024 * 1024
        self._storage_init_failed = False  # Track if storage init failed

    async def run(self) -> None:
        """Main session processing loop.

        Sends session.begin, processes messages, sends session.end.
        """
        # Bind session_id to logging context for all log calls in this session
        structlog.contextvars.bind_contextvars(session_id=self.config.session_id)

        # Send session.begin
        await self._send_session_begin()

        # Initialize storage recorders if enabled
        await self._init_storage()
        self._lag_monitor_task = asyncio.create_task(self._lag_monitor_loop())

        # M71: Start streaming decode background task if active
        if self._streaming_decode_active:
            self._streaming_chunk_queue = asyncio.Queue(maxsize=100)
            self._streaming_decode_task = asyncio.create_task(
                self._streaming_decode_loop()
            )
            logger.info(
                "streaming_decode_session_start",
                streaming_path="rnnt_streaming",
            )
        else:
            logger.info(
                "streaming_decode_session_start",
                streaming_path="vad_segment",
            )

        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    await self._handle_audio(message)
                else:
                    await self._handle_control(message)

                if self._ended:
                    break

        except Exception as e:
            if self._lag_terminated:
                logger.info("session_closed_after_lag_termination")
            else:
                self._error = str(e)
                logger.exception("session_error", error=str(e))
                await self._send_error(
                    ErrorCode.INTERNAL_ERROR,
                    f"Internal error: {e}",
                    recoverable=False,
                )
        finally:
            # M71: Stop streaming decode before final cleanup
            if self._streaming_decode_active:
                await self._flush_streaming_final()
                await self._stop_streaming_decode()
            await self._stop_lag_monitor()
            # Send session.end if not already sent
            if not self._ended:
                try:
                    await self._send_session_end()
                except Exception as e:
                    logger.error("session_end_failed", error=str(e))
            # Avoid duplicate cleanup on normal session.end path, but still
            # cleanup on early termination (e.g., lag) or send_session_end failure.
            if self._session_storage is not None:
                await self._cleanup_storage()

        # Notify callback
        if self._on_session_end:
            await self._on_session_end(
                self.config.session_id,
                self.get_duration(),
                "error" if self._error else "completed",
            )

    async def _handle_audio(self, data: bytes) -> None:
        """Process incoming audio data.

        Args:
            data: Raw audio bytes
        """
        if self._ended:
            return

        # Record raw audio if enabled
        if self.config.store_audio:
            if self._session_storage:
                try:
                    await self._session_storage.append_audio(data)
                except Exception as e:
                    logger.warning("audio_write_failed", error=str(e))
            elif not self._storage_init_failed:
                # Buffer until S3 client initialized (with size limit)
                new_size = self._raw_audio_buffer_bytes + len(data)
                if new_size <= self._max_raw_audio_buffer_bytes:
                    self._raw_audio_buffer.append(data)
                    self._raw_audio_buffer_bytes = new_size
                else:
                    # Buffer limit reached - clear and disable to prevent OOM
                    logger.warning(
                        "audio_buffer_limit_reached",
                        buffer_bytes=self._raw_audio_buffer_bytes,
                        max_bytes=self._max_raw_audio_buffer_bytes,
                    )
                    self._raw_audio_buffer.clear()
                    self._raw_audio_buffer_bytes = 0
                    self._storage_init_failed = True

        # Add to buffer
        self._buffer.add(data)
        self._record_received_audio(len(data))
        await self._evaluate_lag_budget(source="audio_received")

        # Process in chunks
        while True:
            if self._ended:
                break
            chunk = self._buffer.get_chunk()
            if chunk is None:
                break
            await self._process_chunk(chunk)
            self._record_processed_audio(self._samples_to_seconds(len(chunk)))
            await self._evaluate_lag_budget(source="chunk_processed")

    async def _process_chunk(self, audio: np.ndarray) -> None:
        """Process a single audio chunk through VAD and ASR.

        Args:
            audio: Float32 audio samples
        """
        if self._ended:
            return

        await self._maybe_apply_debug_chunk_sleep()

        # M71: When streaming decode is active, feed chunks directly
        # to the background decoder. VAD still runs for endpoint
        # detection and speech_start/end events, but does not gate
        # inference.
        if self._streaming_decode_active:
            await self._process_chunk_streaming(audio)
            return

        # When VAD is disabled, use time-based chunking only
        if self._vad is None:
            await self._process_chunk_no_vad(audio)
            return

        # Run VAD
        vad_result = self._vad.process_chunk(audio)

        if vad_result.event == "speech_start":
            # Reset streaming state
            self._chunks_since_partial = 0
            self._speech_audio_buffer = []

            await self._send(
                VADSpeechStartMessage(timestamp=self._assembler.current_time)
            )

        elif vad_result.event == "speech_end":
            await self._send(
                VADSpeechEndMessage(timestamp=self._assembler.current_time)
            )

            # Clear streaming state
            self._speech_audio_buffer = []
            self._chunks_since_partial = 0

            # Transcribe if we have speech audio
            if vad_result.speech_audio is not None and len(vad_result.speech_audio) > 0:
                await self._transcribe_and_send(vad_result.speech_audio)

        # Handle streaming partial results during speech
        elif (
            self._vad.is_speaking
            and self._supports_streaming
            and self.config.interim_results
        ):
            # Accumulate audio for partial transcription
            self._speech_audio_buffer.append(audio.copy())
            self._chunks_since_partial += 1

            # Send partial result every N chunks
            if self._chunks_since_partial >= self.PARTIAL_RESULT_INTERVAL_CHUNKS:
                await self._send_partial_result()
                self._chunks_since_partial = 0

        # Check for max utterance duration exceeded (prevents unbounded accumulation)
        if self._vad.is_speaking and self.config.max_utterance_duration > 0:
            await self._check_max_utterance_duration()

    # -- M71: Streaming decode methods ----------------------------------------

    async def _process_chunk_streaming(self, audio: np.ndarray) -> None:
        """Process chunk in streaming decode mode.

        Feeds the audio chunk to the background streaming decoder via
        the chunk queue. VAD runs in parallel for endpoint detection
        (speech_start/end events) but does not gate inference.

        Args:
            audio: Float32 audio samples
        """
        # Feed chunk to streaming decoder
        if self._streaming_chunk_queue is not None:
            await self._streaming_chunk_queue.put(audio.copy())

        # Run VAD in parallel for endpoint detection events
        if self._vad is not None:
            vad_result = self._vad.process_chunk(audio)

            if vad_result.event == "speech_start":
                await self._send(
                    VADSpeechStartMessage(timestamp=self._assembler.current_time)
                )

            elif vad_result.event == "speech_end":
                await self._send(
                    VADSpeechEndMessage(timestamp=self._assembler.current_time)
                )

                # On endpoint, send a final result with accumulated text
                # from the streaming decoder
                await self._flush_streaming_final()

    async def _streaming_decode_loop(self) -> None:
        """Background task: consume audio chunks and emit partial results.

        Runs the engine's streaming decode callback in a thread pool,
        yielding TranscribeResult for each decoded word, and sends
        partial transcript events to the client.
        """
        if self._streaming_decode_fn is None or self._streaming_chunk_queue is None:
            return

        # Capture the event loop reference for thread-safe bridging.
        # Must be captured here (in async context), not inside threads.
        loop = asyncio.get_running_loop()

        def _chunk_iterator() -> Iterator[np.ndarray]:
            """Blocking iterator that reads from the async queue."""
            while True:
                future = asyncio.run_coroutine_threadsafe(
                    self._streaming_chunk_queue.get(),
                    loop,  # type: ignore[union-attr]
                )
                chunk = future.result(timeout=10.0)
                if chunk is None:
                    return
                yield chunk

        try:
            async for result in self._run_streaming_in_thread(_chunk_iterator, loop):
                if self._ended:
                    break

                if result.text and result.words:
                    self._streaming_session_text_parts.append(result.text)
                    self._streaming_word_results.extend(result.words)
                    self._streaming_word_count += len(result.words)

                    # Send partial transcript event
                    if self.config.interim_results:
                        cumulative_text = " ".join(self._streaming_session_text_parts)
                        word = result.words[0]

                        await self._send(
                            TranscriptPartialMessage(
                                text=cumulative_text,
                                start=word.start,
                                end=word.end,
                            )
                        )

        except Exception as e:
            if not self._ended:
                logger.error("streaming_decode_error", error=str(e))

    async def _run_streaming_in_thread(
        self,
        chunk_iterator_factory: Callable[[], Iterator[np.ndarray]],
        loop: asyncio.AbstractEventLoop,
    ) -> AsyncIterator[TranscribeResult]:
        """Run streaming decode in thread and yield results asynchronously.

        Args:
            chunk_iterator_factory: Callable returning a blocking chunk iterator
            loop: The event loop for thread-safe coroutine scheduling
        """
        result_queue: asyncio.Queue[TranscribeResult | None] = asyncio.Queue()

        def _thread_target() -> None:
            """Thread function that runs the blocking streaming decode."""
            try:
                chunk_iter = chunk_iterator_factory()
                for result in self._streaming_decode_fn(  # type: ignore[misc]
                    chunk_iter,
                    self.config.language,
                    self.config.model,
                ):
                    asyncio.run_coroutine_threadsafe(result_queue.put(result), loop)
            except Exception as e:
                logger.error("streaming_thread_error", error=str(e))
            finally:
                asyncio.run_coroutine_threadsafe(result_queue.put(None), loop)

        executor_future = loop.run_in_executor(None, _thread_target)

        while True:
            result = await result_queue.get()
            if result is None:
                break
            yield result

        await executor_future

    async def _flush_streaming_final(self) -> None:
        """Send a final transcript from accumulated streaming words.

        Called when VAD detects an endpoint during streaming decode.
        Combines all streaming words into a single final segment,
        using the real timestamps accumulated from the decoder.
        """
        if not self._streaming_session_text_parts:
            return

        full_text = " ".join(self._streaming_session_text_parts)

        # Use real word results from the streaming decoder
        words = list(self._streaming_word_results)

        # Calculate confidence from word results
        confidences = [w.confidence for w in words if w.confidence]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.95

        result = TranscribeResult(
            text=full_text,
            words=words,
            language=self.config.language,
            confidence=avg_confidence,
        )

        # Calculate audio duration from word timestamps
        audio_duration = words[-1].end if words else 0.0

        # Add to assembler for timeline tracking
        segment = self._assembler.add_utterance(result, audio_duration)

        # Send final transcript
        word_infos = None
        if self.config.word_timestamps and segment.words:
            word_infos = [
                WordInfo(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    confidence=w.confidence,
                )
                for w in segment.words
            ]

        await self._send(
            TranscriptFinalMessage(
                text=segment.text,
                start=segment.start,
                end=segment.end,
                confidence=segment.confidence,
                words=word_infos,
            )
        )

        # Reset for next utterance
        self._streaming_session_text_parts = []
        self._streaming_word_results = []
        self._streaming_word_count = 0

    async def _stop_streaming_decode(self) -> None:
        """Stop the streaming decode background task."""
        if self._streaming_chunk_queue is not None:
            # Send sentinel to stop the chunk iterator
            await self._streaming_chunk_queue.put(None)

        if self._streaming_decode_task is not None:
            try:
                await asyncio.wait_for(self._streaming_decode_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._streaming_decode_task.cancel()

    # -- End M71 streaming decode methods ------------------------------------

    async def _process_chunk_no_vad(self, audio: np.ndarray) -> None:
        """Process audio chunk without VAD - pure time-based chunking.

        When VAD is disabled, we accumulate all audio and transcribe
        at fixed intervals based on max_utterance_duration.
        """
        if self._ended:
            return

        # Always accumulate audio
        self._speech_audio_buffer.append(audio.copy())
        self._chunks_since_partial += 1

        # Send partial results if streaming is enabled
        if self._supports_streaming and self.config.interim_results:
            if self._chunks_since_partial >= self.PARTIAL_RESULT_INTERVAL_CHUNKS:
                await self._send_partial_result()
                self._chunks_since_partial = 0

        # Check if we've accumulated enough audio for a chunk
        if self.config.max_utterance_duration > 0:
            total_samples = sum(len(chunk) for chunk in self._speech_audio_buffer)
            duration = self._samples_to_seconds(total_samples)

            if duration >= self.config.max_utterance_duration:
                logger.info(
                    "time_based_chunk_triggered",
                    duration=duration,
                    max_duration=self.config.max_utterance_duration,
                )

                # Transcribe accumulated audio
                if self._speech_audio_buffer:
                    audio_to_transcribe = np.concatenate(self._speech_audio_buffer)
                    await self._transcribe_and_send(audio_to_transcribe)

                # Reset buffer for next chunk
                self._speech_audio_buffer = []
                self._chunks_since_partial = 0

    async def _send_partial_result(self) -> None:
        """Send partial transcription result during speech.

        Called periodically for streaming models while VAD is in speech state.
        """
        if self._ended or not self._speech_audio_buffer:
            return

        try:
            # Concatenate accumulated audio
            audio = np.concatenate(self._speech_audio_buffer)

            # Transcribe in thread pool to avoid blocking event loop
            result = await asyncio.to_thread(
                self._transcribe_fn,
                audio,
                self.config.language,
                self.config.model,
                self.config.vocabulary,
            )

            if self._ended or not result.text:
                return

            # Calculate timing
            audio_duration = self._samples_to_seconds(len(audio))
            start_time = self._assembler.current_time
            end_time = start_time + audio_duration

            # Send partial result
            await self._send(
                TranscriptPartialMessage(
                    text=result.text,
                    start=start_time,
                    end=end_time,
                )
            )

        except Exception as e:
            logger.debug("partial_transcription_error", error=str(e))
            # Don't send error for partial - it's best-effort

    async def _check_max_utterance_duration(self) -> None:
        """Force utterance end if max duration exceeded.

        Prevents unbounded memory growth and transcription latency
        when speech continues without natural pauses.
        """
        # Calculate accumulated speech duration from VAD buffer
        speech_samples = self._vad.get_speech_buffer_samples()
        speech_duration = self._samples_to_seconds(speech_samples)

        if speech_duration >= self.config.max_utterance_duration:
            logger.info(
                "max_utterance_duration_exceeded",
                duration=speech_duration,
                max_duration=self.config.max_utterance_duration,
            )

            # Send VAD speech_end event
            if self.config.enable_vad:
                await self._send(
                    VADSpeechEndMessage(timestamp=self._assembler.current_time)
                )

            # Get accumulated speech audio from VAD and transcribe
            speech_audio = self._vad.force_endpoint()
            if speech_audio is not None and len(speech_audio) > 0:
                await self._transcribe_and_send(speech_audio)

            # Clear streaming state
            self._speech_audio_buffer = []
            self._chunks_since_partial = 0

            # Send VAD speech_start event (speech continues)
            if self.config.enable_vad:
                await self._send(
                    VADSpeechStartMessage(timestamp=self._assembler.current_time)
                )

    async def _transcribe_and_send(self, audio: np.ndarray) -> None:
        """Transcribe audio and send result.

        Args:
            audio: Speech audio to transcribe
        """
        if self._ended:
            return

        try:
            # Call ASR in thread pool to avoid blocking event loop
            # This is critical for slow models (e.g., CPU inference) to
            # prevent WebSocket keepalive ping timeouts
            result = await asyncio.to_thread(
                self._transcribe_fn,
                audio,
                self.config.language,
                self.config.model,
                self.config.vocabulary,
            )

            if self._ended or not result.text:
                return

            # Add to assembler
            audio_duration = self._samples_to_seconds(len(audio))
            segment = self._assembler.add_utterance(result, audio_duration)

            # Send transcript.final
            words = None
            if self.config.word_timestamps and segment.words:
                words = [
                    WordInfo(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence,
                    )
                    for w in segment.words
                ]

            await self._send(
                TranscriptFinalMessage(
                    text=segment.text,
                    start=segment.start,
                    end=segment.end,
                    confidence=segment.confidence,
                    words=words,
                )
            )

        except Exception as e:
            logger.error("transcription_error", error=str(e))
            await self._send_error(
                ErrorCode.INTERNAL_ERROR,
                f"Transcription failed: {e}",
                recoverable=True,
            )

    async def _handle_control(self, message: str) -> None:
        """Handle control message (JSON).

        Args:
            message: JSON string
        """
        if self._ended:
            return

        try:
            parsed = parse_client_message(message)
        except ValueError as e:
            await self._send_error(
                ErrorCode.INVALID_MESSAGE,
                str(e),
                recoverable=True,
            )
            return

        if isinstance(parsed, ConfigUpdateMessage):
            if parsed.language:
                self.config.language = parsed.language
                logger.debug("language_updated", language=parsed.language)
            if parsed.vad_threshold is not None:
                self.config.vad_threshold = parsed.vad_threshold
                if self._vad is not None:
                    self._vad.config.speech_threshold = parsed.vad_threshold
            if parsed.min_silence_duration_ms is not None:
                self.config.min_silence_duration_ms = parsed.min_silence_duration_ms
                if self._vad is not None:
                    self._vad.config.min_silence_duration = (
                        parsed.min_silence_duration_ms / 1000.0
                    )
            if parsed.prefix_padding_ms is not None:
                self.config.prefix_padding_ms = parsed.prefix_padding_ms
                if self._vad is not None:
                    self._vad.config.lookback_chunks = max(
                        1,
                        int(
                            round(
                                parsed.prefix_padding_ms
                                / self._buffer.chunk_duration_ms
                            )
                        ),
                    )

        elif isinstance(parsed, FlushMessage):
            # M71: Flush streaming decode state first
            if self._streaming_decode_active:
                await self._flush_streaming_final()

            # Flush VAD buffer
            if self._vad is not None:
                remaining = self._vad.flush()
                if remaining is not None and len(remaining) > 0:
                    if not self._streaming_decode_active:
                        await self._transcribe_and_send(remaining)

            # Flush audio buffer
            remaining = self._buffer.flush()
            if remaining is not None:
                remaining_duration = self._samples_to_seconds(len(remaining))
                if remaining_duration > 0.1:
                    if not self._streaming_decode_active:
                        # Only process if > 100ms
                        await self._transcribe_and_send(remaining)
                    self._record_processed_audio(remaining_duration)
                else:
                    self._record_discarded_audio(remaining_duration)

        elif isinstance(parsed, ClearMessage):
            # Clear (discard) buffered audio without processing
            if self._vad is not None:
                self._vad.clear()
            discarded_duration = self._buffer.clear()
            self._record_discarded_audio(discarded_duration)
            # Clear streaming state
            self._speech_audio_buffer = []
            self._chunks_since_partial = 0
            # M71: Clear streaming decode accumulated state
            self._streaming_session_text_parts = []
            self._streaming_word_results = []
            self._streaming_word_count = 0
            logger.debug("audio_buffers_cleared")

        elif isinstance(parsed, EndMessage):
            # Graceful end
            await self._send_session_end()
            self._ended = True

        await self._evaluate_lag_budget(source="control_message")

    async def _send_session_begin(self) -> None:
        """Send session.begin message."""
        await self._send(
            SessionBeginMessage(
                session_id=self.config.session_id,
                config=SessionConfigInfo(
                    sample_rate=self.config.sample_rate,
                    encoding=self.config.encoding,
                    channels=self.config.channels,
                    language=self.config.language,
                    model=self.config.model,
                ),
            )
        )

    async def _init_storage(self) -> None:
        """Initialize storage recorders if enabled.

        Creates session storage adapter and flushes any buffered raw audio.
        """
        if not self.config.store_audio and not self.config.store_transcript:
            return

        try:
            self._session_storage = S3SessionStorage(
                store_audio=self.config.store_audio,
                store_transcript=self.config.store_transcript,
            )
            await self._session_storage.start(self.config.session_id, self.config)

            for chunk in self._raw_audio_buffer:
                await self._session_storage.append_audio(chunk)
            self._raw_audio_buffer.clear()
            self._raw_audio_buffer_bytes = 0

        except Exception as e:
            logger.error("storage_init_failed", error=str(e))
            # Continue without storage - don't fail the session
            self._storage_init_failed = True
            # Clear any buffered audio to free memory
            self._raw_audio_buffer.clear()
            self._raw_audio_buffer_bytes = 0

    async def _cleanup_storage(self) -> None:
        """Cleanup storage adapter and abort any incomplete uploads."""
        if self._session_storage:
            try:
                await self._session_storage.abort()
            except Exception:
                pass
            self._session_storage = None

    async def _send_session_end(self) -> None:
        """Send session.end message."""
        # Flush any remaining audio
        if self._vad is not None:
            remaining = self._vad.flush()
            if remaining is not None and len(remaining) > 0:
                await self._transcribe_and_send(remaining)
        elif self._speech_audio_buffer:
            # No VAD mode - transcribe any remaining buffered audio
            audio_to_transcribe = np.concatenate(self._speech_audio_buffer)
            await self._transcribe_and_send(audio_to_transcribe)
            self._speech_audio_buffer = []

        segments = [
            SegmentInfo(start=s.start, end=s.end, text=s.text)
            for s in self._assembler.get_segments()
        ]

        # Finalize storage and get URIs
        audio_uri = None
        transcript_uri = None

        if self._session_storage:
            try:
                transcript_data = {
                    "session_id": self.config.session_id,
                    "language": self.config.language,
                    "duration_seconds": self._assembler.current_time,
                    "text": self._assembler.get_full_transcript(),
                    "utterances": [
                        {
                            "id": i,
                            "start": s.start,
                            "end": s.end,
                            "text": s.text,
                            "words": [
                                {
                                    "word": w.word,
                                    "start": w.start,
                                    "end": w.end,
                                    "confidence": w.confidence,
                                }
                                for w in (s.words or [])
                            ],
                        }
                        for i, s in enumerate(self._assembler.get_segments())
                    ],
                }
                await self._session_storage.save_transcript(transcript_data)
                storage_result = await self._session_storage.finalize()
                audio_uri = storage_result.audio_artifact_ref
                transcript_uri = storage_result.transcript_artifact_ref
            except Exception as e:
                logger.error("session_storage_finalize_failed", error=str(e))

        # Cleanup S3 client
        await self._cleanup_storage()

        await self._send(
            SessionEndMessage(
                session_id=self.config.session_id,
                total_audio_seconds=self._buffer.get_total_duration(),
                total_speech_duration=self._assembler.current_time,
                transcript=self._assembler.get_full_transcript(),
                segments=segments,
                audio_uri=audio_uri,
                transcript_uri=transcript_uri,
            )
        )

        self._ended = True

    def _samples_to_seconds(self, sample_count: int) -> float:
        """Convert decoded sample count to audio seconds."""
        if sample_count <= 0:
            return 0.0
        return sample_count / (self.config.sample_rate * self.config.channels)

    def _audio_bytes_to_seconds(self, byte_count: int) -> float:
        """Convert raw encoded audio bytes to audio seconds."""
        if byte_count <= 0:
            return 0.0

        if self.config.encoding == "pcm_s16le":
            bytes_per_sample = 2
        elif self.config.encoding == "pcm_f32le":
            bytes_per_sample = 4
        elif self.config.encoding in {"mulaw", "alaw"}:
            bytes_per_sample = 1
        else:
            # Defensive fallback: unknown encoding should have been rejected at parse time.
            bytes_per_sample = 2

        return byte_count / (
            bytes_per_sample * self.config.sample_rate * self.config.channels
        )

    def _record_received_audio(self, byte_count: int) -> None:
        self._received_audio_seconds += self._audio_bytes_to_seconds(byte_count)

    def _record_processed_audio(self, processed_seconds: float) -> None:
        if processed_seconds > 0:
            self._processed_audio_seconds += processed_seconds

    def _record_discarded_audio(self, discarded_seconds: float) -> None:
        if discarded_seconds > 0:
            self._discarded_audio_seconds += discarded_seconds

    def _lag_seconds(self) -> float:
        return max(
            0.0,
            self._received_audio_seconds
            - self._processed_audio_seconds
            - self._discarded_audio_seconds,
        )

    async def _lag_monitor_loop(self) -> None:
        """Periodic lag monitor to enforce hard threshold during long ASR calls."""
        try:
            while not self._ended:
                await self._evaluate_lag_budget(source="monitor")
                await asyncio.sleep(LAG_MONITOR_TICK_SECONDS)
        except asyncio.CancelledError:
            return

    async def _stop_lag_monitor(self) -> None:
        if self._lag_monitor_task is None:
            return
        self._lag_monitor_task.cancel()
        try:
            await self._lag_monitor_task
        except asyncio.CancelledError:
            pass
        self._lag_monitor_task = None

    async def _evaluate_lag_budget(
        self,
        source: str,
        now: float | None = None,
    ) -> None:
        """Evaluate lag budget and apply warning/termination policy."""
        if self._ended:
            return

        check_time = time.monotonic() if now is None else now

        async with self._lag_eval_lock:
            if self._ended:
                return

            lag_seconds = self._lag_seconds()

            if lag_seconds >= self.config.lag_warning_seconds:
                if (
                    self._last_lag_warning_at is None
                    or check_time - self._last_lag_warning_at
                    >= LAG_WARNING_RATE_LIMIT_SECONDS
                ):
                    await self._send(
                        ProcessingLagWarningMessage(
                            lag_seconds=round(lag_seconds, 3),
                            warning_threshold_seconds=self.config.lag_warning_seconds,
                            hard_threshold_seconds=self.config.lag_hard_seconds,
                        )
                    )
                    self._last_lag_warning_at = check_time
                    logger.warning(
                        "processing_lag_warning",
                        lag_seconds=round(lag_seconds, 3),
                        warning_threshold_seconds=self.config.lag_warning_seconds,
                        hard_threshold_seconds=self.config.lag_hard_seconds,
                        source=source,
                    )

            if lag_seconds < self.config.lag_hard_seconds:
                if self._lag_hard_exceeded_since is not None:
                    logger.debug(
                        "lag_hard_threshold_cleared",
                        lag_seconds=round(lag_seconds, 3),
                        source=source,
                    )
                self._lag_hard_exceeded_since = None
                return

            if self._lag_hard_exceeded_since is None:
                self._lag_hard_exceeded_since = check_time
                logger.warning(
                    "lag_hard_threshold_crossed",
                    lag_seconds=round(lag_seconds, 3),
                    hard_threshold_seconds=self.config.lag_hard_seconds,
                    grace_seconds=self.config.lag_hard_grace_seconds,
                    source=source,
                )
                return

            if (
                check_time - self._lag_hard_exceeded_since
                < self.config.lag_hard_grace_seconds
            ):
                return

            await self._terminate_for_lag(lag_seconds)

    async def _maybe_apply_debug_chunk_sleep(self) -> None:
        """Inject progressive per-chunk delay for deterministic lag testing."""
        delay_seconds = self._next_debug_chunk_sleep_seconds
        next_delay = delay_seconds + self.config.debug_chunk_sleep_increment_seconds
        self._next_debug_chunk_sleep_seconds = max(0.0, next_delay)

        if delay_seconds <= 0:
            return

        logger.debug(
            "debug_chunk_sleep_applied",
            sleep_seconds=round(delay_seconds, 3),
            next_sleep_seconds=round(self._next_debug_chunk_sleep_seconds, 3),
        )
        await asyncio.sleep(delay_seconds)

    async def _terminate_for_lag(self, lag_seconds: float) -> None:
        """Terminate session because lag exceeded hard budget for full grace window."""
        if self._ended:
            return

        self._lag_terminated = True
        self._error = ErrorCode.LAG_EXCEEDED
        self._ended = True

        logger.error(
            "lag_budget_exceeded_terminating_session",
            lag_seconds=round(lag_seconds, 3),
            warning_threshold_seconds=self.config.lag_warning_seconds,
            hard_threshold_seconds=self.config.lag_hard_seconds,
            hard_grace_seconds=self.config.lag_hard_grace_seconds,
        )

        await self._send_error(
            code=ErrorCode.LAG_EXCEEDED,
            message="Realtime lag budget exceeded",
            recoverable=False,
        )
        await self._send(
            SessionTerminatedMessage(
                session_id=self.config.session_id,
                reason=ErrorCode.LAG_EXCEEDED,
                recoverable=False,
            )
        )

        try:
            await self.websocket.close(
                code=WS_CLOSE_LAG_EXCEEDED,
                reason="Realtime lag budget exceeded",
            )
        except Exception as e:
            logger.debug("lag_close_failed", error=str(e))

    async def _send_error(
        self,
        code: str,
        message: str,
        recoverable: bool = True,
    ) -> None:
        """Send error message."""
        await self._send(
            ErrorMessage(code=code, message=message, recoverable=recoverable)
        )

    async def _send(self, message: Any) -> None:
        """Send message to client.

        Args:
            message: Protocol message with to_json() method
        """
        message_type = getattr(message, "type", None)
        if self._lag_terminated and message_type not in {"error", "session.terminated"}:
            return
        try:
            await self.websocket.send(message.to_json())
        except Exception as e:
            logger.error("send_failed", error=str(e))

    def get_duration(self) -> float:
        """Get session wall-clock duration in seconds."""
        return time.time() - self._started_at

    @property
    def error(self) -> str | None:
        """Error message if session failed."""
        return self._error

    @property
    def session_id(self) -> str:
        """Session identifier."""
        return self.config.session_id
