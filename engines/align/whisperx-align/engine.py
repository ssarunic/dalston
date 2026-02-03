"""WhisperX alignment engine for word-level timestamps.

Uses wav2vec2-based forced alignment via WhisperX to produce
accurate word boundaries from transcription segments.
"""

import logging
from pathlib import Path
from typing import Any

import whisperx

from dalston.engine_sdk import Engine, TaskInput, TaskOutput

logger = logging.getLogger(__name__)


class WhisperXAlignEngine(Engine):
    """WhisperX alignment engine for word-level timestamps.

    Loads language-specific wav2vec2 alignment models lazily and caches
    them for subsequent requests. Falls back gracefully to transcription
    timestamps if alignment fails or the language is unsupported.
    """

    def __init__(self) -> None:
        super().__init__()
        # Cache alignment models by language code
        self._align_models: dict[str, tuple[Any, dict]] = {}

        # Detect device
        self._device, self._compute_type = self._detect_device()
        logger.info(f"Detected device: {self._device}, compute_type: {self._compute_type}")

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        return "cpu", "float32"

    def _load_align_model(self, language: str) -> tuple[Any, dict] | None:
        """Load alignment model for a specific language.

        Args:
            language: ISO 639-1 language code (e.g., 'en', 'es', 'fr')

        Returns:
            Tuple of (model, metadata) or None if language unsupported
        """
        if language in self._align_models:
            logger.debug(f"Using cached alignment model for '{language}'")
            return self._align_models[language]

        logger.info(f"Loading alignment model for language '{language}' on {self._device}")

        try:
            model, metadata = whisperx.load_align_model(
                language_code=language,
                device=self._device,
            )
            self._align_models[language] = (model, metadata)
            logger.info(f"Alignment model for '{language}' loaded successfully")
            return model, metadata

        except Exception as e:
            logger.warning(f"Failed to load alignment model for '{language}': {e}")
            return None

    def process(self, input: TaskInput) -> TaskOutput:
        """Align transcription segments to get word-level timestamps.

        Args:
            input: Task input with audio path and previous transcription output

        Returns:
            TaskOutput with aligned segments or original segments if alignment fails
        """
        audio_path = input.audio_path
        config = input.config

        # Get transcription output from previous stage
        transcribe_output = input.previous_outputs.get("transcribe", {})
        if not transcribe_output:
            raise ValueError("Missing 'transcribe' in previous_outputs")

        text = transcribe_output.get("text", "")
        segments = transcribe_output.get("segments", [])
        language = transcribe_output.get("language", "en")

        logger.info(f"Aligning {len(segments)} segments for language '{language}'")
        logger.info(f"Audio path: {audio_path}")

        # Try to load alignment model for this language
        model_result = self._load_align_model(language)

        if model_result is None:
            # Graceful degradation: return original segments with warning
            logger.warning(
                f"No alignment model for language '{language}', "
                "returning original transcription timestamps"
            )
            return self._fallback_output(text, segments, language, reason=f"No alignment model available for language '{language}'")

        model, metadata = model_result

        try:
            # Load audio using whisperx
            audio = whisperx.load_audio(str(audio_path))

            # Prepare segments for alignment (whisperx expects specific format)
            align_segments = self._prepare_segments_for_alignment(segments)

            # Perform alignment
            result = whisperx.align(
                align_segments,
                model,
                metadata,
                audio,
                device=self._device,
                return_char_alignments=config.get("return_char_alignments", False),
            )

            aligned_segments = result.get("segments", [])

            # Normalize the output format
            output_segments = self._normalize_aligned_segments(aligned_segments)

            logger.info(f"Alignment complete: {len(output_segments)} segments with word timestamps")

            return TaskOutput(
                data={
                    "text": text,
                    "segments": output_segments,
                    "language": language,
                    "word_timestamps": True,
                }
            )

        except Exception as e:
            # Graceful degradation on alignment failure
            logger.error(f"Alignment failed: {e}", exc_info=True)
            return self._fallback_output(
                text, segments, language,
                reason=f"Alignment failed: {str(e)}"
            )

    def _prepare_segments_for_alignment(self, segments: list[dict]) -> list[dict]:
        """Prepare segments for whisperx alignment.

        WhisperX expects segments with 'start', 'end', 'text' keys.

        Args:
            segments: Raw segments from transcription

        Returns:
            Segments formatted for whisperx.align()
        """
        prepared = []
        for seg in segments:
            prepared.append({
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", ""),
            })
        return prepared

    def _normalize_aligned_segments(self, aligned_segments: list[dict]) -> list[dict]:
        """Normalize aligned segments to standard output format.

        Ensures consistent field names and structure.

        Args:
            aligned_segments: Raw output from whisperx.align()

        Returns:
            Normalized segments with words array
        """
        normalized = []
        total_words = 0
        filtered_words = 0

        for seg in aligned_segments:
            segment = {
                "start": round(seg.get("start", 0.0), 3),
                "end": round(seg.get("end", 0.0), 3),
                "text": seg.get("text", ""),
            }

            # Process word-level alignments
            words = seg.get("words", [])
            if words:
                total_words += len(words)
                valid_words = []
                for w in words:
                    word_text = w.get("word", "").strip()
                    if word_text:
                        valid_words.append({
                            "word": word_text,
                            "start": round(w.get("start", 0.0), 3),
                            "end": round(w.get("end", 0.0), 3),
                            "confidence": round(w.get("score", 0.0), 3),
                        })
                    else:
                        filtered_words += 1
                segment["words"] = valid_words if valid_words else None
            else:
                segment["words"] = None

            normalized.append(segment)

        if filtered_words > 0:
            logger.debug(
                f"Filtered {filtered_words}/{total_words} empty words during normalization"
            )

        return normalized

    def _fallback_output(
        self,
        text: str,
        segments: list[dict],
        language: str,
        reason: str,
    ) -> TaskOutput:
        """Create fallback output when alignment fails.

        Returns the original transcription segments unchanged,
        with a warning included in the output.

        Args:
            text: Full transcript text
            segments: Original segments from transcription
            language: Detected language code
            reason: Why alignment failed

        Returns:
            TaskOutput with original segments and warning
        """
        return TaskOutput(
            data={
                "text": text,
                "segments": segments,
                "language": language,
                "word_timestamps": False,
                "warning": {
                    "stage": "align",
                    "status": "failed",
                    "fallback": "transcription_timestamps",
                    "reason": reason,
                },
            }
        )

    def health_check(self) -> dict[str, Any]:
        """Return health status including device info."""
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch
            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        return {
            "status": "healthy",
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cached_languages": list(self._align_models.keys()),
        }


if __name__ == "__main__":
    engine = WhisperXAlignEngine()
    engine.run()
