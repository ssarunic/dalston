"""Phoneme-level forced alignment engine for word-level timestamps.

Uses wav2vec2-based CTC forced alignment to produce accurate word
boundaries from transcription segments. This is a standalone
reimplementation that does not depend on the whisperx package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
import torchaudio
from align import AlignedSegment, InputSegment, align
from model_loader import AlignModelMetadata, is_language_supported, load_align_model

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


class PhonemeAlignEngine(Engine):
    """Phoneme-level forced alignment engine.

    Loads language-specific wav2vec2 alignment models lazily and caches
    them for subsequent requests. Falls back gracefully to transcription
    timestamps if alignment fails or the language is unsupported.
    """

    def __init__(self) -> None:
        super().__init__()
        self._align_models: dict[str, tuple[Any, AlignModelMetadata]] = {}
        self._device, self._compute_type = self._detect_device()
        self.logger.info(
            "detected_device", device=self._device, compute_type=self._compute_type
        )

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available compute device.

        Preference order: CUDA → MPS (Apple Silicon) → CPU.
        """
        if torch.cuda.is_available():
            return "cuda", "float16"
        if torch.backends.mps.is_available():
            # MPS supports float32 reliably; float16 has limited op coverage.
            return "mps", "float32"
        return "cpu", "float32"

    def _get_align_model(self, language: str) -> tuple[Any, AlignModelMetadata] | None:
        """Load or retrieve a cached alignment model for a language."""
        if language in self._align_models:
            self.logger.debug("using_cached_alignment_model", language=language)
            return self._align_models[language]

        if not is_language_supported(language):
            self.logger.warning("unsupported_alignment_language", language=language)
            return None

        self.logger.info(
            "loading_alignment_model", language=language, device=self._device
        )
        try:
            model, metadata = load_align_model(
                language_code=language,
                device=self._device,
            )
            self._align_models[language] = (model, metadata)
            self.logger.info("alignment_model_loaded", language=language)
            return model, metadata
        except Exception as e:
            self.logger.warning(
                "failed_to_load_alignment_model", language=language, error=str(e)
            )
            return None

    def process(self, input: TaskInput) -> TaskOutput:
        """Align transcription segments to produce word-level timestamps."""
        audio_path = input.audio_path

        # Get transcription output from previous stage
        transcribe_output = input.get_transcribe_output()
        if transcribe_output:
            text = transcribe_output.text
            raw_segments: list[InputSegment] = [
                InputSegment(start=s.start, end=s.end, text=s.text)
                for s in transcribe_output.segments
            ]
            language = transcribe_output.language
        else:
            raw_output = input.get_raw_output("transcribe")
            if not raw_output:
                raise ValueError("Missing 'transcribe' in previous_outputs")
            text = raw_output.get("text", "")
            raw_segments = [
                InputSegment(
                    start=s.get("start", 0.0),
                    end=s.get("end", 0.0),
                    text=s.get("text", ""),
                )
                for s in raw_output.get("segments", [])
            ]
            language = raw_output.get("language", "en")

        self.logger.info(
            "aligning_segments",
            segment_count=len(raw_segments),
            language=language,
        )

        # Load alignment model
        model_result = self._get_align_model(language)
        if model_result is None:
            return self._fallback_output(
                text,
                raw_segments,
                language,
                reason=f"No alignment model available for language '{language}'",
            )

        model, metadata = model_result

        try:
            audio = self._load_audio(audio_path)

            result = align(
                transcript=raw_segments,
                model=model,
                metadata=metadata,
                audio=audio,
                device=self._device,
                return_char_alignments=input.config.get(
                    "return_char_alignments", False
                ),
            )

            output_segments, stats = self._to_sdk_segments(result.segments)

            # Compute alignment confidence
            all_words: list[Word] = []
            for seg in output_segments:
                if seg.words:
                    all_words.extend(seg.words)

            confidences = [w.confidence for w in all_words if w.confidence is not None]
            alignment_confidence = (
                sum(confidences) / len(confidences) if confidences else None
            )

            aligned_count = len(all_words)
            unaligned_count = stats["unaligned_words"]
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
                    round(alignment_confidence, 3)
                    if alignment_confidence is not None
                    else None
                ),
                unaligned_words=[f"word_{i}" for i in range(unaligned_count)],
                unaligned_ratio=round(unaligned_ratio, 3),
                granularity_achieved=TimestampGranularity.WORD,
                engine_id="phoneme-align",
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskOutput(data=output)

        except Exception as e:
            self.logger.error("alignment_failed", error=str(e), exc_info=True)
            return self._fallback_output(
                text,
                raw_segments,
                language,
                reason=f"Alignment failed: {e}",
            )

    def _load_audio(self, audio_path: Path) -> np.ndarray:
        """Load audio file as 16 kHz mono numpy array."""
        waveform, sample_rate = torchaudio.load(str(audio_path))
        if sample_rate != 16_000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16_000)
            waveform = resampler(waveform)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        return waveform.squeeze(0).numpy()

    def _to_sdk_segments(
        self, aligned_segments: list[AlignedSegment]
    ) -> tuple[list[Segment], dict[str, int]]:
        """Convert aligned segments to SDK types."""
        segments: list[Segment] = []
        unaligned_words = 0

        for aseg in aligned_segments:
            words: list[Word] | None = None
            if aseg.words:
                valid_words: list[Word] = []
                for aw in aseg.words:
                    if not aw.word.strip():
                        continue
                    if aw.start is None or aw.end is None:
                        unaligned_words += 1
                        continue
                    valid_words.append(
                        Word(
                            text=aw.word,
                            start=aw.start,
                            end=aw.end,
                            confidence=aw.score,
                            alignment_method=AlignmentMethod.PHONEME_WAV2VEC,
                        )
                    )
                words = valid_words if valid_words else None

            segments.append(
                Segment(
                    start=aseg.start,
                    end=aseg.end,
                    text=aseg.text,
                    words=words,
                )
            )

        return segments, {"unaligned_words": unaligned_words}

    def _fallback_output(
        self,
        text: str,
        segments: list[InputSegment],
        language: str,
        reason: str,
    ) -> TaskOutput:
        """Return original timestamps when alignment is not possible."""
        self.logger.warning(
            "alignment_fallback", reason=reason, segment_count=len(segments)
        )
        typed_segments = [
            Segment(start=s["start"], end=s["end"], text=s["text"]) for s in segments
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
            engine_id="phoneme-align",
            skipped=True,
            skip_reason=reason,
            warnings=[reason],
        )
        return TaskOutput(data=output)

    def health_check(self) -> dict[str, Any]:
        """Return health status including device info."""
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        mps_available = torch.backends.mps.is_available()

        return {
            "status": "healthy",
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "mps_available": mps_available,
            "cached_languages": list(self._align_models.keys()),
        }


if __name__ == "__main__":
    engine = PhonemeAlignEngine()
    engine.run()
