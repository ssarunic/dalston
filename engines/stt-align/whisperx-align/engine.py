"""WhisperX alignment engine for word-level timestamps.

Uses wav2vec2-based forced alignment via WhisperX to produce
accurate word boundaries from transcription segments.
"""

from typing import Any

import whisperx

from dalston.engine_sdk import (
    AlignmentMethod,
    AlignOutput,
    Engine,
    Segment,
    TaskInput,
    TaskOutput,
    TimestampGranularity,
    Word,
)


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
        self.logger.info(
            "detected_device", device=self._device, compute_type=self._compute_type
        )

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
            self.logger.debug("using_cached_alignment_model", language=language)
            return self._align_models[language]

        self.logger.info(
            "loading_alignment_model", language=language, device=self._device
        )

        try:
            model, metadata = whisperx.load_align_model(
                language_code=language,
                device=self._device,
            )
            self._align_models[language] = (model, metadata)
            self.logger.info("alignment_model_loaded_successfully", language=language)
            return model, metadata

        except Exception as e:
            self.logger.warning(
                "failed_to_load_alignment_model", language=language, error=str(e)
            )
            return None

    def process(self, input: TaskInput) -> TaskOutput:
        """Align transcription segments to get word-level timestamps.

        Args:
            input: Task input with audio path and previous transcription output

        Returns:
            TaskOutput with AlignOutput containing aligned segments
        """
        audio_path = input.audio_path
        config = input.config

        # Get transcription output from previous stage (try typed first, fall back to raw)
        transcribe_output = input.get_transcribe_output()
        if transcribe_output:
            text = transcribe_output.text
            raw_segments = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in transcribe_output.segments
            ]
            language = transcribe_output.language
        else:
            # Fall back to raw dict access
            raw_output = input.get_raw_output("transcribe")
            if not raw_output:
                raise ValueError("Missing 'transcribe' in previous_outputs")
            text = raw_output.get("text", "")
            raw_segments = raw_output.get("segments", [])
            language = raw_output.get("language", "en")

        self.logger.info(
            "aligning_segments", segment_count=len(raw_segments), language=language
        )
        self.logger.info("audio_path", audio_path=str(audio_path))

        # Try to load alignment model for this language
        model_result = self._load_align_model(language)

        if model_result is None:
            # Graceful degradation: return original segments with warning
            self.logger.warning(
                "no_alignment_model",
                language=language,
                fallback="original_transcription_timestamps",
            )
            return self._fallback_output(
                text,
                raw_segments,
                language,
                reason=f"No alignment model available for language '{language}'",
            )

        model, metadata = model_result

        try:
            # Load audio using whisperx
            audio = whisperx.load_audio(str(audio_path))

            # Prepare segments for alignment (whisperx expects specific format)
            align_segments = self._prepare_segments_for_alignment(raw_segments)

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
            output_segments, stats = self._normalize_aligned_segments(aligned_segments)

            # Calculate average alignment confidence
            all_words: list[Word] = []
            for seg in output_segments:
                if seg.words:
                    all_words.extend(seg.words)

            confidences = [w.confidence for w in all_words if w.confidence is not None]
            alignment_confidence = (
                sum(confidences) / len(confidences) if confidences else None
            )

            aligned_count = len(all_words)
            unaligned_count = stats["filtered_words"]
            total_count = aligned_count + unaligned_count
            unaligned_ratio = unaligned_count / total_count if total_count > 0 else 0.0

            self.logger.info(
                "alignment_complete",
                segment_count=len(output_segments),
                aligned_words=aligned_count,
                unaligned_words=unaligned_count,
            )

            output = AlignOutput(
                text=text,
                segments=output_segments,
                language=language,
                word_timestamps=True,
                alignment_confidence=(
                    round(alignment_confidence, 3) if alignment_confidence else None
                ),
                unaligned_words=[f"word_{i}" for i in range(unaligned_count)],
                unaligned_ratio=round(unaligned_ratio, 3),
                granularity_achieved=TimestampGranularity.WORD,
                engine_id="whisperx-align",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskOutput(data=output)

        except Exception as e:
            # Graceful degradation on alignment failure
            self.logger.error("alignment_failed", error=str(e), exc_info=True)
            return self._fallback_output(
                text, raw_segments, language, reason=f"Alignment failed: {str(e)}"
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
            prepared.append(
                {
                    "start": seg.get("start", 0.0),
                    "end": seg.get("end", 0.0),
                    "text": seg.get("text", ""),
                }
            )
        return prepared

    def _normalize_aligned_segments(
        self, aligned_segments: list[dict]
    ) -> tuple[list[Segment], dict]:
        """Normalize aligned segments to typed output format.

        Args:
            aligned_segments: Raw output from whisperx.align()

        Returns:
            Tuple of (normalized segments, statistics dict)
        """
        normalized: list[Segment] = []
        total_words = 0
        filtered_words = 0

        for seg in aligned_segments:
            # Process word-level alignments
            raw_words = seg.get("words", [])
            words: list[Word] | None = None

            if raw_words:
                total_words += len(raw_words)
                valid_words: list[Word] = []
                for w in raw_words:
                    word_text = w.get("word", "").strip()
                    if word_text:
                        # Use None for missing confidence scores
                        score = w.get("score")
                        confidence = round(score, 3) if score is not None else None
                        valid_words.append(
                            Word(
                                text=word_text,
                                start=round(w.get("start", 0.0), 3),
                                end=round(w.get("end", 0.0), 3),
                                confidence=confidence,
                                alignment_method=AlignmentMethod.PHONEME_WAV2VEC,
                            )
                        )
                    else:
                        filtered_words += 1
                words = valid_words if valid_words else None

            normalized.append(
                Segment(
                    start=round(seg.get("start", 0.0), 3),
                    end=round(seg.get("end", 0.0), 3),
                    text=seg.get("text", ""),
                    words=words,
                )
            )

        if filtered_words > 0:
            self.logger.debug(
                "filtered_empty_words",
                filtered_count=filtered_words,
                total_count=total_words,
            )

        return normalized, {
            "filtered_words": filtered_words,
            "total_words": total_words,
        }

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
            TaskOutput with AlignOutput containing original segments and warning
        """
        # Convert raw segments to typed Segment objects
        typed_segments = [
            Segment(
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", ""),
            )
            for seg in segments
        ]

        output = AlignOutput(
            text=text,
            segments=typed_segments,
            language=language,
            word_timestamps=False,
            alignment_confidence=None,
            unaligned_words=[],
            unaligned_ratio=0.0,
            granularity_achieved=TimestampGranularity.SEGMENT,
            engine_id="whisperx-align",
            skipped=True,
            skip_reason=reason,
            warnings=[reason],
        )

        return TaskOutput(data=output)

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
