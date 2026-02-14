"""Strongly typed pipeline interface models.

These models define the data contracts between pipeline stages as specified
in docs/specs/PIPELINE_INTERFACES.md. All inter-stage communication should
use these types for validation and type safety.

Design principles:
1. Data-driven skip decisions - stages inspect input data to decide if processing is needed
2. Explicit capability signaling - use explicit fields like timestamp_granularity_actual
3. NaN for missing confidence - allows downstream calculations without null-checking
4. Single timeline - all timestamps relative to original audio
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class TimestampGranularity(str, Enum):
    """Timestamp precision levels.

    Hierarchy: phoneme > character > word > segment > none
    A model producing phoneme-level timestamps can satisfy requests for any coarser granularity.
    """

    NONE = "none"
    SEGMENT = "segment"
    WORD = "word"
    CHARACTER = "character"
    PHONEME = "phoneme"


class AlignmentMethod(str, Enum):
    """How word/character timestamps were produced."""

    ATTENTION = "attention"  # Cross-attention alignment (Whisper)
    CTC = "ctc"  # CTC forced alignment (wav2vec2, NeMo)
    RNNT = "rnnt"  # RNNT alignment (Parakeet RNNT)
    TDT = "tdt"  # Token-Duration Transducer (Parakeet TDT)
    PHONEME_WAV2VEC = "phoneme_wav2vec"  # wav2vec2 phoneme model (WhisperX)
    PHONEME_MMS = "phoneme_mms"  # MMS phoneme model (WhisperX)
    MFA = "mfa"  # Montreal Forced Aligner
    WFST = "wfst"  # WFST decoding (Kaldi/Vosk)
    UNKNOWN = "unknown"  # Not specified


class SpeakerDetectionMode(str, Enum):
    """Speaker detection mode for transcription jobs."""

    NONE = "none"  # No speaker detection
    DIARIZE = "diarize"  # Use diarization model
    PER_CHANNEL = "per_channel"  # Each audio channel is a speaker


# =============================================================================
# Core Data Structures
# =============================================================================


class Phoneme(BaseModel):
    """Phoneme-level timing information."""

    model_config = ConfigDict(extra="forbid")

    phoneme: str = Field(..., description="IPA symbol (e.g., 'ð', 'ə')")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    confidence: float | None = Field(
        default=None, description="0.0-1.0 confidence, None if unavailable"
    )
    stress: int | None = Field(
        default=None, ge=0, le=2, description="0=unstressed, 1=primary, 2=secondary"
    )


class Character(BaseModel):
    """Character-level timing information."""

    model_config = ConfigDict(extra="forbid")

    char: str = Field(..., min_length=1, max_length=1, description="Single character")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    confidence: float | None = Field(
        default=None, description="0.0-1.0 confidence, None if unavailable"
    )
    phonemes: list[Phoneme] | None = Field(
        default=None, description="Source phonemes if derived"
    )


class Word(BaseModel):
    """Word-level timing information."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="The word text")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    confidence: float | None = Field(
        default=None, description="0.0-1.0 confidence, None/NaN if unavailable"
    )
    characters: list[Character] | None = Field(
        default=None, description="Character-level timing"
    )
    phonemes: list[Phoneme] | None = Field(
        default=None, description="Phoneme-level timing"
    )
    alignment_method: AlignmentMethod | None = Field(
        default=None, description="How timestamps were produced"
    )


class Segment(BaseModel):
    """Transcript segment with optional word-level detail."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None, description="Stable ID for incremental updates"
    )
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    text: str = Field(..., description="Transcript text")
    words: list[Word] | None = Field(default=None, description="Word-level detail")
    confidence: float | None = Field(
        default=None, description="Segment-level confidence, None if unavailable"
    )
    language: str | None = Field(
        default=None, description="ISO 639-1 code (for code-switching)"
    )
    is_speech: bool | None = Field(
        default=None, description="False for music/noise segments"
    )
    is_final: bool | None = Field(
        default=None, description="False for interim realtime results"
    )


class SpeakerTurn(BaseModel):
    """Speaker diarization turn."""

    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(..., description="Speaker ID (e.g., 'SPEAKER_00')")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    confidence: float | None = Field(
        default=None, description="0.0-1.0 confidence, None if unavailable"
    )
    overlapping_speakers: list[str] | None = Field(
        default=None, description="Other speakers during overlap"
    )


class SpeechRegion(BaseModel):
    """Detected speech region from VAD."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    confidence: float | None = Field(
        default=None, description="VAD confidence, None if unavailable"
    )


