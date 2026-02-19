"""Session handler for real-time transcription.

Manages a single WebSocket transcription session, coordinating
audio buffering, VAD, ASR, and transcript assembly.
"""

from __future__ import annotations

import asyncio
import audioop
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from dalston.realtime_sdk.assembler import (
    TranscribeResult,
    TranscriptAssembler,
)
from dalston.realtime_sdk.protocol import (
    ConfigUpdateMessage,
    EndMessage,
    ErrorCode,
    ErrorMessage,
    FlushMessage,
    SegmentInfo,
    SessionBeginMessage,
    SessionConfigInfo,
    SessionEndMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    VADSpeechEndMessage,
    VADSpeechStartMessage,
    WordInfo,
    parse_client_message,
)
from dalston.realtime_sdk.vad import VADConfig, VADProcessor

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client
    from websockets import WebSocketServerProtocol

logger = structlog.get_logger()


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
        max_utterance_duration: Max seconds before forcing utterance end (0=unlimited)
        vad_threshold: VAD speech probability threshold (0.0-1.0)
        min_speech_duration_ms: Min speech duration before valid utterance (ms)
        min_silence_duration_ms: Silence duration to trigger endpoint (ms)
        store_audio: Whether to record audio to S3 (uses S3_BUCKET from env)
        store_transcript: Whether to save transcript to S3 (uses S3_BUCKET from env)
    """

    session_id: str
    language: str = "auto"
    model: str | None = None
    encoding: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    enable_vad: bool = True
    interim_results: bool = True
    word_timestamps: bool = False
    max_utterance_duration: float = 60.0  # Force utterance end after 60s
    # VAD tuning parameters (ElevenLabs-compatible naming)
    vad_threshold: float = 0.5  # Speech detection threshold (0.0-1.0)
    min_speech_duration_ms: int = 250  # Min speech duration (ms)
    min_silence_duration_ms: int = 500  # Silence to trigger endpoint (ms)
    # Storage options (S3 bucket/endpoint read from Settings env vars)
    store_audio: bool = True
    store_transcript: bool = True


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
        chunk_duration_ms: int = 100,
    ) -> None:
        """Initialize audio buffer.

        Args:
            sample_rate: Expected sample rate
            encoding: Audio encoding (see SUPPORTED_ENCODINGS)
            chunk_duration_ms: Chunk size for VAD processing in milliseconds
        """
        if encoding not in self.SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported encoding: {encoding}. "
                f"Supported: {self.SUPPORTED_ENCODINGS}"
            )

        self.sample_rate = sample_rate
        self.encoding = encoding
        self.chunk_duration_ms = chunk_duration_ms

        # Calculate chunk size in samples
        self.chunk_samples = int(sample_rate * chunk_duration_ms / 1000)

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

    def get_total_duration(self) -> float:
        """Total audio duration received in seconds."""
        return self._total_samples / self.sample_rate

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


# Type alias for transcribe callback
TranscribeCallback = Callable[
    [np.ndarray, str, str],  # audio, language, model
    TranscribeResult,
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
    ) -> None:
        """Initialize session handler.

        Args:
            websocket: WebSocket connection
            config: Session configuration
            transcribe_fn: Callback to engine's transcribe method
            on_session_end: Optional async callback when session ends
            supports_streaming: Whether engine supports streaming partial results
        """
        self.websocket = websocket
        self.config = config
        self._transcribe_fn = transcribe_fn
        self._on_session_end = on_session_end
        self._supports_streaming = supports_streaming

        # Initialize components
        self._buffer = AudioBuffer(
            sample_rate=config.sample_rate,
            encoding=config.encoding,
        )
        self._vad = VADProcessor(
            VADConfig(
                sample_rate=config.sample_rate,
                speech_threshold=config.vad_threshold,
                min_speech_duration=config.min_speech_duration_ms / 1000.0,
                min_silence_duration=config.min_silence_duration_ms / 1000.0,
            )
        )
        self._assembler = TranscriptAssembler()

        # Session state
        self._started_at = time.time()
        self._error: str | None = None
        self._ended = False

        # Streaming partial results state
        self._chunks_since_partial = 0
        self._speech_audio_buffer: list[np.ndarray] = []

        # Storage recorders (initialized in run() when S3 client available)
        self._s3_context_manager = None  # Context manager for S3 client
        self._s3_client: S3Client | None = None
        self._audio_recorder = None  # AudioRecorder when store_audio=True
        self._transcript_recorder = (
            None  # TranscriptRecorder when store_transcript=True
        )
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

        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    await self._handle_audio(message)
                else:
                    await self._handle_control(message)

                if self._ended:
                    break

        except Exception as e:
            self._error = str(e)
            logger.exception("session_error", error=str(e))
            await self._send_error(
                ErrorCode.INTERNAL_ERROR,
                f"Internal error: {e}",
                recoverable=False,
            )
        finally:
            # Send session.end if not already sent
            if not self._ended:
                try:
                    await self._send_session_end()
                except Exception as e:
                    logger.error("session_end_failed", error=str(e))
                    # Ensure cleanup happens even if session end fails
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
        # Record raw audio if enabled
        if self.config.store_audio:
            if self._audio_recorder:
                try:
                    await self._audio_recorder.write(data)
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

        # Process in chunks
        while True:
            chunk = self._buffer.get_chunk()
            if chunk is None:
                break
            await self._process_chunk(chunk)

    async def _process_chunk(self, audio: np.ndarray) -> None:
        """Process a single audio chunk through VAD and ASR.

        Args:
            audio: Float32 audio samples
        """
        # Run VAD
        vad_result = self._vad.process_chunk(audio)

        if vad_result.event == "speech_start":
            # Reset streaming state
            self._chunks_since_partial = 0
            self._speech_audio_buffer = []

            if self.config.enable_vad:
                await self._send(
                    VADSpeechStartMessage(timestamp=self._assembler.current_time)
                )

        elif vad_result.event == "speech_end":
            if self.config.enable_vad:
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

    async def _send_partial_result(self) -> None:
        """Send partial transcription result during speech.

        Called periodically for streaming models while VAD is in speech state.
        """
        if not self._speech_audio_buffer:
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
            )

            if not result.text:
                return

            # Calculate timing
            audio_duration = len(audio) / self.config.sample_rate
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
        speech_duration = speech_samples / self.config.sample_rate

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
        try:
            # Call ASR in thread pool to avoid blocking event loop
            # This is critical for slow models (e.g., CPU inference) to
            # prevent WebSocket keepalive ping timeouts
            result = await asyncio.to_thread(
                self._transcribe_fn,
                audio,
                self.config.language,
                self.config.model,
            )

            if not result.text:
                return

            # Add to assembler
            audio_duration = len(audio) / self.config.sample_rate
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

        elif isinstance(parsed, FlushMessage):
            # Flush VAD buffer
            remaining = self._vad.flush()
            if remaining is not None and len(remaining) > 0:
                await self._transcribe_and_send(remaining)

            # Flush audio buffer
            remaining = self._buffer.flush()
            if (
                remaining is not None
                and len(remaining) > self._vad.config.sample_rate * 0.1
            ):
                # Only process if > 100ms
                await self._transcribe_and_send(remaining)

        elif isinstance(parsed, EndMessage):
            # Graceful end
            await self._send_session_end()
            self._ended = True

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

        Creates S3 client and AudioRecorder/TranscriptRecorder instances.
        Flushes any buffered raw audio to the recorder.
        """
        if not self.config.store_audio and not self.config.store_transcript:
            return

        try:
            from dalston.common.s3 import get_s3_client
            from dalston.config import get_settings
            from dalston.realtime_sdk.audio_recorder import (
                AudioRecorder,
                TranscriptRecorder,
            )

            settings = get_settings()
            bucket = settings.s3_bucket

            if not bucket:
                logger.warning(
                    "storage_disabled_no_bucket",
                    msg="store_audio/store_transcript enabled but S3_BUCKET not set",
                )
                return

            # Create S3 client context manager (uses Settings from env vars)
            # We store the context manager to properly manage its lifecycle
            self._s3_context_manager = get_s3_client(settings)
            self._s3_client = await self._s3_context_manager.__aenter__()

            if self.config.store_audio:
                self._audio_recorder = AudioRecorder(
                    session_id=self.config.session_id,
                    s3_client=self._s3_client,
                    bucket=bucket,
                    sample_rate=self.config.sample_rate,
                )
                await self._audio_recorder.start()

                # Flush any buffered raw audio
                for chunk in self._raw_audio_buffer:
                    await self._audio_recorder.write(chunk)
                self._raw_audio_buffer.clear()

                logger.info(
                    "audio_recorder_initialized",
                    bucket=bucket,
                )

            if self.config.store_transcript:
                self._transcript_recorder = TranscriptRecorder(
                    session_id=self.config.session_id,
                    s3_client=self._s3_client,
                    bucket=bucket,
                )
                logger.info(
                    "transcript_recorder_initialized",
                    bucket=bucket,
                )

        except Exception as e:
            logger.error("storage_init_failed", error=str(e))
            # Continue without storage - don't fail the session
            self._storage_init_failed = True
            # Clear any buffered audio to free memory
            self._raw_audio_buffer.clear()
            self._raw_audio_buffer_bytes = 0

    async def _cleanup_storage(self) -> None:
        """Cleanup S3 client and abort any incomplete uploads."""
        if self._audio_recorder and not self._audio_recorder._finalized:
            try:
                await self._audio_recorder.abort()
            except Exception:
                pass

        if self._s3_context_manager:
            try:
                await self._s3_context_manager.__aexit__(None, None, None)
            except Exception:
                pass
            self._s3_context_manager = None
            self._s3_client = None

    async def _send_session_end(self) -> None:
        """Send session.end message."""
        # Flush any remaining audio
        remaining = self._vad.flush()
        if remaining is not None and len(remaining) > 0:
            await self._transcribe_and_send(remaining)

        segments = [
            SegmentInfo(start=s.start, end=s.end, text=s.text)
            for s in self._assembler.get_segments()
        ]

        # Finalize storage and get URIs
        audio_uri = None
        transcript_uri = None

        if self._audio_recorder:
            try:
                audio_uri = await self._audio_recorder.finalize()
                logger.info("audio_recorded", audio_uri=audio_uri)
            except Exception as e:
                logger.error("audio_finalize_failed", error=str(e))

        if self._transcript_recorder:
            try:
                # Build transcript data matching batch format
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
                transcript_uri = await self._transcript_recorder.save(transcript_data)
                logger.info("transcript_saved", transcript_uri=transcript_uri)
            except Exception as e:
                logger.error("transcript_save_failed", error=str(e))

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
