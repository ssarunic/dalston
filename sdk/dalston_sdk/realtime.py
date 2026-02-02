"""Real-time streaming transcription client for Dalston.

Provides both asynchronous (AsyncRealtimeSession) and synchronous (RealtimeSession)
clients for real-time streaming transcription via WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlencode

import websockets

from .exceptions import (
    AuthenticationError,
    ConnectionError,
    PermissionError,
    RateLimitError,
    RealtimeError,
)
from .types import (
    RealtimeError as RealtimeErrorData,
    RealtimeMessage,
    RealtimeMessageType,
    SessionBegin,
    SessionEnd,
    TranscriptFinal,
    TranscriptPartial,
    VADEvent,
    Word,
)


def _parse_message(data: dict[str, Any]) -> RealtimeMessage:
    """Parse a raw WebSocket message into typed message object."""
    msg_type = data.get("type", "")

    if msg_type == "session.begin":
        return RealtimeMessage(
            type=RealtimeMessageType.SESSION_BEGIN,
            data=SessionBegin(
                session_id=data["session_id"],
                model=data.get("model", ""),
                language=data.get("language", "auto"),
                sample_rate=data.get("sample_rate", 16000),
                encoding=data.get("encoding", "pcm_s16le"),
            ),
        )

    elif msg_type == "session.end":
        return RealtimeMessage(
            type=RealtimeMessageType.SESSION_END,
            data=SessionEnd(
                session_id=data["session_id"],
                total_audio_seconds=data.get("total_audio_seconds", 0.0),
                total_billed_seconds=data.get("total_billed_seconds"),
            ),
        )

    elif msg_type == "transcript.partial":
        return RealtimeMessage(
            type=RealtimeMessageType.TRANSCRIPT_PARTIAL,
            data=TranscriptPartial(
                text=data.get("text", ""),
                is_final=False,
            ),
        )

    elif msg_type == "transcript.final":
        words = None
        if data.get("words"):
            words = [
                Word(
                    text=w["text"],
                    start=w["start"],
                    end=w["end"],
                    confidence=w.get("confidence"),
                    speaker_id=w.get("speaker_id"),
                )
                for w in data["words"]
            ]

        return RealtimeMessage(
            type=RealtimeMessageType.TRANSCRIPT_FINAL,
            data=TranscriptFinal(
                text=data.get("text", ""),
                start=data.get("start", 0.0),
                end=data.get("end", 0.0),
                words=words,
                confidence=data.get("confidence"),
                speaker_id=data.get("speaker_id"),
            ),
        )

    elif msg_type == "vad.speech_start":
        return RealtimeMessage(
            type=RealtimeMessageType.VAD_SPEECH_START,
            data=VADEvent(
                type="speech_start",
                timestamp=data.get("timestamp", 0.0),
            ),
        )

    elif msg_type == "vad.speech_end":
        return RealtimeMessage(
            type=RealtimeMessageType.VAD_SPEECH_END,
            data=VADEvent(
                type="speech_end",
                timestamp=data.get("timestamp", 0.0),
            ),
        )

    elif msg_type == "error":
        return RealtimeMessage(
            type=RealtimeMessageType.ERROR,
            data=RealtimeErrorData(
                code=data.get("code", "unknown"),
                message=data.get("message", "Unknown error"),
                details=data.get("details"),
            ),
        )

    else:
        # Unknown message type, treat as error
        return RealtimeMessage(
            type=RealtimeMessageType.ERROR,
            data=RealtimeErrorData(
                code="unknown_message",
                message=f"Unknown message type: {msg_type}",
                details=data,
            ),
        )


class AsyncRealtimeSession:
    """Asynchronous WebSocket client for real-time transcription.

    Uses binary protocol for efficiency (raw PCM bytes, no base64 encoding).

    Example:
        ```python
        async with AsyncRealtimeSession(base_url="ws://localhost:8000") as session:
            await session.connect()

            # Send audio chunks
            async for chunk in audio_stream:
                await session.send_audio(chunk)

            # Receive transcripts
            async for message in session:
                if message.type == RealtimeMessageType.TRANSCRIPT_FINAL:
                    print(message.data.text)

            await session.close()
        ```
    """

    def __init__(
        self,
        base_url: str = "ws://localhost:8000",
        api_key: str | None = None,
        language: str = "auto",
        model: str = "fast",
        encoding: str = "pcm_s16le",
        sample_rate: int = 16000,
        enable_vad: bool = True,
        interim_results: bool = True,
        word_timestamps: bool = False,
    ) -> None:
        """Initialize the real-time session.

        Args:
            base_url: WebSocket URL of the Dalston server.
            api_key: Optional API key for authentication.
            language: Language code or "auto" for detection.
            model: Model variant ("fast" or "accurate").
            encoding: Audio encoding (pcm_s16le, pcm_f32le, mulaw, alaw).
            sample_rate: Audio sample rate in Hz.
            enable_vad: Enable voice activity detection events.
            interim_results: Send partial transcripts.
            word_timestamps: Include word-level timing.
        """
        # Convert http(s) to ws(s) if needed
        if base_url.startswith("http://"):
            base_url = "ws://" + base_url[7:]
        elif base_url.startswith("https://"):
            base_url = "wss://" + base_url[8:]

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.language = language
        self.model = model
        self.encoding = encoding
        self.sample_rate = sample_rate
        self.enable_vad = enable_vad
        self.interim_results = interim_results
        self.word_timestamps = word_timestamps

        self._ws: Any = None
        self._session_id: str | None = None
        self._connected = False

    def _build_url(self) -> str:
        """Build WebSocket URL with query parameters.

        Note: api_key is passed as a query parameter because WebSocket
        connections don't reliably support custom headers in all browsers
        and environments.
        """
        params: dict[str, str] = {
            "language": self.language,
            "model": self.model,
            "encoding": self.encoding,
            "sample_rate": str(self.sample_rate),
            "enable_vad": str(self.enable_vad).lower(),
            "interim_results": str(self.interim_results).lower(),
            "word_timestamps": str(self.word_timestamps).lower(),
        }

        # Pass API key as query parameter (WebSocket auth standard)
        if self.api_key:
            params["api_key"] = self.api_key

        query = urlencode(params)
        return f"{self.base_url}/v1/audio/transcriptions/stream?{query}"

    def _build_headers(self) -> dict[str, str]:
        """Build WebSocket headers.

        Note: Some WebSocket clients support custom headers. We send
        the API key in both places for maximum compatibility.
        """
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def session_id(self) -> str | None:
        """Get the current session ID."""
        return self._session_id

    @property
    def connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._ws is not None

    async def connect(self) -> SessionBegin:
        """Establish WebSocket connection.

        Returns:
            SessionBegin message with session configuration.

        Raises:
            AuthenticationError: If API key is invalid or missing.
            PermissionError: If API key lacks required scope.
            RateLimitError: If rate limit exceeded.
            ConnectionError: If connection fails.
            RealtimeError: If server returns an error.
        """
        url = self._build_url()
        headers = self._build_headers()

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers if headers else None,
                open_timeout=10,
                close_timeout=5,
            )
        except websockets.exceptions.InvalidStatusCode as e:
            # Handle HTTP-level errors (before WebSocket upgrade)
            if e.status_code == 401:
                raise AuthenticationError("Invalid or missing API key") from e
            elif e.status_code == 403:
                raise PermissionError("API key lacks required scope") from e
            elif e.status_code == 429:
                raise RateLimitError("Rate limit exceeded") from e
            raise ConnectionError(f"Failed to connect: {e}") from e
        except websockets.exceptions.ConnectionClosedError as e:
            # Handle WebSocket close codes for auth errors
            if e.code == 4001:
                raise AuthenticationError(e.reason or "Invalid API key") from e
            elif e.code == 4003:
                raise PermissionError(e.reason or "Missing required scope") from e
            elif e.code == 4029:
                raise RateLimitError(e.reason or "Rate limit exceeded") from e
            raise ConnectionError(f"Connection closed: {e}") from e
        except Exception as e:
            raise ConnectionError(f"Failed to connect: {e}") from e

        self._connected = True

        # Wait for session.begin message
        try:
            raw = await self._ws.recv()
            if isinstance(raw, bytes):
                raise RealtimeError("Unexpected binary message during handshake")

            data = json.loads(raw)
            message = _parse_message(data)

            if message.type == RealtimeMessageType.SESSION_BEGIN:
                self._session_id = message.data.session_id  # type: ignore
                return message.data  # type: ignore
            elif message.type == RealtimeMessageType.ERROR:
                error_data: RealtimeErrorData = message.data  # type: ignore
                raise RealtimeError(error_data.message, code=error_data.code)
            else:
                raise RealtimeError(f"Unexpected message type: {message.type}")

        except json.JSONDecodeError as e:
            raise RealtimeError(f"Invalid JSON from server: {e}") from e

    async def send_audio(self, audio: bytes) -> None:
        """Send raw audio bytes.

        Args:
            audio: Raw PCM audio bytes (no base64 encoding).

        Raises:
            RealtimeError: If not connected.
        """
        if not self._ws or not self._connected:
            raise RealtimeError("Not connected", code="not_connected")

        await self._ws.send(audio)

    async def flush(self) -> None:
        """Force processing of buffered audio.

        Useful when there's a pause in audio input and you want
        immediate results.
        """
        if not self._ws or not self._connected:
            raise RealtimeError("Not connected", code="not_connected")

        await self._ws.send(json.dumps({"type": "flush"}))

    async def close(self) -> SessionEnd | None:
        """Gracefully close the session.

        Returns:
            SessionEnd message with session statistics, or None if
            connection was already closed.
        """
        if not self._ws:
            return None

        try:
            # Send end message
            await self._ws.send(json.dumps({"type": "end"}))

            # Wait for session.end message
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue

                data = json.loads(raw)
                message = _parse_message(data)

                if message.type == RealtimeMessageType.SESSION_END:
                    return message.data  # type: ignore

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connected = False
            await self._ws.close()
            self._ws = None

        return None

    async def __aiter__(self) -> AsyncIterator[RealtimeMessage]:
        """Iterate over incoming messages.

        Yields:
            RealtimeMessage objects for each server message.
        """
        if not self._ws:
            raise RealtimeError("Not connected", code="not_connected")

        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    # Binary messages not expected from server
                    continue

                data = json.loads(raw)
                yield _parse_message(data)

        except websockets.exceptions.ConnectionClosed:
            self._connected = False

    async def __aenter__(self) -> "AsyncRealtimeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._connected:
            await self.close()


class RealtimeSession:
    """Synchronous wrapper for real-time transcription with callbacks.

    Runs the async session in a background thread and dispatches
    messages to registered callbacks.

    Example:
        ```python
        session = RealtimeSession(base_url="ws://localhost:8000")

        @session.on_final
        def handle_final(transcript: TranscriptFinal):
            print(f"Final: {transcript.text}")

        @session.on_vad_start
        def handle_vad_start(event: VADEvent):
            print("Speech started")

        session.connect()

        # Send audio in main thread
        for chunk in audio_chunks:
            session.send_audio(chunk)

        session.close()
        ```
    """

    def __init__(
        self,
        base_url: str = "ws://localhost:8000",
        api_key: str | None = None,
        language: str = "auto",
        model: str = "fast",
        encoding: str = "pcm_s16le",
        sample_rate: int = 16000,
        enable_vad: bool = True,
        interim_results: bool = True,
        word_timestamps: bool = False,
    ) -> None:
        """Initialize the real-time session.

        Args:
            base_url: WebSocket URL of the Dalston server.
            api_key: Optional API key for authentication.
            language: Language code or "auto" for detection.
            model: Model variant ("fast" or "accurate").
            encoding: Audio encoding (pcm_s16le, pcm_f32le, mulaw, alaw).
            sample_rate: Audio sample rate in Hz.
            enable_vad: Enable voice activity detection events.
            interim_results: Send partial transcripts.
            word_timestamps: Include word-level timing.
        """
        self._async_session = AsyncRealtimeSession(
            base_url=base_url,
            api_key=api_key,
            language=language,
            model=model,
            encoding=encoding,
            sample_rate=sample_rate,
            enable_vad=enable_vad,
            interim_results=interim_results,
            word_timestamps=word_timestamps,
        )

        self._callbacks: dict[str, list[Callable[..., None]]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._session_begin: SessionBegin | None = None
        self._session_end: SessionEnd | None = None

    @property
    def session_id(self) -> str | None:
        """Get the current session ID."""
        return self._async_session.session_id

    @property
    def connected(self) -> bool:
        """Check if connected."""
        return self._async_session.connected

    def on_partial(self, fn: Callable[[TranscriptPartial], None]) -> Callable[[TranscriptPartial], None]:
        """Register callback for transcript.partial messages.

        Args:
            fn: Callback function receiving TranscriptPartial.

        Returns:
            The callback function (for decorator use).
        """
        self._callbacks["partial"].append(fn)
        return fn

    def on_final(self, fn: Callable[[TranscriptFinal], None]) -> Callable[[TranscriptFinal], None]:
        """Register callback for transcript.final messages.

        Args:
            fn: Callback function receiving TranscriptFinal.

        Returns:
            The callback function (for decorator use).
        """
        self._callbacks["final"].append(fn)
        return fn

    def on_vad_start(self, fn: Callable[[VADEvent], None]) -> Callable[[VADEvent], None]:
        """Register callback for vad.speech_start events.

        Args:
            fn: Callback function receiving VADEvent.

        Returns:
            The callback function (for decorator use).
        """
        self._callbacks["vad_start"].append(fn)
        return fn

    def on_vad_end(self, fn: Callable[[VADEvent], None]) -> Callable[[VADEvent], None]:
        """Register callback for vad.speech_end events.

        Args:
            fn: Callback function receiving VADEvent.

        Returns:
            The callback function (for decorator use).
        """
        self._callbacks["vad_end"].append(fn)
        return fn

    def on_error(self, fn: Callable[[RealtimeErrorData], None]) -> Callable[[RealtimeErrorData], None]:
        """Register callback for error messages.

        Args:
            fn: Callback function receiving RealtimeErrorData.

        Returns:
            The callback function (for decorator use).
        """
        self._callbacks["error"].append(fn)
        return fn

    def _dispatch(self, message: RealtimeMessage) -> None:
        """Dispatch message to registered callbacks."""
        if message.type == RealtimeMessageType.TRANSCRIPT_PARTIAL:
            for cb in self._callbacks["partial"]:
                cb(message.data)
        elif message.type == RealtimeMessageType.TRANSCRIPT_FINAL:
            for cb in self._callbacks["final"]:
                cb(message.data)
        elif message.type == RealtimeMessageType.VAD_SPEECH_START:
            for cb in self._callbacks["vad_start"]:
                cb(message.data)
        elif message.type == RealtimeMessageType.VAD_SPEECH_END:
            for cb in self._callbacks["vad_end"]:
                cb(message.data)
        elif message.type == RealtimeMessageType.ERROR:
            for cb in self._callbacks["error"]:
                cb(message.data)

    async def _receive_loop(self) -> None:
        """Background loop to receive and dispatch messages."""
        try:
            async for message in self._async_session:
                if self._stop_event.is_set():
                    break
                self._dispatch(message)
        except Exception:
            pass

    def _run_loop(self) -> None:
        """Run the event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._receive_loop())
        finally:
            self._loop.close()

    def connect(self, timeout: float = 10.0) -> SessionBegin:
        """Connect to the server.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            SessionBegin message with session configuration.

        Raises:
            ConnectionError: If connection fails.
            RealtimeError: If server returns an error.
        """
        # Create event loop for connection
        loop = asyncio.new_event_loop()
        try:
            self._session_begin = loop.run_until_complete(
                asyncio.wait_for(self._async_session.connect(), timeout)
            )
        finally:
            loop.close()

        # Start background thread for receiving messages
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        return self._session_begin

    def send_audio(self, audio: bytes) -> None:
        """Send raw audio bytes.

        Args:
            audio: Raw PCM audio bytes.

        Raises:
            RealtimeError: If not connected.
        """
        if not self._async_session.connected:
            raise RealtimeError("Not connected", code="not_connected")

        # Run send in a temporary event loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._async_session.send_audio(audio))
        finally:
            loop.close()

    def flush(self) -> None:
        """Force processing of buffered audio."""
        if not self._async_session.connected:
            raise RealtimeError("Not connected", code="not_connected")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._async_session.flush())
        finally:
            loop.close()

    def close(self, timeout: float = 5.0) -> SessionEnd | None:
        """Close the session.

        Args:
            timeout: Timeout for graceful close.

        Returns:
            SessionEnd message with session statistics.
        """
        self._stop_event.set()

        loop = asyncio.new_event_loop()
        try:
            self._session_end = loop.run_until_complete(
                asyncio.wait_for(self._async_session.close(), timeout)
            )
        except asyncio.TimeoutError:
            pass
        finally:
            loop.close()

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        return self._session_end

    def __enter__(self) -> "RealtimeSession":
        return self

    def __exit__(self, *args: Any) -> None:
        if self.connected:
            self.close()
