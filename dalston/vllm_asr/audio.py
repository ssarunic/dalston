"""Audio helpers shared by vLLM-ASR engines.

Realtime workers produce in-memory numpy audio. vLLM's current multimodal
audio chat path consumes file URLs, so we normalize numpy buffers and write
temporary WAV files as the bridge format.

The implementations live in :mod:`dalston.engine_sdk.audio` — this module
keeps the original import path stable for vLLM-specific code and tests.
"""

from __future__ import annotations

from dalston.engine_sdk.audio import (
    normalize_mono_audio,
    temporary_wav_file,
    write_wav_file,
)

__all__ = ["normalize_mono_audio", "temporary_wav_file", "write_wav_file"]