class AudioMedia(BaseModel):
    """Audio file with metadata.

    Groups URI with its audio properties for self-describing media references.
    Used for both input (original audio) and output (prepared/channel files).
    """

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., description="S3 URI to audio file")
    format: str = Field(..., description="Audio format (e.g., 'wav', 'mp3')")
    duration: float = Field(..., ge=0, description="Duration in seconds")
    sample_rate: int = Field(..., gt=0, description="Sample rate in Hz")
    channels: int = Field(..., ge=1, description="Number of channels")
    bit_depth: int | None = Field(
        default=None, description="Bits per sample (None for lossy formats)"
    )


class TaskInputData(BaseModel):
    """Task input data written to S3 for engine consumption.

    This is the schema for task input.json files that engines read.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Task identifier")
    job_id: str = Field(..., description="Parent job identifier")

    # Audio source - one of these is set
    media: AudioMedia | None = Field(
        default=None, description="Input audio with metadata (prepare stage)"
    )
    audio_uri: str | None = Field(
        default=None, description="Audio URI (non-prepare stages)"
    )

    previous_outputs: dict[str, Any] = Field(
        default_factory=dict, description="Results from dependency stages"
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="Engine-specific configuration"
    )


class Speaker(BaseModel):
    """Speaker information for output."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Speaker ID (e.g., 'SPEAKER_00')")
    label: str | None = Field(default=None, description="User-assigned label")
    channel: int | None = Field(
        default=None, description="Audio channel if per_channel mode"
    )


