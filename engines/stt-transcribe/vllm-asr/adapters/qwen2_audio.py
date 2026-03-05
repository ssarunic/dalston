"""Qwen2-Audio adapter for Alibaba's audio LLM.

Handles prompt construction and output parsing for:
- Qwen/Qwen2-Audio-7B-Instruct

Qwen2-Audio processes audio via a dedicated audio encoder and
produces text transcriptions. Like Voxtral, it does not produce
word-level timestamps.
"""

from pathlib import Path
from typing import Any

import structlog

from dalston.common.pipeline_types import (
    Segment,
    TimestampGranularity,
    TranscribeOutput,
)

from .base import AudioLLMAdapter

logger = structlog.get_logger()


class Qwen2AudioAdapter(AudioLLMAdapter):
    """Adapter for Qwen2-Audio models.

    Qwen2-Audio uses a chat prompt format with audio embedded via
    audio_url. The model supports multilingual transcription and
    audio understanding tasks.
    """

    def build_messages(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build Qwen2-Audio chat messages.

        Args:
            audio_path: Path to the audio file
            language: Language code or None for auto-detect

        Returns:
            Chat messages list for vLLM
        """
        if language and language != "auto":
            prompt_text = (
                f"Please transcribe the following audio in {language}. "
                "Output only the transcription text."
            )
        else:
            prompt_text = (
                "Please transcribe the following audio. "
                "Output only the transcription text."
            )

        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio_url",
                        "audio_url": {"url": f"file://{audio_path}"},
                    },
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                ],
            },
        ]

    def parse_output(
        self,
        raw_text: str,
        language: str | None = None,
    ) -> TranscribeOutput:
        """Parse Qwen2-Audio output to TranscribeOutput.

        Args:
            raw_text: Raw text from the model
            language: Language used for transcription

        Returns:
            TranscribeOutput with text in a single segment
        """
        text = raw_text.strip()

        effective_language = language if language and language != "auto" else "en"

        return TranscribeOutput(
            text=text,
            segments=[
                Segment(
                    start=0.0,
                    end=0.0,
                    text=text,
                ),
            ],
            language=effective_language,
            language_confidence=0.9,
            timestamp_granularity_requested=TimestampGranularity.SEGMENT,
            timestamp_granularity_actual=TimestampGranularity.SEGMENT,
            runtime="vllm-asr",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

    def get_sampling_kwargs(self) -> dict[str, Any]:
        """Qwen2-Audio sampling parameters."""
        return {
            "temperature": 0.0,
            "max_tokens": 4096,
        }
