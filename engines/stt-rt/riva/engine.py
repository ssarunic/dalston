"""Riva NIM realtime transcription engine.

Thin gRPC client that delegates per-utterance inference to a Riva NIM
container's offline_recognize API. The SessionHandler manages VAD and
chunking — this engine only handles inference for each detected utterance.
"""

from __future__ import annotations

import os
import time

import numpy as np
import riva.client
import structlog

from dalston.realtime_sdk.assembler import TranscribeResult, Word
from dalston.realtime_sdk.base import RealtimeEngine

logger = structlog.get_logger()


class RivaRealtimeEngine(RealtimeEngine):
    """Realtime engine delegating to Riva NIM gRPC."""

    def load_models(self) -> None:
        """Set up gRPC channel to Riva NIM container."""
        riva_url = os.environ["RIVA_GRPC_URL"]
        self._auth = riva.client.Auth(uri=riva_url)
        self._asr = riva.client.ASRService(self._auth)
        logger.info("riva_realtime_engine_initialized", riva_url=riva_url)

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe a single VAD-segmented utterance via Riva NIM.

        Args:
            audio: float32 numpy array, mono, 16kHz
            language: Language code ("en") or "auto"
            model_variant: Model name (unused — NIM serves a fixed model)
            vocabulary: Not supported by Riva NIM

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        # Convert float32 numpy array to int16 PCM bytes for Riva
        audio_int16 = (audio * 32768).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        # Normalize language code for Riva (expects BCP-47)
        lang_code = language if language != "auto" else "en-US"
        if len(lang_code) == 2:
            lang_code = f"{lang_code}-US" if lang_code == "en" else lang_code

        config = riva.client.RecognitionConfig(
            language_code=lang_code,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        )

        start_time = time.monotonic()
        response = self._asr.offline_recognize(audio_bytes, config)
        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Map Riva response → Dalston TranscribeResult
        text_parts: list[str] = []
        words: list[Word] = []
        for result in response.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            text_parts.append(alt.transcript)
            for w in alt.words:
                words.append(
                    Word(
                        word=w.word,
                        start=w.start_time,
                        end=w.end_time,
                        confidence=w.confidence,
                    )
                )

        confidence = 0.0
        if response.results and response.results[0].alternatives:
            confidence = response.results[0].alternatives[0].confidence

        detected_language = language if language != "auto" else "en"

        logger.debug(
            "riva_rt_transcribe_completed",
            duration_ms=round(elapsed_ms, 1),
            audio_duration_s=round(len(audio) / 16000, 2),
            words_count=len(words),
        )

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=detected_language,
            confidence=confidence,
        )

    def get_models(self) -> list[str]:
        return ["nvidia/parakeet-ctc-1.1b-riva"]

    def get_languages(self) -> list[str]:
        return ["en"]

    def get_runtime(self) -> str:
        return "riva"

    def supports_streaming(self) -> bool:
        return False

    def get_supports_vocabulary(self) -> bool:
        return False


if __name__ == "__main__":
    import asyncio

    engine = RivaRealtimeEngine()
    asyncio.run(engine.run())