class MergedSegment(BaseModel):
    """Transcript segment with speaker assignment (extends Segment)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Segment ID (e.g., 'seg_000')")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., ge=0, description="End time in seconds")
    text: str = Field(..., description="Transcript text")
    speaker: str | None = Field(default=None, description="Assigned speaker ID")
    words: list[Word] | None = Field(default=None, description="Word-level detail")
    speaker_confidence: float | None = Field(
        default=None, description="Speaker assignment confidence"
    )
    emotion: str | None = Field(default=None, description="Detected emotion")
    emotion_confidence: float | None = Field(
        default=None, description="Emotion confidence"
    )
    events: list[str] = Field(default_factory=list, description="Detected events")


# =============================================================================
# Stage Input Models
# =============================================================================


class PrepareInput(BaseModel):
    """Input for audio preparation stage."""

    model_config = ConfigDict(extra="ignore")

    target_sample_rate: int = Field(default=16000, description="Target sample rate")
    target_channels: int = Field(default=1, description="1=mono, 2=stereo")
    target_encoding: str = Field(default="pcm_s16le", description="Audio encoding")
    normalize_volume: bool = Field(
        default=True, description="Apply volume normalization"
    )
    detect_speech_regions: bool = Field(
        default=False, description="Run VAD to detect speech"
    )
    split_channels: bool = Field(
        default=False, description="Split to separate channel files"
    )


class TranscribeInput(BaseModel):
    """Input for transcription stage."""

    model_config = ConfigDict(extra="ignore")

    # Language
    language: str | None = Field(
        default=None, description="ISO 639-1 code, None=auto-detect"
    )
    task: str = Field(default="transcribe", description="'transcribe' or 'translate'")

    # Timestamps
    timestamp_granularity: TimestampGranularity = Field(
        default=TimestampGranularity.WORD, description="Requested timestamp precision"
    )

    # Decoding hints
    initial_prompt: str | None = Field(
        default=None, description="Domain vocabulary hints"
    )
    hotwords: list[str] | None = Field(default=None, description="Terms to boost")
    suppress_tokens: list[str] | None = Field(
        default=None, description="Tokens to suppress"
    )
    suppress_blank: bool = Field(default=True, description="Filter empty segments")

    # Processing
    vad_filter: bool = Field(
        default=True, description="Skip inference on non-speech regions"
    )
    temperature: float | list[float] = Field(
        default=0.0, description="Decoding temperature(s)"
    )
    beam_size: int | None = Field(default=None, description="Beam search width")
    best_of: int | None = Field(default=None, description="Number of candidates")


class AlignInput(BaseModel):
    """Input for alignment stage."""

    model_config = ConfigDict(extra="ignore")

    target_granularity: TimestampGranularity = Field(
        default=TimestampGranularity.WORD, description="Target timestamp precision"
    )
    realign_if_quality_below: float | None = Field(
        default=None, ge=0, le=1, description="Quality threshold for re-alignment"
    )
    return_char_alignments: bool = Field(
        default=False, description="Include character timing"
    )
    return_phoneme_alignments: bool = Field(
        default=False, description="Include phoneme timing"
    )


class DiarizeInput(BaseModel):
    """Input for diarization stage."""

    model_config = ConfigDict(extra="ignore")

    num_speakers: int | None = Field(
        default=None, ge=1, description="Exact speaker count, None=auto"
    )
    min_speakers: int | None = Field(
        default=None, ge=1, description="Minimum for auto-detect"
    )
    max_speakers: int | None = Field(
        default=None, ge=1, description="Maximum for auto-detect"
    )
    detect_overlap: bool = Field(default=True, description="Detect overlapping speech")


class MergeInput(BaseModel):
    """Input for merge stage."""

    model_config = ConfigDict(extra="ignore")

    merge_strategy: str = Field(
        default="segment", description="'segment' or 'word' level merging"
    )
    split_on_speaker_change: bool = Field(
        default=False, description="Re-segment at speaker boundaries"
    )


# =============================================================================
# Stage Output Models
# =============================================================================


class PrepareOutput(BaseModel):
    """Output from audio preparation stage."""

    model_config = ConfigDict(extra="forbid")

    # Audio files - always an array (1 element for mono, N for per-channel)
    channel_files: list[AudioMedia] = Field(
        ..., description="Prepared audio files (1 for mono, N for per-channel)"
    )

    split_channels: bool = Field(
        default=False, description="Whether per-channel processing is enabled"
    )

    # VAD output (if detect_speech_regions=True)
    speech_regions: list[SpeechRegion] | None = Field(
        default=None, description="Detected speech regions"
    )
    speech_ratio: float | None = Field(
        default=None, ge=0, le=1, description="Fraction containing speech"
    )

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether processing was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class TranscribeOutput(BaseModel):
    """Output from transcription stage."""

    model_config = ConfigDict(extra="forbid")

    segments: list[Segment] = Field(..., description="Transcript segments")
    text: str = Field(..., description="Full transcript text")
    language: str = Field(..., description="Detected/used language code")
    language_confidence: float | None = Field(
        default=None, ge=0, le=1, description="Language detection confidence"
    )
    duration: float | None = Field(default=None, ge=0, description="Audio duration")

    # Timestamp metadata
    timestamp_granularity_requested: TimestampGranularity | None = Field(
        default=None, description="What was requested"
    )
    timestamp_granularity_actual: TimestampGranularity | None = Field(
        default=None, description="What was produced"
    )
    alignment_method: AlignmentMethod | None = Field(
        default=None, description="How timestamps were produced"
    )

    # Per-channel mode
    channel: int | None = Field(
        default=None,
        description="Source audio channel (0=left, 1=right) for per_channel mode",
    )

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether processing was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class AlignOutput(BaseModel):
    """Output from alignment stage."""

    model_config = ConfigDict(extra="forbid")

    segments: list[Segment] = Field(..., description="Segments with refined timestamps")
    text: str = Field(..., description="Full transcript text")
    language: str = Field(..., description="Language code")

    # Word timestamps flag (for merger compatibility)
    word_timestamps: bool = Field(
        default=False, description="Whether word timestamps are present"
    )

    # Alignment statistics
    alignment_confidence: float | None = Field(
        default=None, description="Overall alignment quality"
    )
    unaligned_words: list[str] = Field(
        default_factory=list, description="Words that couldn't be aligned"
    )
    unaligned_ratio: float = Field(
        default=0.0, ge=0, le=1, description="Fraction of unaligned words"
    )
    granularity_achieved: TimestampGranularity = Field(
        default=TimestampGranularity.WORD, description="Actual granularity produced"
    )

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether alignment was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")

    # Legacy warning field for backward compatibility during migration
    warning: dict | None = Field(default=None, description="Deprecated: use warnings")


class DiarizeOutput(BaseModel):
    """Output from diarization stage."""

    model_config = ConfigDict(extra="forbid")

    turns: list[SpeakerTurn] = Field(..., description="Speaker segments")
    speakers: list[str] = Field(..., description="All speaker IDs")
    num_speakers: int = Field(..., ge=0, description="Number of speakers found")

    # Overlap statistics
    overlap_duration: float = Field(
        default=0.0, ge=0, description="Total overlap in seconds"
    )
    overlap_ratio: float = Field(
        default=0.0, ge=0, le=1, description="Fraction with overlap"
    )

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether diarization was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class TranscriptMetadata(BaseModel):
    """Metadata for final transcript output."""

    model_config = ConfigDict(extra="forbid")

    audio_duration: float = Field(..., ge=0, description="Audio duration in seconds")
    audio_channels: int = Field(..., ge=1, description="Original audio channels")
    sample_rate: int = Field(..., gt=0, description="Audio sample rate")
    language: str = Field(..., description="Primary language code")
    language_confidence: float = Field(
        default=1.0, ge=0, le=1, description="Language detection confidence"
    )
    word_timestamps: bool = Field(
        default=False, description="Whether word timestamps are available"
    )
    word_timestamps_requested: bool = Field(
        default=False, description="Whether word timestamps were requested"
    )
    speaker_detection: SpeakerDetectionMode = Field(
        default=SpeakerDetectionMode.NONE, description="Speaker detection mode used"
    )
    speaker_count: int = Field(default=0, ge=0, description="Number of speakers")
    created_at: str = Field(..., description="ISO 8601 timestamp")
    completed_at: str = Field(..., description="ISO 8601 timestamp")
    pipeline_stages: list[str] = Field(
        default_factory=list, description="Stages that ran"
    )
    pipeline_warnings: list = Field(
        default_factory=list, description="Warnings from pipeline"
    )


class MergeOutput(BaseModel):
    """Output from merge stage - the final transcript."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., description="Job identifier")
    version: str = Field(default="1.0", description="Schema version")
    metadata: TranscriptMetadata = Field(..., description="Transcript metadata")
    text: str = Field(..., description="Full transcript text")
    speakers: list[Speaker] = Field(default_factory=list, description="Speaker list")
    segments: list[MergedSegment] = Field(..., description="Transcript segments")
    paragraphs: list = Field(default_factory=list, description="Paragraph groupings")
    summary: str | None = Field(default=None, description="AI-generated summary")

    # PII detection results (M26 - optional)
    redacted_text: str | None = Field(
        default=None, description="Transcript with PII redacted"
    )
    pii_entities: list["PIIEntity"] | None = Field(
        default=None, description="Detected PII entities"
    )
    pii_metadata: "PIIMetadata | None" = Field(
        default=None, description="PII detection metadata"
    )


