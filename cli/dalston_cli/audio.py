"""Audio capture module for Dalston CLI.

Provides cross-platform microphone capture using sounddevice (PortAudio).
"""

from __future__ import annotations

import queue
from typing import Any

import sounddevice as sd


class MicrophoneStream:
    """Cross-platform microphone capture using sounddevice/PortAudio.

    Captures audio from the default or specified input device and
    provides chunks via a blocking read() method.

    Example:
        ```python
        with MicrophoneStream(sample_rate=16000) as mic:
            while True:
                chunk = mic.read()
                # Process chunk...
        ```
    """

    def __init__(
        self,
        device: int | None = None,
        sample_rate: int = 16000,
        chunk_ms: int = 100,
    ):
        """Initialize microphone stream.

        Args:
            device: Audio device index, or None for default.
            sample_rate: Sample rate in Hz (default: 16000).
            chunk_ms: Chunk duration in milliseconds (default: 100).
        """
        self.device = device
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_ms / 1000)
        self._stream: sd.InputStream | None = None
        self._queue: queue.Queue[bytes] = queue.Queue()

    def __enter__(self) -> "MicrophoneStream":
        """Start audio capture."""
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.chunk_size,
            callback=self._callback,
        )
        self._stream.start()
        return self

    def __exit__(self, *args: Any) -> None:
        """Stop audio capture."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(
        self,
        indata: Any,
        frames: int,
        time: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback for audio data from sounddevice."""
        if status:
            # Could log status warnings here
            pass
        self._queue.put(indata.tobytes())

    def read(self, timeout: float | None = None) -> bytes:
        """Read next audio chunk.

        Blocks until audio data is available.

        Args:
            timeout: Maximum time to wait in seconds, or None for no timeout.

        Returns:
            Raw PCM audio bytes (int16, mono).

        Raises:
            queue.Empty: If timeout expires before data is available.
        """
        return self._queue.get(timeout=timeout)

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        """List available audio input devices.

        Returns:
            List of device info dictionaries with keys:
            - index: Device index
            - name: Device name
            - channels: Number of input channels
            - sample_rate: Default sample rate
        """
        devices = sd.query_devices()
        return [
            {
                "index": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "sample_rate": d["default_samplerate"],
            }
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]

    @staticmethod
    def get_default_device() -> dict[str, Any] | None:
        """Get default input device info.

        Returns:
            Device info dictionary, or None if no default device.
        """
        try:
            idx = sd.default.device[0]
            if idx is not None:
                d = sd.query_devices(idx)
                return {
                    "index": idx,
                    "name": d["name"],
                    "channels": d["max_input_channels"],
                    "sample_rate": d["default_samplerate"],
                }
        except Exception:
            pass
        return None


def resolve_device(device_str: str) -> int:
    """Resolve device by name or index.

    Args:
        device_str: Device index as string, or partial device name.

    Returns:
        Device index.

    Raises:
        ValueError: If device not found.
    """
    # Try as integer first
    try:
        return int(device_str)
    except ValueError:
        pass

    # Search by name
    devices = MicrophoneStream.list_devices()
    for d in devices:
        if device_str.lower() in d["name"].lower():
            return d["index"]

    raise ValueError(f"Device not found: {device_str}")
