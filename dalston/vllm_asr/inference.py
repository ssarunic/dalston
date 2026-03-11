"""Shared inference helpers for audio LLMs served by vLLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from dalston.common.pipeline_types import Transcript

from .adapters import get_adapter
from .audio import temporary_wav_file


def transcribe_audio_path(
    llm: Any,
    runtime_model_id: str,
    audio_path: Path,
    language: str | None,
) -> tuple[str, Transcript]:
    """Run one vLLM audio chat inference and parse it into ``Transcript``."""
    adapter = get_adapter(runtime_model_id)
    messages = adapter.build_messages(audio_path=audio_path, language=language)

    from vllm import SamplingParams

    sampling_params = SamplingParams(**adapter.get_sampling_kwargs())
    outputs = llm.chat(messages=messages, sampling_params=sampling_params)
    raw_text = outputs[0].outputs[0].text
    transcript = adapter.parse_output(raw_text, language)
    return raw_text, transcript


def transcribe_audio_array(
    llm: Any,
    runtime_model_id: str,
    audio: np.ndarray,
    language: str | None,
    sample_rate: int = 16000,
) -> tuple[str, Transcript]:
    """Run vLLM audio inference from an in-memory numpy waveform."""
    with temporary_wav_file(audio=audio, sample_rate=sample_rate) as audio_path:
        return transcribe_audio_path(
            llm=llm,
            runtime_model_id=runtime_model_id,
            audio_path=audio_path,
            language=language,
        )