# =============================================================================
# PII Detection Types (M26)
# =============================================================================


class PIIEntityCategory(str, Enum):
    """PII entity category for compliance classification."""

    PII = "pii"  # Personal: name, email, phone, SSN, etc.
    PCI = "pci"  # Payment: credit card, IBAN, CVV, etc.
    PHI = "phi"  # Health: MRN, conditions, medications, etc.


class PIIDetectionTier(str, Enum):
    """PII detection tier controlling speed/accuracy tradeoff."""

    FAST = "fast"  # Presidio regex only (<5ms)
    STANDARD = "standard"  # Presidio + GLiNER (~100ms)
    THOROUGH = "thorough"  # Presidio + GLiNER + LLM (1-3s)


class PIIRedactionMode(str, Enum):
    """Audio redaction mode."""

    SILENCE = "silence"  # Replace with silence (volume=0)
    BEEP = "beep"  # Replace with 1kHz tone


class PIIEntity(BaseModel):
    """Detected PII entity with position and timing information."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(
        ..., description="Entity type (e.g., 'credit_card_number')"
    )
    category: PIIEntityCategory = Field(..., description="Category: pii, pci, phi")
    start_offset: int = Field(..., ge=0, description="Character offset in text")
    end_offset: int = Field(..., ge=0, description="Character offset in text")
    start_time: float = Field(..., ge=0, description="Audio time in seconds")
    end_time: float = Field(..., ge=0, description="Audio time in seconds")
    confidence: float = Field(..., ge=0, le=1, description="Detection confidence")
    speaker: str | None = Field(default=None, description="Speaker ID if diarized")
    redacted_value: str = Field(
        ..., description="Redacted representation (e.g., '****7890')"
    )
    original_text: str = Field(..., description="Original detected text")


class PIIMetadata(BaseModel):
    """Metadata about PII detection results."""

    model_config = ConfigDict(extra="forbid")

    detection_tier: PIIDetectionTier = Field(..., description="Detection tier used")
    entities_detected: int = Field(..., ge=0, description="Total entities detected")
    entity_count_by_type: dict[str, int] = Field(
        default_factory=dict, description="Count per entity type"
    )
    entity_count_by_category: dict[str, int] = Field(
        default_factory=dict, description="Count per category"
    )
    redacted_audio_uri: str | None = Field(
        default=None, description="URI to redacted audio file"
    )
    processing_time_ms: int = Field(..., ge=0, description="Processing time in ms")


class PIIDetectOutput(BaseModel):
    """Output from PII detection stage."""

    model_config = ConfigDict(extra="forbid")

    entities: list[PIIEntity] = Field(..., description="Detected PII entities")
    redacted_text: str = Field(..., description="Text with PII redacted")
    entity_count_by_type: dict[str, int] = Field(
        default_factory=dict, description="Count per entity type"
    )
    entity_count_by_category: dict[str, int] = Field(
        default_factory=dict, description="Count per category"
    )
    detection_tier: PIIDetectionTier = Field(..., description="Detection tier used")
    processing_time_ms: int = Field(..., ge=0, description="Processing time in ms")

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether detection was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class AudioRedactOutput(BaseModel):
    """Output from audio redaction stage."""

    model_config = ConfigDict(extra="forbid")

    redacted_audio_uri: str = Field(..., description="URI to redacted audio file")
    redaction_mode: PIIRedactionMode = Field(..., description="Redaction mode used")
    buffer_ms: int = Field(..., ge=0, description="Buffer padding in milliseconds")
    entities_redacted: int = Field(..., ge=0, description="Number of entities redacted")
    redaction_map: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Map of redacted time ranges with entity types",
    )

    # Standard output fields
    engine_id: str = Field(..., description="Engine identifier")
    skipped: bool = Field(default=False, description="Whether redaction was skipped")
    skip_reason: str | None = Field(default=None, description="Reason if skipped")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


# =============================================================================
# Type aliases for convenience
# =============================================================================

# Previous outputs dict with typed values
PreviousOutputs = dict[
    str,
    PrepareOutput
    | TranscribeOutput
    | AlignOutput
    | DiarizeOutput
    | PIIDetectOutput
    | AudioRedactOutput
    | None,
]
