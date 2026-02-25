"""PII Detection Engine using Presidio with GLiNER backend.

Detects personally identifiable information in transcripts and generates
redacted text. Supports three detection tiers:
- fast: Regex-based detection only (<5ms)
- standard: Regex + GLiNER NER model (~100ms)
- thorough: Regex + GLiNER + LLM contextual (1-3s) - future

The engine maps detected entities to word timestamps from the transcript
to enable audio redaction in the subsequent stage.
"""

import os
import re
import time
from collections import defaultdict
from typing import Any

from dalston.engine_sdk import (
    Engine,
    PIIDetectionTier,
    PIIDetectOutput,
    PIIEntity,
    PIIEntityCategory,
    Segment,
    TaskInput,
    TaskOutput,
)

# Entity type to category mapping
ENTITY_CATEGORY_MAP = {
    # PII category
    "name": PIIEntityCategory.PII,
    "name_given": PIIEntityCategory.PII,
    "name_family": PIIEntityCategory.PII,
    "email_address": PIIEntityCategory.PII,
    "phone_number": PIIEntityCategory.PII,
    "ssn": PIIEntityCategory.PII,
    "location": PIIEntityCategory.PII,
    "location_address": PIIEntityCategory.PII,
    "date_of_birth": PIIEntityCategory.PII,
    "age": PIIEntityCategory.PII,
    "ip_address": PIIEntityCategory.PII,
    "driver_license": PIIEntityCategory.PII,
    "passport_number": PIIEntityCategory.PII,
    "organization": PIIEntityCategory.PII,
    "jmbg": PIIEntityCategory.PII,
    "oib": PIIEntityCategory.PII,
    # PCI category
    "credit_card_number": PIIEntityCategory.PCI,
    "credit_card_cvv": PIIEntityCategory.PCI,
    "credit_card_expiry": PIIEntityCategory.PCI,
    "iban": PIIEntityCategory.PCI,
    "bank_account": PIIEntityCategory.PCI,
    # PHI category
    "medical_record_number": PIIEntityCategory.PHI,
    "medical_condition": PIIEntityCategory.PHI,
    "medication": PIIEntityCategory.PHI,
    "health_plan_id": PIIEntityCategory.PHI,
}

# Default entity types to detect (tier-independent)
DEFAULT_ENTITY_TYPES = [
    "name",
    "email_address",
    "phone_number",
    "ssn",
    "location",
    "date_of_birth",
    "ip_address",
    "credit_card_number",
    "credit_card_cvv",
    "credit_card_expiry",
    "iban",
]

# Low-signal tokens that GLiNER may misclassify as PERSON in ASR text.
# These are filtered only for "name" entities to reduce obvious false positives.
NAME_FALSE_POSITIVE_TOKENS = {
    "i",
    "you",
    "we",
    "he",
    "she",
    "they",
    "it",
    "me",
    "him",
    "her",
    "them",
    "us",
    "my",
    "your",
    "our",
    "their",
    "mine",
    "yours",
    "ours",
    "theirs",
    "this",
    "that",
    "these",
    "those",
}

# Presidio entity type mapping (Dalston type -> Presidio type)
PRESIDIO_TYPE_MAP = {
    "name": "PERSON",
    "email_address": "EMAIL_ADDRESS",
    "phone_number": "PHONE_NUMBER",
    "ssn": "US_SSN",
    "location": "LOCATION",
    "ip_address": "IP_ADDRESS",
    "credit_card_number": "CREDIT_CARD",
    "iban": "IBAN_CODE",
    "date_of_birth": "DATE_TIME",
}


