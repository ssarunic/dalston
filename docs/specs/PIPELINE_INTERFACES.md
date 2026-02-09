# Pipeline Stage Interface Specification

> **Status:** Draft
> **Version:** 0.1
> **Date:** February 2026

This document defines the normalized data interfaces between pipeline stages in Dalston's modular transcription system.

---

## Table of Contents

1. [Design Principles](#design-principles)
2. [Timestamp Granularity](#timestamp-granularity)
3. [Core Data Structures](#core-data-structures)
4. [Stage Interface Definitions](#stage-interface-definitions)
5. [Model Capability Matrix](#model-capability-matrix)
6. [Input Parameter Mapping](#input-parameter-mapping)
7. [Output Field Mapping](#output-field-mapping)
8. [Unmapped Parameters & Edge Cases](#unmapped-parameters--edge-cases)
9. [Realtime vs Batch Differences](#realtime-vs-batch-differences)
10. [Open Questions](#open-questions)

---

## Design Principles

### 1. Data-Driven Skip Decisions

Each stage inspects its input data and decides whether processing is needed. The orchestrator does not need to know model capabilities.

**Example:** The aligner checks "do these segments have word timestamps with adequate quality?" rather than the orchestrator checking "did we use faster-whisper with word_timestamps=True?"

### 2. Explicit Capability Signaling

Instead of implicit checks (e.g., "does `words` exist?"), use explicit fields:

- `timestamp_granularity_requested` vs `timestamp_granularity_actual`
- `skipped` with `skip_reason`
- `warnings` list for graceful degradation

### 3. NaN for Missing Confidence

Use `NaN` (not-a-number) instead of null/None for confidence values. This allows downstream calculations without null-checking.

### 4. Escape Hatches

Every stage accepts `engine_params` for model-specific configuration that doesn't fit the normalized interface.

### 5. Single Timeline

All timestamps are relative to the original audio. **No stage modifies the audio timeline.** This ensures:

- Diarization and transcription timestamps align without mapping
- Final output timestamps match original audio for video sync
- Debugging is straightforward (one timeline to reason about)

---

## Timestamp Granularity

### Granularity Levels

| Level | Description | Typical Use Case |
|-------|-------------|------------------|
| **none** | No timestamps | Text-only output |
| **segment** | Utterance/sentence boundaries | Basic transcription |
| **word** | Individual word boundaries | Subtitles, karaoke |
| **character** | Per-character timing | Lip sync, accessibility |
| **phoneme** | IPA phoneme boundaries | Linguistics, pronunciation |

### Hierarchy

```
phoneme ⊃ character ⊃ word ⊃ segment ⊃ none
```

A model producing phoneme-level timestamps can satisfy requests for any coarser granularity.

### Industry Support

| API/Model | Supported Granularities |
|-----------|------------------------|
| OpenAI Whisper API | segment, word |
| Deepgram | word, utterance |
| NeMo CTC/TDT | segment, word, character |
| WhisperX | word, character, phoneme |

---

## Core Data Structures

### Phoneme

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| phoneme | string | yes | IPA symbol (e.g., "ð", "ə") |
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| confidence | float | no | 0.0-1.0, NaN if unavailable |
| stress | int | no | 0=unstressed, 1=primary, 2=secondary |

### Character

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| char | string | yes | Single character |
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| confidence | float | no | 0.0-1.0, NaN if unavailable |
| phonemes | Phoneme[] | no | Source phonemes if derived |

### Word

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| text | string | yes | The word text |
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| confidence | float | no | 0.0-1.0, NaN if unavailable |
| characters | Character[] | no | Character-level timing |
| phonemes | Phoneme[] | no | Phoneme-level timing |
| alignment_method | enum | no | How timestamps were produced |

### Alignment Method Enum

| Value | Description | Models |
|-------|-------------|--------|
| attention | Cross-attention alignment | Whisper |
| ctc | CTC forced alignment | wav2vec2, NeMo |
| rnnt | RNNT alignment | Parakeet RNNT |
| tdt | Token-Duration Transducer | Parakeet TDT |
| phoneme_wav2vec | wav2vec2 phoneme model | WhisperX |
| phoneme_mms | MMS phoneme model | WhisperX |
| mfa | Montreal Forced Aligner | MFA |
| wfst | WFST decoding | Kaldi/Vosk |
| unknown | Not specified | — |

### Segment

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | no | Stable ID for incremental updates |
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| text | string | yes | Transcript text |
| words | Word[] | no | Word-level detail |
| confidence | float | no | Segment-level confidence, NaN if unavailable |
| language | string | no | ISO 639-1 code (for code-switching) |
| is_speech | bool | no | False for music/noise segments |
| is_final | bool | no | False for interim realtime results |

### Speaker Turn

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| speaker | string | yes | Speaker ID (e.g., "SPEAKER_00") |
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| confidence | float | no | 0.0-1.0, NaN if unavailable |
| overlapping_speakers | string[] | no | Other speakers during overlap |

### Speech Region

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| start | float | yes | Start time in seconds |
| end | float | yes | End time in seconds |
| confidence | float | no | VAD confidence, NaN if unavailable |

---

## Stage Interface Definitions

### 1. Audio Preprocessor

**Purpose:** Normalize audio format, extract metadata, optionally detect speech regions. **Does not modify timeline.**

#### Input Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| audio_uri | string | yes* | — | S3/file URI |
| audio_bytes | bytes | yes* | — | Raw bytes (realtime) |
| target_sample_rate | int | no | 16000 | Target sample rate |
| target_channels | int | no | 1 | 1=mono, 2=stereo |
| target_encoding | string | no | pcm_s16le | Audio encoding |
| normalize_volume | bool | no | true | Apply volume normalization |
| detect_speech_regions | bool | no | false | Run VAD to detect speech (metadata only) |
| split_channels | bool | no | false | Split to separate files |
| engine_params | dict | no | null | Engine-specific params |

*One of audio_uri or audio_bytes required

#### Output Fields

| Field | Type | Description |
|-------|------|-------------|
| audio_uri | string | Location of processed audio |
| channel_uris | string[] | Per-channel files if split |
| duration | float | Total duration in seconds |
| sample_rate | int | Actual sample rate |
| channels | int | Actual channel count |
| original_channels | int | Original channel count |
| peak_amplitude | float | Peak amplitude |
| rms_amplitude | float | RMS amplitude |
| speech_regions | SpeechRegion[] | Detected speech regions (if detect_speech_regions=true) |
| speech_ratio | float | Fraction of audio containing speech |
| skipped | bool | Whether processing was skipped |
| skip_reason | string | Reason if skipped |
| warnings | string[] | Any warnings |

#### VAD in Preprocessing vs Transcription

| Aspect | Preprocessing (`detect_speech_regions`) | Transcription (`vad_filter`) |
|--------|----------------------------------------|------------------------------|
| Purpose | Provide speech region metadata | Skip inference on non-speech |
| Modifies audio | No | No |
| Modifies timeline | No | No |
| Output | `speech_regions` metadata | Transcription skips silent parts |
| Use case | Models without built-in VAD | Compute optimization |

**Key principle:** Preprocessing detects and reports; transcription acts on it. Neither modifies the audio timeline.

---

### 2. Transcriber

**Purpose:** Convert audio to text with timestamps.

#### Input Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| **Language** | | | | |
| language | string | no | null | ISO 639-1 code, null=auto-detect |
| task | enum | no | transcribe | "transcribe" or "translate" |
| **Timestamps** | | | | |
| timestamp_granularity | enum | no | word | none/segment/word/character/phoneme |
| **Decoding Hints** | | | | |
| initial_prompt | string | no | null | Domain vocabulary hints |
| hotwords | string[] | no | null | Terms to boost |
| suppress_tokens | string[] | no | null | Tokens to suppress |
| suppress_blank | bool | no | true | Filter empty segments |
| **Vocabulary Boosting** | | | | |
| vocabulary_boost | VocabularyBoost[] | no | null | Terms to boost recognition (see below) |
| **Processing** | | | | |
| vad_filter | bool | no | true | Skip inference on non-speech regions |
| speech_regions | SpeechRegion[] | no | null | Pre-detected speech regions (from preprocessing) |
| temperature | float or float[] | no | 0.0 | Decoding temperature(s) |
| beam_size | int | no | null | Beam search width |
| best_of | int | no | null | Number of candidates |
| patience | float | no | null | Beam search patience |
| length_penalty | float | no | null | Length penalty |
| max_segment_length | float | no | null | Max segment duration |
| **Escape Hatch** | | | | |
| engine_params | dict | no | null | Model-specific parameters |

#### VAD Behavior

| Model | Built-in VAD | `vad_filter` Behavior | `speech_regions` Behavior |
|-------|--------------|----------------------|---------------------------|
| faster-whisper | Yes (Silero) | Uses built-in Silero VAD | Ignored (uses built-in) |
| Parakeet | No | Runs external Silero VAD | Uses provided regions |
| Canary-Qwen | No | Runs external Silero VAD | Uses provided regions |
| Vosk | Yes | Uses built-in VAD | Ignored |

If `speech_regions` provided and model lacks built-in VAD, transcription only processes those regions. Timestamps remain relative to original audio.

#### Vocabulary Boosting

Custom vocabulary, hotwords, and taxonomy input help improve transcription accuracy for domain-specific terms, proper nouns, company names, and technical jargon.

##### VocabularyBoost Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| term | string | yes | Word or phrase to boost |
| boost | float | no | Boost score (-100 to +100), default +50 |

##### Vocabulary Boosting Methods

| Method | Description | Models |
|--------|-------------|--------|
| **initial_prompt** | Text prepended to decoder context; biases toward style/spelling | Whisper, Granite |
| **hotwords** | Simple word list with implicit boosting | faster-whisper (v1.x+), Vosk |
| **word_boosting** | Weighted word list with explicit boost scores | NeMo (Parakeet, Canary) |
| **llm_context** | Large context window via LLM decoder for domain knowledge | Canary-Qwen, Granite Speech |
| **fst_recompilation** | Rebuild decoding graph with custom vocabulary/LM | Vosk/Kaldi |

##### Key Distinctions

| Feature | initial_prompt | vocabulary_boost |
|---------|---------------|------------------|
| **Purpose** | Style/spelling hints | Recognition biasing |
| **Format** | Natural language sentence | Structured word list |
| **Scope** | First 30s only (Whisper) | Entire audio |
| **Mechanism** | Decoder context seeding | Beam search score adjustment |
| **New words** | No (vocabulary frozen) | No (vocabulary frozen) |
| **Use case** | "Spell names correctly" | "Recognize these terms more often" |

**Important limitation:** All models have frozen vocabularies from training. Vocabulary boosting guides the decoder toward existing tokens—it cannot add truly new words the model has never seen. The model must have encountered similar subword tokens during training.

##### Model Vocabulary Capabilities

| Model | initial_prompt | hotwords | word_boosting | Max Context | Notes |
|-------|---------------|----------|---------------|-------------|-------|
| faster-whisper | ✓ (224 tokens) | ✓ (v1.x+) | ✗ | 224 tokens | Prompt applies to first segment only |
| Distil-Whisper | ✓ (224 tokens) | ✗ | ✗ | 224 tokens | Same as Whisper |
| Parakeet RNNT | ✗ | ✗ | ✓ (GPU-PB) | Utterance | Via NeMo word boosting |
| Parakeet TDT | ✗ | ✗ | ✓ (GPU-PB) | Utterance | Via NeMo word boosting |
| Canary-Qwen | ✗ | ✗ | ✓ (GPU-PB) | 128K tokens | LLM decoder enables long context |
| Granite Speech | ✓ (prefix) | ✗ | ✗ | 128K tokens | Two-pass: transcribe then refine |
| Omnilingual ASR | ✓ (few-shot) | ✗ | ✗ | Model-dependent | In-context learning examples |
| Vosk/Kaldi | ✗ | ✓ (vocab) | ✓ (FST) | Model-dependent | Requires FST recompilation |

##### Effectiveness Guidelines

| Use Case | Recommended Method | Expected Improvement |
|----------|-------------------|---------------------|
| Proper nouns (names) | initial_prompt or vocabulary_boost | High (spelling correction) |
| Company names | vocabulary_boost with high score | High |
| Technical jargon | vocabulary_boost | Medium-High |
| Acronyms | initial_prompt with expansion | Medium |
| Foreign words in English | vocabulary_boost | Low-Medium |
| Suppress misrecognitions | vocabulary_boost with negative score | Medium |

##### Handling Unsupported Features

| Parameter | Unsupported Models | Handling |
|-----------|-------------------|----------|
| initial_prompt | Parakeet, CTC models | Log warning, ignore |
| hotwords | Parakeet, Canary, Granite | Log warning, ignore |
| vocabulary_boost | Whisper, Distil-Whisper | Convert to initial_prompt if possible, else ignore with warning |

When `vocabulary_boost` is provided to a model that only supports `initial_prompt`, the engine may attempt automatic conversion to a comma-separated term list. This is lossy (no boost scores, limited context) and triggers a warning.

#### Output Fields

| Field | Type | Description |
|-------|------|-------------|
| segments | Segment[] | Transcript segments |
| text | string | Full transcript text |
| language | string | Detected/used language |
| language_confidence | float | Language detection confidence |
| duration | float | Audio duration |
| timestamp_granularity_requested | enum | What was requested |
| timestamp_granularity_actual | enum | What was produced |
| engine_id | string | Which engine produced this |
| skipped | bool | Whether processing was skipped |
| skip_reason | string | Reason if skipped |
| warnings | string[] | Any warnings or degradations |

---

### 3. Aligner

**Purpose:** Refine timestamps to finer granularity via forced alignment.

#### Input Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| segments | Segment[] | yes | — | Segments from transcription |
| language | string | yes | — | Language for phoneme models |
| target_granularity | enum | no | word | word/character/phoneme |
| realign_if_quality_below | float | no | null | Quality threshold 0.0-1.0 |
| return_char_alignments | bool | no | false | Include character timing |
| return_phoneme_alignments | bool | no | false | Include phoneme timing |
| engine_params | dict | no | null | Engine-specific params |

#### Output Fields

| Field | Type | Description |
|-------|------|-------------|
| segments | Segment[] | Segments with refined timestamps |
| alignment_confidence | float | Overall alignment quality |
| unaligned_words | string[] | Words that couldn't be aligned |
| unaligned_ratio | float | Fraction of unaligned words |
| granularity_achieved | enum | Actual granularity produced |
| engine_id | string | Which engine produced this |
| skipped | bool | Whether alignment was skipped |
| skip_reason | string | Reason if skipped |
| warnings | string[] | Any warnings |

#### Skip Conditions

| Condition | Skip? | Reason |
|-----------|-------|--------|
| All segments already have target granularity | Yes | "target granularity already met" |
| Timestamps exist but quality below threshold | No | Proceed with re-alignment |
| No phoneme model for language | Partial | Degrade to coarser granularity, add warning |

---

### 4. Diarizer

**Purpose:** Identify who spoke when (speaker segmentation).

#### Input Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| num_speakers | int | no | null | Exact speaker count, null=auto |
| min_speakers | int | no | null | Minimum for auto-detect |
| max_speakers | int | no | null | Maximum for auto-detect |
| speaker_embeddings | dict | no | null | Known speaker embeddings |
| detect_overlap | bool | no | true | Detect overlapping speech |
| engine_params | dict | no | null | Engine-specific params |

#### Output Fields

| Field | Type | Description |
|-------|------|-------------|
| turns | SpeakerTurn[] | Speaker segments |
| num_speakers | int | Number of speakers found |
| speakers | string[] | All speaker IDs |
| overlap_duration | float | Total overlap in seconds |
| overlap_ratio | float | Fraction with overlap |
| engine_id | string | Which engine produced this |
| skipped | bool | Whether diarization was skipped |
| skip_reason | string | Reason if skipped |
| warnings | string[] | Any warnings |

---

### 5. Merger

**Purpose:** Combine transcription segments with diarization turns.

#### Input Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| segments | Segment[] | yes | — | Transcription segments |
| speaker_turns | SpeakerTurn[] | no | null | Diarization output |
| merge_strategy | enum | no | segment | "segment" or "word" |
| split_on_speaker_change | bool | no | false | Re-segment at speaker boundaries |

#### Output Fields

| Field | Type | Description |
|-------|------|-------------|
| segments | MergedSegment[] | Segments with speaker assignment |
| speakers | string[] | All speaker IDs |
| num_speakers | int | Number of speakers |
| language | string | Primary language |
| duration | float | Total duration |
| unassigned_segments | int | Segments without speaker match |
| reassigned_words | int | Words with different speaker than segment |
| skipped | bool | Whether merge was skipped |
| skip_reason | string | Reason if skipped |
| warnings | string[] | Any warnings |

#### Merged Segment (extends Segment)

| Field | Type | Description |
|-------|------|-------------|
| speaker | string | Assigned speaker ID |
| speaker_confidence | float | Assignment confidence |

---

## Model Capability Matrix

### Batch Transcription Models

| Model | Languages | Word TS | Char TS | Translate | VAD Built-in | Punctuation | License |
|-------|-----------|---------|---------|-----------|--------------|-------------|---------|
| faster-whisper large-v3 | 99 | ✓ (attention) | ✗ | ✓ | ✓ | ✓ | MIT |
| faster-whisper turbo | 99 | ✓ (attention) | ✗ | ✓ | ✓ | ✓ | MIT |
| Distil-Whisper | 1 (EN) | ✓ (attention) | ✗ | ✗ | ✓ | ✓ | MIT |
| Parakeet TDT 0.6B | 1 (EN) | ✓ (TDT) | ✓ (CTC) | ✗ | ✗ | ✓ | NVIDIA |
| Parakeet RNNT 0.6B | 1 (EN) | ✓ (RNNT) | ✗ | ✗ | ✗ | ✓ | NVIDIA |
| Parakeet RNNT 1.1B | 1 (EN) | ✓ (RNNT) | ✗ | ✗ | ✗ | ✓ | NVIDIA |
| Canary-Qwen 2.5B | 1 (EN) | ✓ | ✗ | ✗ | ✗ | ✓ | CC-BY-4.0 |
| Granite Speech 8B | 5 | ✓ | ✗ | ✓ (JP,ZH) | ✗ | ✓ | Apache 2.0 |
| Phi-4 Multimodal 5.6B | 8 | ✓ | ✗ | ✗ | ✗ | ✓ | MIT |
| Omnilingual ASR 300M | 1600+ | ✓ | ✗ | ✗ | ✗ | ✗ | Apache 2.0 |
| Omnilingual ASR 7B | 1600+ | ✓ | ✗ | ✗ | ✗ | ✗ | Apache 2.0 |
| MMS 1B | 1100 | ✓ (CTC) | ✗ | ✗ | ✗ | ✗ | CC-BY-NC |
| CrisperWhisper | 2 (EN,DE) | ✓ | ✗ | ✗ | ✓ | ✗ | CC-BY-NC |
| wav2vec2/XLS-R | 53+ | ✓ (CTC) | ✗ | ✗ | ✗ | ✗ | Apache 2.0 |

### Realtime Transcription Models

| Model | Languages | Word TS | Typical Latency | VAD Built-in | Punctuation | License |
|-------|-----------|---------|-----------------|--------------|-------------|---------|
| Parakeet RNNT 0.6B | 1 (EN) | ✓ | ~100ms | ✗ | ✓ | NVIDIA |
| Parakeet TDT 0.6B | 1 (EN) | ✓ | ~100ms | ✗ | ✓ | NVIDIA |
| Whisper Streaming | 99 | ✓ | ~500-1000ms | ✓ | ✓ | MIT |
| Kyutai STT 2.6B | 2 (EN,FR) | ✓ | ~1000ms | ✓ | ✓ | CC-BY-4.0 |
| WeNet U2 | 2+ (EN,ZH) | ✓ | ~500ms | ✗ | ✗ | Apache 2.0 |
| Vosk/Kaldi | 20+ | ✓ | ~200ms | ✓ | ✗ | Apache 2.0 |
| K2 Sherpa | 2 (EN,ZH) | ✓ | ~300ms | ✓ | ✗ | Apache 2.0 |
| Moonshine | 1 (EN) | ✓ | ~50ms | ✓ | ✗ | MIT |
| TorchAudio Emformer | 1 (EN) | ✓ | ~200ms | ✗ | ✗ | BSD |

### Alignment Models

| Model | Languages | Word | Char | Phoneme | Method |
|-------|-----------|------|------|---------|--------|
| WhisperX (wav2vec2) | ~40 | ✓ | ✓ | ✓ | phoneme_wav2vec |
| WhisperX (MMS) | ~1100 | ✓ | ✓ | ✓ | phoneme_mms |
| NeMo NFA | ~10 | ✓ | ✓ | ✓ | ctc |
| Montreal Forced Aligner | ~20 | ✓ | ✓ | ✓ | mfa |

### Diarization Models

| Model | Max Speakers | Overlap Detection | Streaming | License |
|-------|--------------|-------------------|-----------|---------|
| pyannote 3.1 | Unlimited | ✓ | ✗ | MIT |
| pyannote 4.0 | Unlimited | ✓ | ✓ | MIT |
| NeMo MSDD | Unlimited | ✓ | ✗ | Apache 2.0 |

---

## Input Parameter Mapping

### Language Parameter

| Model | Supported Values | Auto-detect | Validation |
|-------|------------------|-------------|------------|
| faster-whisper | ISO 639-1 (99 langs) | ✓ (null) | Warn if unknown |
| Distil-Whisper | "en" only | ✗ | Error if not EN |
| Parakeet | "en" only | ✗ | Error if not EN |
| Canary-Qwen | "en" only | ✗ | Error if not EN |
| Granite | en, fr, de, es, pt | ✓ | Error if unsupported |
| Phi-4 | en, zh, de, fr, it, ja, es, pt | ✓ | Error if unsupported |
| Omnilingual | 1600+ codes | ✓ | Warn if unknown |
| MMS | 1100 codes | ✓ | Error if unsupported |
| Kyutai | en, fr | ✓ | Error if unsupported |
| Vosk | Model-dependent (~20) | ✗ | N/A (model selection) |

### Task Parameter

| Model | transcribe | translate | Notes |
|-------|------------|-----------|-------|
| faster-whisper | ✓ | ✓ (to EN) | Full support |
| Distil-Whisper | ✓ | ✗ | Error if translate |
| Parakeet | ✓ | ✗ | Error if translate |
| Canary-Qwen | ✓ | ✗ | Error if translate |
| Granite | ✓ | ✓ (to JP,ZH) | Limited targets |
| Phi-4 | ✓ | ✗ | Error if translate |
| Omnilingual | ✓ | ✗ | Error if translate |
| All others | ✓ | ✗ | Error if translate |

### Timestamp Granularity Parameter

| Model | none | segment | word | character | phoneme |
|-------|------|---------|------|-----------|---------|
| faster-whisper | ✓ | ✓ | ✓ | ✗ | ✗ |
| Parakeet TDT | ✓ | ✓ | ✓ | ✓ | ✗ |
| Parakeet RNNT | ✓ | ✓ | ✓ | ✗ | ✗ |
| Canary-Qwen | ✓ | ✓ | ✓ | ✗ | ✗ |
| Granite | ✓ | ✓ | ✓ | ✗ | ✗ |
| CTC models | ✓ | ✓ | ✓ | ✗ | ✗ |
| WhisperX (align) | — | — | ✓ | ✓ | ✓ |
| NeMo NFA (align) | — | — | ✓ | ✓ | ✓ |

**Note:** If character/phoneme requested but model doesn't support, output `timestamp_granularity_actual` reflects what was produced, and `warnings` includes degradation notice.

### Decoding Hints

| Parameter | faster-whisper | Parakeet | Canary | Granite | Omnilingual | Vosk |
|-----------|----------------|----------|--------|---------|-------------|------|
| initial_prompt | ✓ | ✗ | ✗ | ✓ (prefix) | ✓ (few-shot) | ✗ |
| hotwords | ✓ (v1.x+) | ✗ | ✗ | ✗ | ✗ | ✓ (vocab) |
| suppress_tokens | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| suppress_blank | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |

### Processing Control

| Parameter | faster-whisper | Parakeet | Canary | Granite | CTC models |
|-----------|----------------|----------|--------|---------|------------|
| vad_filter | ✓ (built-in) | ✓ (external) | ✓ (external) | ✓ (external) | ✓ (external) |
| speech_regions | Ignored | ✓ | ✓ | ✓ | ✓ |
| temperature | ✓ (list OK) | ✗ | ✗ | ✓ (single) | ✗ |
| beam_size | ✓ | ✓ | ✓ | ✓ | ✗ (CTC) |
| best_of | ✓ | ✗ | ✗ | ✗ | ✗ |
| patience | ✓ | ✗ | ✗ | ✗ | ✗ |
| length_penalty | ✓ | ✗ | ✗ | ✓ | ✗ |

### Engine-Specific Parameters (via engine_params)

#### faster-whisper

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| compression_ratio_threshold | float | 2.4 | Max compression ratio |
| no_speech_threshold | float | 0.6 | No-speech probability threshold |
| log_prob_threshold | float | -1.0 | Average log probability threshold |
| condition_on_previous_text | bool | true | Condition on previous output |
| repetition_penalty | float | 1.0 | Penalty for repetition |
| no_repeat_ngram_size | int | 0 | Prevent n-gram repetition |

#### Parakeet

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| decoding_strategy | enum | greedy | greedy or beam |
| timestamps_type | enum | word | word, char, or segment |
| chunk_size_ms | int | 100 | Streaming chunk size |

#### Canary-Qwen

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| task_mode | enum | asr | asr or summ (summarization) |
| max_new_tokens | int | 256 | Max output tokens |

#### Vosk

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| words | bool | true | Enable word timestamps |
| partial_words | bool | false | Enable partial results |
| max_alternatives | int | 1 | N-best alternatives |

---

## Output Field Mapping

### Segment Confidence

| Model | Provides Segment Confidence? | Source |
|-------|------------------------------|--------|
| faster-whisper | ✗ | Use NaN |
| Parakeet | ✗ | Use NaN |
| Canary-Qwen | ✗ | Use NaN |
| Granite | ✗ | Use NaN |
| Vosk | ✗ | Use NaN |

**Note:** No surveyed model provides native segment-level confidence. Word confidences can be aggregated if needed.

### Word Confidence

| Model | Provides Word Confidence? | Source |
|-------|---------------------------|--------|
| faster-whisper | ✓ | Token probability |
| Parakeet | ✗ | Use NaN |
| Canary-Qwen | Variable | Model-dependent |
| Granite | Variable | Model-dependent |
| Vosk | ✓ | Lattice confidence |
| WhisperX (align) | ✓ | Alignment score |

### Language Detection

| Model | Provides language? | Provides confidence? |
|-------|-------------------|---------------------|
| faster-whisper | ✓ | ✓ |
| Parakeet | ✗ (EN-only) | ✗ |
| Canary-Qwen | ✗ (EN-only) | ✗ |
| Granite | ✓ | ✗ |
| Omnilingual | ✓ | ✗ |
| Vosk | ✗ (model-dependent) | ✗ |

### Alignment Method Mapping

| Model | alignment_method value |
|-------|----------------------|
| faster-whisper (word_timestamps=True) | attention |
| Parakeet TDT | tdt |
| Parakeet RNNT | rnnt |
| wav2vec2/MMS (standalone) | ctc |
| WhisperX with wav2vec2 | phoneme_wav2vec |
| WhisperX with MMS | phoneme_mms |
| Vosk/Kaldi | wfst |
| NeMo NFA | ctc |
| Montreal Forced Aligner | mfa |
| Unknown/not specified | unknown |

---

## Unmapped Parameters & Edge Cases

### Parameters Without Model Support

| Parameter | Models That Don't Support | Handling |
|-----------|---------------------------|----------|
| initial_prompt | Parakeet, Canary-Qwen, CTC models | Log warning, ignore |
| hotwords | Parakeet, Canary-Qwen, Granite, Phi-4 | Log warning, ignore |
| translate task | All except Whisper, Granite | Return error |
| temperature (list) | All except Whisper | Use first value |
| suppress_tokens | All except Whisper | Ignore |
| patience | All except Whisper | Ignore |
| character timestamps | Whisper, RNNT models | Degrade to word, add warning |
| phoneme timestamps | All transcribers | Degrade to word, add warning |

### Output Normalization Issues

| Issue | Affected Models | Resolution |
|-------|-----------------|------------|
| No segment confidence | All | Use NaN |
| Hardcoded word confidence | Parakeet (0.95) | Use NaN instead |
| Zero-duration words | Whisper attention | Flag in warnings, suggest re-alignment |
| No language detection | EN-only models, Vosk | Return "en" or "unknown" |
| Different phoneme alphabets | MFA (ARPAbet) | Normalize to IPA |
| Non-speech segments | SenseVoice | Set is_speech=false |

### Edge Cases

| Case | Description | Recommended Handling |
|------|-------------|---------------------|
| Audio < 1 second | Some models produce empty output | Pad audio or return empty with warning |
| Audio > model max | Whisper 30s chunks, memory limits | VAD-based chunking (preserves timeline) |
| Code-switching | Mid-sentence language change | Use segment-level language field |
| Overlapping speech | Multiple speakers simultaneously | Diarizer overlapping_speakers field |
| Background music | Non-speech audio detected | is_speech=false, SenseVoice |
| Non-Latin scripts | Limited phoneme model coverage | Degrade to word-level with warning |

---

## Realtime vs Batch Differences

### Interface Differences

| Aspect | Batch | Realtime |
|--------|-------|----------|
| Input source | File URI | Audio stream/chunks |
| Output timing | After completion | Progressive |
| Segment finality | Always is_final=true | Mix of partial and final |
| Typical latency | Seconds to minutes | <500ms target |
| Diarization | Full pipeline | Deferred or streaming (pyannote 4.0) |
| Alignment | Full pipeline | Usually skipped for latency |

### Additional Realtime Output Fields

| Field | Type | Description |
|-------|------|-------------|
| is_final | bool | False for interim results |
| audio_timestamp | float | Position in audio stream |
| processing_latency_ms | int | Time from audio arrival to result |
| stability | float | 0.0-1.0, likelihood result will change |
| alternatives | string[] | N-best hypotheses |

### Model Suitability

| Use Case | Recommended Models |
|----------|-------------------|
| Batch, multilingual | faster-whisper large-v3 |
| Batch, English max accuracy | Canary-Qwen 2.5B |
| Batch, fast processing | Distil-Whisper, Parakeet TDT |
| Batch, 100+ languages | Omnilingual ASR |
| Realtime, English | Parakeet RNNT 0.6B |
| Realtime, multilingual | Whisper streaming, Kyutai |
| Edge/browser | Moonshine, Vosk |
| Post-transcription alignment | WhisperX |

---

## Open Questions

### 1. Phoneme Alphabet Standardization

**Problem:** Different alignment models output different phoneme representations:

- WhisperX/wav2vec2: IPA (International Phonetic Alphabet)
- Montreal Forced Aligner: ARPAbet (ASCII-based, e.g., "AH" instead of "ʌ")
- NeMo: Model-dependent, often custom token sets

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Normalize to IPA** | All engines convert their native format to IPA before output |
| **B: Preserve native + indicator** | Output native format with `phoneme_alphabet` field |
| **C: Dual output** | Output both native and IPA (where conversion is possible) |

**Comparison:**

| Aspect | A: Normalize to IPA | B: Native + indicator | C: Dual output |
|--------|--------------------|-----------------------|----------------|
| Downstream simplicity | High (one format) | Low (must handle multiple) | Medium |
| Linguistic accuracy | Medium (lossy conversion) | High (native precision) | High |
| Implementation effort | High (conversion tables) | Low | Medium |
| Storage overhead | None | None | ~2x for phonemes |
| Interoperability | High (standard format) | Low | High |

**Recommendation: A (Normalize to IPA)**

IPA is the universal standard for phonetic notation. While conversion from ARPAbet to IPA is slightly lossy for edge cases (e.g., some regional variants), the benefit of a single downstream format outweighs this. Consumers shouldn't need to handle multiple phoneme alphabets.

If a use case requires native precision, it can use `engine_params` to request raw output.

---

### 2. Confidence Aggregation

**Problem:** Models provide word-level confidence but not segment-level. Downstream consumers (quality filters, UI highlighting) often need segment-level scores.

**Approaches:**

| Approach | Formula | Behavior |
|----------|---------|----------|
| **A: Arithmetic mean** | `sum(confidences) / n` | Averages all words equally |
| **B: Geometric mean** | `(prod(confidences))^(1/n)` | Penalizes low-confidence words more |
| **C: Minimum** | `min(confidences)` | Segment is only as good as worst word |
| **D: Duration-weighted mean** | `sum(conf * duration) / total_duration` | Longer words contribute more |
| **E: Don't aggregate** | Always NaN | Leave to consumer |

**Comparison:**

| Aspect | A: Arithmetic | B: Geometric | C: Minimum | D: Weighted | E: Don't |
|--------|---------------|--------------|------------|-------------|----------|
| Intuitive meaning | "Average confidence" | "Typical confidence" | "Weakest link" | "Time-weighted" | N/A |
| Sensitivity to outliers | Low | Medium | Maximum | Low | N/A |
| Useful for quality filter | Medium | High | High | Medium | No |
| Implementation | Simple | Simple | Simple | Needs durations | None |
| Risk of misleading | Medium | Low | Low (conservative) | Medium | None |

**Recommendation: E (Don't aggregate) with optional B (Geometric mean)**

The interface should output `NaN` for segment confidence by default — we shouldn't invent data the model didn't provide. However, provide a utility function or optional `compute_segment_confidence` flag that uses geometric mean when requested.

Geometric mean is preferred because:

- A segment with words at [0.95, 0.95, 0.20, 0.95] should not score 0.76 (arithmetic) — the 0.20 indicates a problem
- Geometric mean gives 0.63, which better reflects "there's a low-confidence word here"
- Minimum (0.20) is too aggressive for segments with many words

---

### 3. Streaming Alignment

**Problem:** Word-level timestamps add value (subtitles, karaoke) but alignment adds latency. In realtime, latency budget is ~100-500ms.

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Skip in realtime** | Realtime never does alignment; batch-only feature |
| **B: Inline alignment** | Run alignment in realtime, accept latency hit |
| **C: Async enhancement** | Realtime outputs segment-level; background job adds word-level later |
| **D: Model-native only** | Use word timestamps from model if available (Parakeet TDT), skip otherwise |

**Comparison:**

| Aspect | A: Skip | B: Inline | C: Async | D: Native only |
|--------|---------|-----------|----------|----------------|
| Realtime latency | None | +100-300ms | None | None |
| Word timestamps available | No | Yes | Yes (delayed) | Model-dependent |
| Implementation complexity | Low | Low | High (job system) | Low |
| User experience | Segment-only | Full but slow | Progressive | Varies by model |
| Consistency | Predictable | Predictable | Complex state | Unpredictable |

**Recommendation: D (Model-native only) with C (Async enhancement) as optional**

For realtime:

1. Use whatever word timestamps the model provides natively (Parakeet TDT has good ones)
2. Don't add alignment latency inline
3. Optionally offer "enhance this session" which spawns a batch job to re-process with full alignment

This gives immediate results with zero added latency, while allowing quality enhancement for recordings that need it (e.g., subtitle generation from a live session).

---

### 4. Cross-Model Alignment

**Problem:** If transcription uses Whisper (attention-based word timestamps) and alignment uses WhisperX (phoneme-based), should the aligner:

- Refine the existing timestamps?
- Ignore them and redo from scratch?

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Always redo** | Aligner ignores incoming timestamps, aligns text to audio fresh |
| **B: Refine if present** | Use existing timestamps as initialization hints, then refine |
| **C: Quality-based decision** | Check incoming timestamp quality; redo if poor, skip if good |

**Comparison:**

| Aspect | A: Always redo | B: Refine | C: Quality-based |
|--------|----------------|-----------|------------------|
| Consistency | High | Medium | Medium |
| Compute cost | Always full | Potentially lower | Varies |
| Accuracy | Highest (phoneme-based) | Good | Good |
| Implementation | Simple | Complex | Medium |
| Handles mixed input | Yes | Tricky | Yes |

**Recommendation: A (Always redo) with skip if quality already sufficient**

The aligner should:

1. First check: "Does input already meet target granularity with adequate quality?" → Skip if yes
2. If alignment needed: Ignore existing timestamps and align fresh

Rationale:

- Attention-based timestamps (Whisper) and phoneme-based timestamps (WhisperX) use fundamentally different methods
- "Refining" attention timestamps with phoneme alignment is not meaningfully easier than fresh alignment
- Fresh alignment is more predictable and debuggable
- The skip check handles the "already good enough" case

---

### 5. Contract Versioning

**Problem:** The interface will evolve. How do we handle breaking changes without breaking existing engines/consumers?

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Schema version field** | Every output includes `schema_version: "1.0"` |
| **B: URL-style versioning** | Different stage endpoints: `/v1/transcribe`, `/v2/transcribe` |
| **C: Feature flags** | Add fields optionally; consumers check for presence |
| **D: Never break** | Only additive changes; deprecate but don't remove |

**Comparison:**

| Aspect | A: Schema version | B: URL versioning | C: Feature flags | D: Never break |
|--------|-------------------|-------------------|------------------|----------------|
| Breaking change support | Yes | Yes | No | No |
| Migration path | Clear | Clear | Implicit | N/A |
| Maintenance burden | Medium | High (parallel versions) | Low | Low initially, high over time |
| Ecosystem complexity | Low | High | Low | Low |
| Long-term cleanliness | High | High | Medium | Low (cruft accumulates) |

**Recommendation: A (Schema version) + D (Never break) hybrid**

1. Include `schema_version` in every stage output
2. Follow additive-only changes as default policy
3. When breaking change is unavoidable:
   - Bump schema version
   - Support previous version for N releases with deprecation warning
   - Document migration path

This gives us an escape hatch for breaking changes while encouraging discipline to avoid them.

---

### 6. Punctuation Handling

**Problem:** Some models output punctuation (Whisper, Parakeet), others don't (wav2vec2, older Kaldi). Should punctuation be:

- Part of the normalized interface?
- A post-processing concern?

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Normalized field** | `Segment.punctuated_text` and `Segment.raw_text` as separate fields |
| **B: Single text, flag** | `Segment.text` with `has_punctuation: bool` indicator |
| **C: Post-processing stage** | Separate "punctuation" stage that adds punctuation to unpunctuated text |
| **D: Model responsibility** | Each model outputs whatever it outputs; no normalization |

**Comparison:**

| Aspect | A: Dual fields | B: Single + flag | C: Post-process | D: No normalization |
|--------|----------------|------------------|-----------------|---------------------|
| Storage overhead | ~2x text | None | None | None |
| Downstream clarity | High | Medium | High | Low |
| Consistency | High | Low | High | None |
| Implementation | Medium | Low | High (new stage) | None |
| Flexibility | High | Low | High | N/A |

**Recommendation: B (Single text + flag) with optional C (Post-processing)**

1. `Segment.text` contains whatever the model outputs
2. `has_punctuation: bool` indicates whether punctuation is present
3. If punctuation is needed and `has_punctuation=false`, a post-processing stage can add it

Rationale:

- Dual fields doubles text storage for marginal benefit
- Most models now include punctuation (Whisper, Parakeet, Canary)
- For models without punctuation, a dedicated punctuation model (e.g., NeMo Punctuation) can be added as optional pipeline stage
- The flag lets consumers know what they're getting

---

### 7. Verbatim vs Clean Transcription

**Problem:** Different use cases need different transcription styles:

- **Verbatim:** "Um, so I was like, you know, going to the, uh, store"
- **Clean:** "I was going to the store"

Models like CrisperWhisper preserve disfluencies; Whisper tends to clean them up.

**Approaches:**

| Approach | Description |
|----------|-------------|
| **A: Input flag** | `transcription_style: "verbatim" \| "clean"` parameter |
| **B: Model tier** | Separate model selection (e.g., "whisper-large-v3" vs "crisperwhisper") |
| **C: Post-processing** | Always transcribe verbatim; optional "cleanup" stage removes disfluencies |
| **D: Output both** | Model outputs both versions where capable |

**Comparison:**

| Aspect | A: Input flag | B: Model tier | C: Post-process | D: Output both |
|--------|---------------|---------------|-----------------|----------------|
| User control | High | Medium | High | High |
| Model compatibility | Low (few support both) | High | High | Low |
| Compute cost | Normal | Normal | Higher | Higher |
| Accuracy | Model-dependent | Highest per style | Medium (cleanup is lossy) | Model-dependent |
| Implementation | Low | Low | Medium | Low |

**Recommendation: B (Model tier) + C (Post-processing) hybrid**

1. Model selection determines base behavior:
   - CrisperWhisper → verbatim
   - Whisper → mostly clean (some disfluencies preserved)

2. Optional post-processing stages:
   - "Disfluency removal" stage for verbatim → clean
   - No good reverse (can't add disfluencies back)

3. Add `transcription_style` to output metadata indicating what was produced

Rationale:

- Most users want clean transcription (Whisper default is fine)
- Verbatim is a specialized need (legal, medical, linguistic research)
- Users needing verbatim should explicitly choose a verbatim model
- Post-processing can clean up verbatim output but cannot add back removed disfluencies
- Flag approach doesn't work because most models don't support both styles

---

## References

- [WhisperX Paper](https://arxiv.org/abs/2303.00747)
- [NVIDIA NeMo ASR Documentation](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/intro.html)
- [OpenAI Audio API Reference](https://platform.openai.com/docs/api-reference/audio/)
- [Deepgram Timestamps Documentation](https://deepgram.com/learn/working-with-timestamps-utterances-and-speaker-diarization-in-deepgram)
- [Word Level Timestamp Generation (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/hu25e_interspeech.pdf)
