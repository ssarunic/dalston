"""Generic adapter for audio LLM transcription via vLLM.

Works with any vLLM-compatible audio model. No model-specific logic —
the prompt format is universal across audio LLMs (Voxtral, Qwen2-Audio,
etc.).
"""

from pathlib import Path
from typing import Any

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TimestampGranularity,
    Transcript,
    TranscriptSegment,
)


class AudioLLMAdapter:
    """Builds prompts and parses output for any vLLM audio model."""

    def build_messages(
        self,
        audio_path: Path,
        language: str | None = None,
        vocabulary: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build chat messages with audio input for vLLM."""
        if language and language != "auto":
            prompt = (
                f"Transcribe this audio in {language}. "
                "Output only the transcription text."
            )
        else:
            prompt = (
                "Transcribe this audio accurately. Output only the transcription text."
            )

        if vocabulary:
            terms = ", ".join(vocabulary)
            prompt += f" Pay special attention to these terms: {terms}."

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
                        "text": prompt,
                    },
                ],
            },
        ]

    def parse_output(
        self,
        raw_text: str,
        language: str | None = None,
        duration: float | None = None,
    ) -> Transcript:
        """Parse model output to a single-segment Transcript."""
        text = raw_text.strip()
        effective_language = language if language and language != "auto" else "en"
        segment_end = max(duration or 0.0, 0.001 if text else 0.0)

        return Transcript(
            text=text,
            segments=[
                TranscriptSegment(
                    start=0.0,
                    end=segment_end,
                    text=text,
                ),
            ],
            language=effective_language,
            language_confidence=0.9,
            timestamp_granularity=TimestampGranularity.SEGMENT,
            alignment_method=AlignmentMethod.UNKNOWN,
            engine_id="vllm-asr",
            warnings=[],
        )

    def get_sampling_kwargs(self) -> dict[str, Any]:
        """Return sampling parameters for vLLM inference."""
        return {
            "temperature": 0.0,
            "max_tokens": 4096,
        }


# Module-level singleton — adapter is stateless.
adapter = AudioLLMAdapter()