class PIIDetectionEngine(Engine):
    """PII detection engine using Presidio + GLiNER."""

    def __init__(self) -> None:
        super().__init__()
        self._analyzer = None
        self._anonymizer = None
        self._gliner_model = None
        self._tier: PIIDetectionTier | None = None
        self._device = self._resolve_device()
        self.logger.info("pii_detection_engine_initialized", device=self._device)

    def _resolve_device(self) -> str:
        """Resolve inference device from DEVICE env with auto-detect fallback."""
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        try:
            import torch

            cuda_available = torch.cuda.is_available()
        except ImportError:
            cuda_available = False

        if requested_device == "cpu":
            return "cpu"

        if requested_device == "cuda":
            if not cuda_available:
                raise RuntimeError(
                    "DEVICE=cuda but CUDA is not available for pii-presidio."
                )
            return "cuda"

        if requested_device in ("", "auto"):
            return "cuda" if cuda_available else "cpu"

        raise ValueError(f"Unknown DEVICE value: {requested_device}. Use cuda or cpu.")

    def _load_presidio(self) -> None:
        """Load Presidio analyzer and anonymizer."""
        if self._analyzer is not None:
            return

        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        self.logger.info("loading_presidio")
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

        # Register custom recognizers
        self._register_custom_recognizers()
        self.logger.info("presidio_loaded")

    def _register_custom_recognizers(self) -> None:
        """Register custom Presidio recognizers for additional entity types."""
        from presidio_analyzer import Pattern, PatternRecognizer

        # Credit card with Luhn validation (handled by Presidio's built-in)
        # IBAN with mod-97 validation (handled by Presidio's built-in)

        # CVV pattern (3-4 digits near card context)
        cvv_recognizer = PatternRecognizer(
            supported_entity="CREDIT_CARD_CVV",
            patterns=[
                Pattern(
                    name="cvv_pattern",
                    regex=r"\b(?:cvv|cvc|cvc2|cvv2|security\s+code)[:\s]*(\d{3,4})\b",
                    score=0.7,
                ),
            ],
            supported_language="en",
        )
        self._analyzer.registry.add_recognizer(cvv_recognizer)

        # Card expiry pattern (MM/YY or MM/YYYY)
        expiry_recognizer = PatternRecognizer(
            supported_entity="CREDIT_CARD_EXPIRY",
            patterns=[
                Pattern(
                    name="expiry_pattern",
                    regex=r"\b(?:exp(?:ir(?:y|es|ation))?|valid\s+(?:thru|until))[:\s]*(\d{1,2}[/\-]\d{2,4})\b",
                    score=0.7,
                ),
            ],
            supported_language="en",
        )
        self._analyzer.registry.add_recognizer(expiry_recognizer)

        # JMBG (Serbian/Yugoslav national ID - 13 digits with checksum)
        jmbg_recognizer = PatternRecognizer(
            supported_entity="JMBG",
            patterns=[
                Pattern(
                    name="jmbg_pattern",
                    regex=r"\b\d{13}\b",
                    score=0.5,
                ),
            ],
            supported_language="en",
        )
        self._analyzer.registry.add_recognizer(jmbg_recognizer)

        # OIB (Croatian personal ID - 11 digits with checksum)
        oib_recognizer = PatternRecognizer(
            supported_entity="OIB",
            patterns=[
                Pattern(
                    name="oib_pattern",
                    regex=r"\b\d{11}\b",
                    score=0.5,
                ),
            ],
            supported_language="en",
        )
        self._analyzer.registry.add_recognizer(oib_recognizer)

    def _load_gliner(self) -> None:
        """Load GLiNER model for ML-based entity recognition."""
        if self._gliner_model is not None:
            return

        try:
            from gliner import GLiNER

            self.logger.info("loading_gliner_model", device=self._device)
            try:
                self._gliner_model = GLiNER.from_pretrained(
                    "urchade/gliner_multi-v2.1",
                    device=self._device,
                )
            except TypeError:
                # Backward compatibility for GLiNER versions without device kwarg.
                self._gliner_model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
                if hasattr(self._gliner_model, "to"):
                    moved = self._gliner_model.to(self._device)
                    if moved is not None:
                        self._gliner_model = moved

            self.logger.info("gliner_model_loaded", device=self._device)
        except ImportError:
            self.logger.warning("gliner_not_available")
        except Exception as e:
            self.logger.warning("gliner_load_failed", error=str(e))

    def process(self, input: TaskInput) -> TaskOutput:
        """Detect PII entities in transcript.

        Args:
            input: Task input containing transcript from transcribe/align stages

        Returns:
            TaskOutput with PIIDetectOutput containing detected entities
        """
        start_time = time.time()
        config = input.config
        job_id = input.job_id

        # Get detection tier
        tier_str = config.get("detection_tier", "standard")
        tier = PIIDetectionTier(tier_str)
        self._tier = tier

        # Get entity types to detect
        entity_types = config.get("entity_types") or DEFAULT_ENTITY_TYPES
        confidence_threshold = config.get("confidence_threshold", 0.5)

        self.logger.info(
            "pii_detection_starting",
            job_id=job_id,
            tier=tier.value,
            entity_types=entity_types,
        )

        # Load models based on tier
        self._load_presidio()
        if tier in (PIIDetectionTier.STANDARD, PIIDetectionTier.THOROUGH):
            self._load_gliner()

        # Get transcript data from previous stages
        align_output = input.get_align_output()
        transcribe_output = input.get_transcribe_output()

        # Extract language from transcription
        language = "en"  # Default fallback
        if transcribe_output and transcribe_output.language:
            language = transcribe_output.language
        elif (
            align_output and hasattr(align_output, "language") and align_output.language
        ):
            language = align_output.language

        if align_output and not align_output.skipped:
            segments = align_output.segments
            text = align_output.text
        elif transcribe_output:
            segments = transcribe_output.segments
            text = transcribe_output.text
        else:
            # Fallback to raw output
            raw_transcribe = input.get_raw_output("transcribe") or {}
            text = raw_transcribe.get("text", "")
            language = raw_transcribe.get("language", "en")
            segments = []

        self.logger.info("detected_language_for_pii", language=language)

        # Get diarization for speaker assignment
        diarize_output = input.get_diarize_output()
        speaker_turns = diarize_output.turns if diarize_output else []

        # Detect entities
        entities = self._detect_entities(
            text=text,
            segments=segments,
            entity_types=entity_types,
            confidence_threshold=confidence_threshold,
            speaker_turns=speaker_turns,
            language=language,
        )

        # Generate redacted text
        redacted_text = self._generate_redacted_text(text, entities)

        # Count entities by type and category
        entity_count_by_type: dict[str, int] = defaultdict(int)
        entity_count_by_category: dict[str, int] = defaultdict(int)
        for entity in entities:
            entity_count_by_type[entity.entity_type] += 1
            entity_count_by_category[entity.category.value] += 1

        processing_time_ms = int((time.time() - start_time) * 1000)

        self.logger.info(
            "pii_detection_complete",
            job_id=job_id,
            entities_found=len(entities),
            processing_time_ms=processing_time_ms,
        )

        output = PIIDetectOutput(
            entities=entities,
            redacted_text=redacted_text,
            entity_count_by_type=dict(entity_count_by_type),
            entity_count_by_category=dict(entity_count_by_category),
            detection_tier=tier,
            processing_time_ms=processing_time_ms,
            engine_id="pii-presidio",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

    # Languages supported by Presidio's default NLP models
    PRESIDIO_SUPPORTED_LANGUAGES = {"en", "de", "es", "fr", "it", "pt", "nl", "he"}

    def _detect_entities(
        self,
        text: str,
        segments: list[Segment],
        entity_types: list[str],
        confidence_threshold: float,
        speaker_turns: list,
        language: str = "en",
    ) -> list[PIIEntity]:
        """Detect PII entities in text using Presidio and optionally GLiNER.

        For languages not supported by Presidio (en, de, es, fr, it, pt, nl, he),
        we skip Presidio and rely solely on GLiNER which is multilingual.

        Args:
            text: Full transcript text
            segments: Transcript segments with word timestamps
            entity_types: Entity types to detect
            confidence_threshold: Minimum confidence threshold
            speaker_turns: Speaker diarization turns for assignment
            language: ISO 639-1 language code from transcription

        Returns:
            List of detected PIIEntity objects
        """
        entities: list[PIIEntity] = []

        # Build word-to-time mapping from segments
        word_times = self._build_word_time_map(segments)

        # Check if Presidio supports this language
        use_presidio = language in self.PRESIDIO_SUPPORTED_LANGUAGES
        if not use_presidio:
            self.logger.info(
                "presidio_skipped_unsupported_language",
                language=language,
                supported=list(self.PRESIDIO_SUPPORTED_LANGUAGES),
            )

        # Map entity types to Presidio entities
        presidio_entities = []
        for et in entity_types:
            if et in PRESIDIO_TYPE_MAP:
                presidio_entities.append(PRESIDIO_TYPE_MAP[et])

        # Add custom recognizer entities
        custom_entities = ["CREDIT_CARD_CVV", "CREDIT_CARD_EXPIRY", "JMBG", "OIB"]
        for ce in custom_entities:
            dalston_type = ce.lower()
            if dalston_type in entity_types:
                presidio_entities.append(ce)

        # Run Presidio analysis only for supported languages
        if presidio_entities and self._analyzer and use_presidio:
            results = self._analyzer.analyze(
                text=text,
                entities=presidio_entities,
                language=language,
            )

            for result in results:
                if result.score < confidence_threshold:
                    continue

                # Map Presidio entity back to Dalston type
                dalston_type = self._presidio_to_dalston_type(result.entity_type)
                category = ENTITY_CATEGORY_MAP.get(dalston_type, PIIEntityCategory.PII)

                # Get timing from word map
                start_time, end_time = self._find_entity_timing(
                    text, result.start, result.end, word_times
                )

                # Find speaker
                speaker = self._find_speaker(start_time, end_time, speaker_turns)

                # Generate redacted value
                original_text = text[result.start : result.end]
                redacted_value = self._generate_redacted_value(
                    original_text, dalston_type
                )

                entities.append(
                    PIIEntity(
                        entity_type=dalston_type,
                        category=category,
                        start_offset=result.start,
                        end_offset=result.end,
                        start_time=start_time,
                        end_time=end_time,
                        confidence=result.score,
                        speaker=speaker,
                        redacted_value=redacted_value,
                        original_text=original_text,
                    )
                )

        # Run GLiNER for name/location/organization (standard/thorough tiers)
        if self._gliner_model and self._tier in (
            PIIDetectionTier.STANDARD,
            PIIDetectionTier.THOROUGH,
        ):
            gliner_entities = self._detect_with_gliner(
                text, entity_types, confidence_threshold, word_times, speaker_turns
            )
            entities.extend(gliner_entities)

        # Sort by position and deduplicate overlapping entities
        entities.sort(key=lambda e: e.start_offset)
        entities = self._deduplicate_entities(entities)

        return entities

    def _detect_with_gliner(
        self,
        text: str,
        entity_types: list[str],
        confidence_threshold: float,
        word_times: dict[int, tuple[float, float]],
        speaker_turns: list,
    ) -> list[PIIEntity]:
        """Detect entities using GLiNER model."""
        entities: list[PIIEntity] = []

        # GLiNER labels to detect
        gliner_labels = []
        gliner_to_dalston = {}
        if "name" in entity_types:
            gliner_labels.append("person")
            gliner_to_dalston["person"] = "name"
        if "organization" in entity_types:
            gliner_labels.append("organization")
            gliner_to_dalston["organization"] = "organization"
        if "location" in entity_types:
            gliner_labels.append("location")
            gliner_to_dalston["location"] = "location"
        if "medical_condition" in entity_types:
            gliner_labels.append("medical condition")
            gliner_to_dalston["medical condition"] = "medical_condition"
        if "medication" in entity_types:
            gliner_labels.append("medication")
            gliner_to_dalston["medication"] = "medication"

        if not gliner_labels:
            return entities

        try:
            predictions = self._gliner_model.predict_entities(
                text, gliner_labels, threshold=confidence_threshold
            )

            for pred in predictions:
                dalston_type = gliner_to_dalston.get(pred["label"])
                if not dalston_type:
                    continue

                if self._should_filter_gliner_entity(
                    dalston_type, pred.get("text", "")
                ):
                    continue

                category = ENTITY_CATEGORY_MAP.get(dalston_type, PIIEntityCategory.PII)
                start_offset = pred["start"]
                end_offset = pred["end"]

                # Get timing
                start_time, end_time = self._find_entity_timing(
                    text, start_offset, end_offset, word_times
                )

                # Find speaker
                speaker = self._find_speaker(start_time, end_time, speaker_turns)

                # Generate redacted value
                original_text = pred["text"]
                redacted_value = self._generate_redacted_value(
                    original_text, dalston_type
                )

                entities.append(
                    PIIEntity(
                        entity_type=dalston_type,
                        category=category,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        start_time=start_time,
                        end_time=end_time,
                        confidence=pred.get("score", 0.8),
                        speaker=speaker,
                        redacted_value=redacted_value,
                        original_text=original_text,
                    )
                )
        except Exception as e:
            self.logger.warning("gliner_prediction_failed", error=str(e))

        return entities

    def _should_filter_gliner_entity(self, entity_type: str, text: str) -> bool:
        """Filter low-signal GLiNER entities that are frequent false positives."""
        if entity_type != "name":
            return False

        normalized = re.sub(r"[^a-zA-Z']+", "", text).lower()
        if not normalized:
            return True

        if len(normalized) == 1:
            return True

        return normalized in NAME_FALSE_POSITIVE_TOKENS

    def _build_word_time_map(
        self, segments: list[Segment]
    ) -> dict[int, tuple[float, float]]:
        """Build mapping from character offset to word timing.

        Args:
            segments: Transcript segments with word timestamps

        Returns:
            Dict mapping character offset to (start_time, end_time)
        """
        word_times: dict[int, tuple[float, float]] = {}
        current_offset = 0

        for seg in segments:
            if hasattr(seg, "words") and seg.words:
                for word in seg.words:
                    word_text = (
                        word.text if hasattr(word, "text") else word.get("text", "")
                    )
                    word_start = (
                        word.start if hasattr(word, "start") else word.get("start", 0)
                    )
                    word_end = word.end if hasattr(word, "end") else word.get("end", 0)

                    # Map each character in this word
                    for i in range(len(word_text)):
                        word_times[current_offset + i] = (word_start, word_end)
                    current_offset += len(word_text) + 1  # +1 for space
            else:
                # Fall back to segment timing
                seg_text = seg.text if hasattr(seg, "text") else seg.get("text", "")
                seg_start = seg.start if hasattr(seg, "start") else seg.get("start", 0)
                seg_end = seg.end if hasattr(seg, "end") else seg.get("end", 0)

                for i in range(len(seg_text)):
                    word_times[current_offset + i] = (seg_start, seg_end)
                current_offset += len(seg_text) + 1

        return word_times

    def _find_entity_timing(
        self,
        text: str,
        start_offset: int,
        end_offset: int,
        word_times: dict[int, tuple[float, float]],
    ) -> tuple[float, float]:
        """Find audio timing for an entity span.

        Args:
            text: Full text
            start_offset: Character start offset
            end_offset: Character end offset
            word_times: Character offset to timing map

        Returns:
            Tuple of (start_time, end_time) in seconds
        """
        start_time = 0.0
        end_time = 0.0

        # Find first word timing in range
        for offset in range(start_offset, end_offset):
            if offset in word_times:
                start_time = word_times[offset][0]
                break

        # Find last word timing in range
        for offset in range(end_offset - 1, start_offset - 1, -1):
            if offset in word_times:
                end_time = word_times[offset][1]
                break

        return start_time, end_time

    def _find_speaker(
        self,
        start_time: float,
        end_time: float,
        speaker_turns: list,
    ) -> str | None:
        """Find speaker for an entity based on timing overlap."""
        if not speaker_turns:
            return None

        best_speaker = None
        max_overlap = 0.0

        for turn in speaker_turns:
            turn_start = turn.start if hasattr(turn, "start") else turn.get("start", 0)
            turn_end = turn.end if hasattr(turn, "end") else turn.get("end", 0)
            turn_speaker = (
                turn.speaker if hasattr(turn, "speaker") else turn.get("speaker")
            )

            overlap_start = max(start_time, turn_start)
            overlap_end = min(end_time, turn_end)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = turn_speaker

        return best_speaker

    def _presidio_to_dalston_type(self, presidio_type: str) -> str:
        """Map Presidio entity type back to Dalston type."""
        reverse_map = {v: k for k, v in PRESIDIO_TYPE_MAP.items()}
        return reverse_map.get(presidio_type, presidio_type.lower())

    def _generate_redacted_value(self, original: str, entity_type: str) -> str:
        """Generate redacted representation of an entity.

        Args:
            original: Original entity text
            entity_type: Type of entity

        Returns:
            Redacted string (e.g., "****7890" for credit card)
        """
        if entity_type == "credit_card_number":
            # Show last 4 digits
            digits = re.sub(r"\D", "", original)
            if len(digits) >= 4:
                return f"****{digits[-4:]}"
            return "****"
        elif entity_type in ("phone_number", "ssn"):
            # Show last 4 characters
            clean = re.sub(r"\s", "", original)
            if len(clean) >= 4:
                return f"****{clean[-4:]}"
            return "****"
        elif entity_type == "email_address":
            # Show domain only
            if "@" in original:
                parts = original.split("@")
                return f"****@{parts[-1]}"
            return "****"
        else:
            # Generic placeholder
            return f"[{entity_type.upper()}]"

    def _generate_redacted_text(self, text: str, entities: list[PIIEntity]) -> str:
        """Generate redacted version of text.

        Args:
            text: Original text
            entities: Detected entities (sorted by position)

        Returns:
            Text with PII replaced by placeholders
        """
        # Sort by position descending to replace from end
        sorted_entities = sorted(entities, key=lambda e: e.start_offset, reverse=True)

        result = text
        for entity in sorted_entities:
            placeholder = f"[{entity.entity_type.upper()}]"
            result = (
                result[: entity.start_offset]
                + placeholder
                + result[entity.end_offset :]
            )

        return result

    def _deduplicate_entities(self, entities: list[PIIEntity]) -> list[PIIEntity]:
        """Remove overlapping duplicate entities, keeping highest confidence."""
        if not entities:
            return entities

        result: list[PIIEntity] = []
        for entity in entities:
            # Check if overlaps with any existing entity
            overlaps = False
            for i, existing in enumerate(result):
                if (
                    entity.start_offset < existing.end_offset
                    and entity.end_offset > existing.start_offset
                ):
                    # Overlapping - keep higher confidence
                    overlaps = True
                    if entity.confidence > existing.confidence:
                        result[i] = entity
                    break
            if not overlaps:
                result.append(entity)

        return result

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        return {
            "status": "healthy",
            "presidio_loaded": self._analyzer is not None,
            "gliner_loaded": self._gliner_model is not None,
            "tier": self._tier.value if self._tier else None,
        }


if __name__ == "__main__":
    engine = PIIDetectionEngine()
    engine.run()
