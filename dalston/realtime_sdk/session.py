"""Session handler for real-time transcription.

Manages a single WebSocket transcription session, coordinating
audio buffering, VAD, ASR, and transcript assembly.
"""

from __future__ import annotations

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
    VADSpeechEndMessage,
    VADSpeechStartMessage,
    WordInfo,
    parse_client_message,
)
from dalston.realtime_sdk.vad import VADConfig, VADProcessor

if TYPE_CHECKING:
    from websockets import WebSocketServerProtocol

logger = structlog.get_logger()


@dataclass
class SessionConfig:
    """Session configuration from connection parameters.

    Attributes:
        session_id: Unique session identifier
        language: Language code or "auto"
        model: Model variant ("fast" or "accurate")
        encoding: Audio encoding
        sample_rate: Expected sample rate
        channels: Number of audio channels
        enable_vad: Whether VAD events are sent to client
        interim_results: Whether partial transcripts are sent
        word_timestamps: Whether word-level timing is included
    """

    session_id: str
    language: str = "auto"
    model: str = "fast"
    encoding: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    enable_vad: bool = True
    interim_results: bool = True
    word_timestamps: bool = False


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

    def __init__(
        self,
        websocket: WebSocketServerProtocol,
        config: SessionConfig,
        transcribe_fn: TranscribeCallback,
        on_session_end: Callable[[str, float, str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize session handler.

        Args:
            websocket: WebSocket connection
            config: Session configuration
            transcribe_fn: Callback to engine's transcribe method
            on_session_end: Optional async callback when session ends
        """
        self.websocket = websocket
        self.config = config
        self._transcribe_fn = transcribe_fn
        self._on_session_end = on_session_end

        # Initialize components
        self._buffer = AudioBuffer(
            sample_rate=config.sample_rate,
            encoding=config.encoding,
        )
        self._vad = VADProcessor(VADConfig(sample_rate=config.sample_rate))
        self._assembler = TranscriptAssembler()

        # Session state
        self._started_at = time.time()
        self._error: str | None = None
        self._ended = False

    async def run(self) -> None:
        """Main session processing loop.

        Sends session.begin, processes messages, sends session.end.
        """
        # Send session.begin
        await self._send_session_begin()

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
            logger.exception("session_error", session_id=self.config.session_id, error=str(e))
            await self._send_error(
                ErrorCode.INTERNAL_ERROR,
                f"Internal error: {e}",
                recoverable=False,
            )

        # Send session.end if not already sent
        if not self._ended:
            await self._send_session_end()

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
            if self.config.enable_vad:
                await self._send(
                    VADSpeechStartMessage(timestamp=self._assembler.current_time)
                )

        elif vad_result.event == "speech_end":
            if self.config.enable_vad:
                await self._send(
                    VADSpeechEndMessage(timestamp=self._assembler.current_time)
                )

            # Transcribe if we have speech audio
            if vad_result.speech_audio is not None and len(vad_result.speech_audio) > 0:
                await self._transcribe_and_send(vad_result.speech_audio)

    async def _transcribe_and_send(self, audio: np.ndarray) -> None:
        """Transcribe audio and send result.

        Args:
            audio: Speech audio to transcribe
        """
        try:
            # Call ASR
            result = self._transcribe_fn(
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

        await self._send(
            SessionEndMessage(
                session_id=self.config.session_id,
                total_duration=self.get_duration(),
                total_speech_duration=self._assembler.current_time,
                transcript=self._assembler.get_full_transcript(),
                segments=segments,
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
