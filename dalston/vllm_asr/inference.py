"""Shared inference helpers for audio LLMs served by vLLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from dalston.common.pipeline_types import Transcript

from .adapter import adapter
from .audio import temporary_wav_file


def transcribe_audio_path(
    llm: Any,
    audio_path: Path,
    language: str | None,
    vocabulary: list[str] | None = None,
) -> tuple[str, Transcript]:
    """Run one vLLM audio chat inference and parse it into ``Transcript``."""
    messages = adapter.build_messages(
        audio_path=audio_path, language=language, vocabulary=vocabulary
    )

    from vllm import SamplingParams

    sampling_params = SamplingParams(**adapter.get_sampling_kwargs())
    outputs = llm.chat(messages=messages, sampling_params=sampling_params)
    raw_text = outputs[0].outputs[0].text

    audio_duration: float | None = None
    try:
        import soundfile as sf

        info = sf.info(str(audio_path))
        if info.samplerate and info.frames:
            audio_duration = float(info.frames) / float(info.samplerate)
    except Exception:
        audio_duration = None

    transcript = adapter.parse_output(raw_text, language, duration=audio_duration)
    return raw_text, transcript


def transcribe_audio_array(
    llm: Any,
    audio: np.ndarray,
    language: str | None,
    sample_rate: int = 16000,
    vocabulary: list[str] | None = None,
) -> tuple[str, Transcript]:
    """Run vLLM audio inference from an in-memory numpy waveform.

    Callers should execute this in a worker thread (``asyncio.to_thread``)
    when invoked from async code because vLLM chat inference is blocking.
    """
    with temporary_wav_file(audio=audio, sample_rate=sample_rate) as audio_path:
        return transcribe_audio_path(
            llm=llm,
            audio_path=audio_path,
            language=language,
            vocabulary=vocabulary,
        )
