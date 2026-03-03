"""Voxtral-specific adapter for Mistral's audio LLMs.

Handles prompt construction and output parsing for:
- mistralai/Voxtral-Mini-3B-2507
- mistralai/Voxtral-Small-24B-2507

Voxtral models produce plain text transcriptions without timestamps.
Use the alignment stage if word-level timing is needed.
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

# Language-specific transcription prompts
_LANGUAGE_PROMPTS: dict[str | None, str] = {
    "en": "Transcribe this audio in English.",
    "es": "Transcribe this audio in Spanish.",
    "fr": "Transcribe this audio in French.",
    "de": "Transcribe this audio in German.",
    "pt": "Transcribe this audio in Portuguese.",
    "hi": "Transcribe this audio in Hindi.",
    "nl": "Transcribe this audio in Dutch.",
    "it": "Transcribe this audio in Italian.",
    None: "Transcribe this audio accurately.",
}

SUPPORTED_LANGUAGES = ["en", "es", "fr", "pt", "hi", "de", "nl", "it"]


class VoxtralAdapter(AudioLLMAdapter):
    """Adapter for Mistral Voxtral audio models.

    Voxtral uses a chat-style prompt with audio content embedded as
    a special audio URL. The model produces clean text output without
    timestamps or special formatting.
    """

    def build_messages(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build Voxtral chat messages with audio.

        Uses vLLM's multi-modal input format with audio_url pointing
        to the local file path.

        Args:
            audio_path: Path to the audio file
            language: Language code or None for auto-detect

        Returns:
            Chat messages list for vLLM
        """
        prompt_text = _LANGUAGE_PROMPTS.get(language, _LANGUAGE_PROMPTS[None])

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
        """Parse Voxtral output to TranscribeOutput.

        Voxtral returns plain text without timestamps. A single segment
        is created covering the entire transcription.

        Args:
            raw_text: Raw text from the model
            language: Language used for transcription

        Returns:
            TranscribeOutput with text in a single segment
        """
        text = raw_text.strip()

        # Determine effective language
        effective_language = language if language and language != "auto" else "en"
        if effective_language not in SUPPORTED_LANGUAGES:
            effective_language = "en"

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
            engine_id="vllm-asr",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

    def get_sampling_kwargs(self) -> dict[str, Any]:
        """Voxtral sampling parameters."""
        return {
            "temperature": 0.0,
            "max_tokens": 4096,
        }
