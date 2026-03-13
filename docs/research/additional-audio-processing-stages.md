# Research: Additional Optional Audio Processing Stages

**Date:** 2026-03-13
**Status:** Exploratory research
**Scope:** Non-verbal event detection, speaker recognition, noise reduction, emotion detection — with focus on NVIDIA NIM/NeMo ecosystem

---

## Executive Summary

This document explores four additional optional processing stages for Dalston's pipeline:

| Stage | NVIDIA NIM Available? | Alternative Models | Integration Complexity |
|-------|----------------------|-------------------|----------------------|
| **Noise Reduction** | Yes (Maxine BNR NIM) | RNNoise, DeepFilterNet | Low — pre-processing before TRANSCRIBE |
| **Speaker Recognition** | No standalone NIM (TitaNet via NeMo) | Resemblyzer, SpeechBrain | Medium — post-DIARIZE enrichment |
| **Non-Verbal Event Detection** | No | PANNs, BEATs, Audio Spectrogram Transformer | Medium — parallel to TRANSCRIBE |
| **Emotion Detection** | No | emotion2vec, SpeechBrain, Wav2Vec2-emotion | Medium — post-TRANSCRIBE enrichment |

NVIDIA's NIM ecosystem excels at core ASR and diarization but has **significant gaps** in non-verbal detection and emotion analysis. Their strongest non-ASR offering is the **Maxine Background Noise Removal NIM** for speech enhancement. Speaker recognition via TitaNet is available through NeMo but not as a production NIM.

---

## 1. Current Pipeline Architecture

Dalston's pipeline is **capability-driven and modular**. The orchestrator builds a DAG at job submission time based on job parameters and available engine capabilities.

**Current stages:**
```
PREPARE → TRANSCRIBE → [ALIGN] → [DIARIZE] → [PII_DETECT] → [AUDIO_REDACT] → MERGE
```

**Extensibility model:**
- Each engine self-declares its stage via `engine.yaml`
- The orchestrator's `engine_selector.py` decides which stages to include
- DAG construction in `dag.py` wires dependencies
- New stages require: data types in `pipeline_types.py`, engine implementation, DAG logic, selection logic

The system already supports conditional stage inclusion (ALIGN is skipped if the transcriber has native word timestamps; DIARIZE is skipped if the transcriber includes diarization). New optional stages would follow this same pattern.

---

## 2. Proposed New Stages

### 2.1 NOISE_REDUCE (Speech Enhancement / Denoising)

**Purpose:** Remove background noise, reverb, and audio artifacts before transcription to improve ASR accuracy on noisy recordings.

**Pipeline position:** Between PREPARE and TRANSCRIBE (pre-processing)
```
PREPARE → [NOISE_REDUCE] → TRANSCRIBE → ...
```

**Trigger parameter:** `noise_reduction: bool = False` (or `noise_reduction: "auto" | "off" | "light" | "aggressive"`)

#### NVIDIA Options

**A. Maxine BNR NIM (Recommended for NVIDIA path)**
- Container: `nvcr.io/nim/nvidia/maxine-bnr:latest`
- gRPC API on port 8001
- Two modes: **Streaming** (10ms chunks) and **Transactional** (full file, up to 32MB / ~6 min)
- Configurable intensity ratio (0.0–1.0)
- Built on CUDA + TensorRT + Triton
- Production-ready, commercially licensed

**B. NeMo Speech Enhancement Models**
- Multiple architectures: Encoder-Mask-Decoder, Predictive, Score-based (diffusion), Flow Matching
- More flexible than BNR but requires custom deployment
- Better for research/fine-tuning scenarios

**C. Maxine Audio Effects SDK (non-NIM)**
- Broader capabilities: denoising, dereverb, echo cancellation, audio super-resolution, speaker focus
- SDK-based, not containerized as NIM
- "Studio Voice" effect can enhance low-quality mic recordings

#### Non-NVIDIA Alternatives

| Model | License | GPU Required | Notes |
|-------|---------|-------------|-------|
| **DeepFilterNet** | MIT | Optional (has ONNX) | State-of-art open-source denoising, ~5x real-time on CPU |
| **RNNoise** | BSD-3 | No (CPU-only) | Lightweight, real-time capable, Opus codec integration |
| **Meta Demucs** | MIT | Optional | Music source separation; can isolate vocals from background |
| **SpeechBrain SE** | Apache 2.0 | Optional | MetricGAN+, SepFormer for enhancement |

#### Recommended Engine Implementations

1. **`maxine-bnr`** — Wraps NVIDIA Maxine BNR NIM via gRPC. Best for GPU deployments already using NVIDIA stack.
2. **`deepfilternet`** — Wraps DeepFilterNet. Best for CPU-only or mixed deployments. MIT licensed.

#### Data Types

```python
class NoiseReduceOutput(BaseModel):
    enhanced_audio_artifact_id: str
    noise_reduction_method: str           # "maxine-bnr", "deepfilternet", etc.
    estimated_snr_before: float | None    # Estimated SNR before enhancement
    estimated_snr_after: float | None     # Estimated SNR after enhancement
    intensity: float                      # 0.0-1.0, applied intensity
```

#### DAG Integration

- Depends on: PREPARE (needs prepared audio)
- Downstream: TRANSCRIBE consumes enhanced audio instead of raw prepared audio
- Input binding: audio artifact from PREPARE
- Output: new audio artifact (enhanced WAV) uploaded to S3

---

### 2.2 SPEAKER_RECOGNIZE (Speaker Identification / Verification)

**Purpose:** Match detected speakers against a known speaker database to assign real identities (names) instead of anonymous labels (SPEAKER_00, SPEAKER_01).

**Pipeline position:** After DIARIZE
```
... → DIARIZE → [SPEAKER_RECOGNIZE] → [PII_DETECT] → ...
```

**Trigger parameter:** `speaker_recognition: bool = False` plus `speaker_profiles: list[str]` (IDs of enrolled speaker profiles)

#### NVIDIA Options

**TitaNet (NeMo, not a standalone NIM)**
- Architecture: Depth-wise separable Conv1D encoder
- TitaNet-Large: ~23M parameters, 0.66% EER on VoxCeleb1
- Produces fixed-length speaker embeddings from audio segments
- Supports: verification (same speaker?), identification (who is this?), clustering
- Training: angular softmax loss; inference: cosine similarity scoring
- License: CC-BY-4.0
- Available on NGC and HuggingFace, with ONNX export support

**Sortformer (within ASR NIM)**
- End-to-end neural diarizer supporting up to 4 speakers
- Available in streaming and offline modes
- Integrated into ASR NIM profiles — produces per-word speaker tags
- Does NOT do speaker identification (only anonymous labeling)

**Integration approach:** TitaNet would be deployed as a standalone engine using NeMo toolkit. It would:
1. Receive diarization output (speaker turns with timestamps)
2. Extract audio segments for each speaker
3. Compute embeddings using TitaNet
4. Compare against enrolled speaker profile embeddings
5. Assign identities based on cosine similarity threshold

#### Non-NVIDIA Alternatives

| Model | License | GPU Required | Notes |
|-------|---------|-------------|-------|
| **SpeechBrain ECAPA-TDNN** | Apache 2.0 | Optional | Strong speaker verification, HuggingFace integration |
| **Resemblyzer** | Apache 2.0 | No | GE2E-based, lightweight, good for comparison |
| **Pyannote embedding** | MIT | Optional | Same ecosystem as our pyannote diarizer |
| **WeSpeaker** | Apache 2.0 | Optional | ResNet/ECAPA variants, ONNX export |

#### Data Types

```python
class SpeakerProfile(BaseModel):
    profile_id: str
    name: str
    embedding: list[float]  # Pre-computed speaker embedding

class SpeakerMatch(BaseModel):
    anonymous_label: str      # "SPEAKER_00"
    matched_profile_id: str | None
    matched_name: str | None
    confidence: float         # Cosine similarity score

class SpeakerRecognizeOutput(BaseModel):
    speaker_matches: list[SpeakerMatch]
    unmatched_speakers: list[str]     # Speakers with no profile match
    threshold_used: float             # Similarity threshold
```

#### Design Considerations

- **Speaker enrollment API**: Need a separate API endpoint for uploading speaker voice samples and computing/storing reference embeddings. This is outside the job pipeline.
- **Storage**: Speaker profiles stored in Postgres, embeddings in a vector column or serialized blob.
- **Privacy**: Speaker embeddings are biometric data — PII implications.
- **Threshold tuning**: Cosine similarity threshold significantly impacts false accept/reject rates. Should be configurable per-job.

---

### 2.3 NONVERBAL_DETECT (Non-Verbal Event Detection)

**Purpose:** Detect and annotate non-speech audio events — laughter, applause, music, coughing, sighing, crying, etc. Useful for meeting transcripts, media captioning, and accessibility.

**Pipeline position:** Parallel to TRANSCRIBE (operates on prepared audio independently)
```
PREPARE → TRANSCRIBE → ...
   └────→ NONVERBAL_DETECT ──→ (merged into final transcript)
```

**Trigger parameter:** `nonverbal_detection: bool = False` (or `nonverbal_events: list[str]` to specify which event types)

#### NVIDIA Options

**NVIDIA has no dedicated model or NIM for this capability.** This is a clear gap in their offering.

- Parakeet models handle non-speech segments gracefully (don't hallucinate text during music/silence) but do **not label** them
- MarbleNet (VAD) only classifies speech vs. non-speech binary
- MatchboxNet classifies short speech commands, not environmental sounds

#### Recommended Models

| Model | Architecture | License | Classes | Notes |
|-------|-------------|---------|---------|-------|
| **BEATs** (Microsoft) | Audio Transformer | MIT | AudioSet (527) | State-of-art audio classification, iterative pre-training |
| **Audio Spectrogram Transformer (AST)** | ViT-based | Apache 2.0 | AudioSet (527) | Google, strong zero-shot |
| **PANNs** (CNN14) | CNN | MIT | AudioSet (527) | Proven, lightweight, good baseline |
| **CLAP** (LAION/Microsoft) | Contrastive audio-text | Various | Open-vocabulary | Zero-shot via text queries ("laughter", "applause") |
| **Whisper** (w/ special tokens) | Transformer | MIT | Limited | Whisper already detects `[LAUGHTER]`, `[MUSIC]` etc. but inconsistently |

**Recommended approach:** BEATs or AST for classification, with a curated subset of AudioSet classes relevant to speech contexts:

```python
SPEECH_CONTEXT_EVENTS = [
    "Laughter", "Applause", "Music", "Crying", "Cough",
    "Sneeze", "Sigh", "Yawn", "Throat_clearing",
    "Door_slam", "Typing", "Phone_ringing", "Dog_bark",
    "Background_noise", "Silence",
]
```

#### Data Types

```python
class NonVerbalEvent(BaseModel):
    event_type: str           # "laughter", "applause", "music", etc.
    start: float              # Start time in seconds
    end: float                # End time in seconds
    confidence: float         # Detection confidence
    speaker: str | None       # If attributable to a speaker (from diarization)

class NonVerbalDetectOutput(BaseModel):
    events: list[NonVerbalEvent]
    event_counts: dict[str, int]    # Summary counts per event type
    audio_quality_score: float | None  # Overall audio quality estimate
```

#### Design Considerations

- **Windowed inference**: Process audio in overlapping windows (e.g., 2s with 0.5s hop), aggregate detections
- **Threshold per class**: Different event types need different confidence thresholds
- **Merger integration**: Non-verbal events should be interleaved into the final transcript at appropriate timestamps (e.g., `[laughter]` inserted between words)
- **AudioSet class mapping**: AudioSet has 527 classes; we'd expose a curated subset and allow users to specify which ones they care about
- **Parallel execution**: This stage is independent of TRANSCRIBE and can run in parallel — good for latency

---

### 2.4 EMOTION_DETECT (Speech Emotion Recognition)

**Purpose:** Detect emotions/sentiment from speech — useful for call center analytics, meeting sentiment analysis, and media annotation.

**Pipeline position:** After TRANSCRIBE (and optionally after DIARIZE for per-speaker emotions)
```
... → TRANSCRIBE → [DIARIZE] → [EMOTION_DETECT] → ...
```

**Trigger parameter:** `emotion_detection: bool = False`

#### NVIDIA Options

**NVIDIA has no dedicated speech emotion model or NIM.** NeMo lists "Audio Sentiment Classification" as a task category but has no prominent pretrained model. The NVIDIA ecosystem approach would be: transcribe → NLP-based sentiment on text.

#### Recommended Models

| Model | Approach | License | Emotions | Notes |
|-------|----------|---------|----------|-------|
| **emotion2vec** (FunASR/Alibaba) | Self-supervised audio | Apache 2.0 | 9 classes | State-of-art SER, directly from audio |
| **Wav2Vec2-emotion** (various) | Fine-tuned Wav2Vec2 | Various | Varies (4-8) | Multiple fine-tuned checkpoints on HuggingFace |
| **SpeechBrain SER** | ECAPA-TDNN/Wav2Vec2 | Apache 2.0 | IEMOCAP (4) | Well-integrated with speaker verification |
| **Whisper + LLM** | Hybrid | MIT + varies | Open-ended | Transcribe → LLM emotion analysis on text+prosody features |

**Recommended approach:** Dual-signal emotion detection:
1. **Audio-based** (emotion2vec or Wav2Vec2-emotion): Captures prosody, tone, speaking rate
2. **Text-based** (LLM on transcript): Captures semantic content, word choice

Combined scoring would give more robust results than either alone.

#### Data Types

```python
class EmotionScore(BaseModel):
    emotion: str              # "neutral", "happy", "sad", "angry", "fearful", "disgusted", "surprised"
    score: float              # 0.0-1.0

class SegmentEmotion(BaseModel):
    segment_id: str           # Reference to transcript segment
    start: float
    end: float
    speaker: str | None
    dominant_emotion: str
    scores: list[EmotionScore]
    arousal: float | None     # Low (calm) to high (excited), 0.0-1.0
    valence: float | None     # Negative to positive, 0.0-1.0

class EmotionDetectOutput(BaseModel):
    segment_emotions: list[SegmentEmotion]
    overall_sentiment: str                     # "positive", "negative", "neutral", "mixed"
    speaker_emotion_summary: dict[str, dict[str, float]] | None  # Per-speaker emotion distribution
```

#### Design Considerations

- **Granularity**: Emotion per segment vs. per utterance vs. per speaker turn. Segment-level aligns with existing transcript structure.
- **Audio vs. text**: Audio-based models capture prosody (tone, pitch, rate); text-based captures semantics. Both have different failure modes.
- **Cultural sensitivity**: Emotion expression varies by culture; model calibration matters.
- **Per-speaker tracking**: With diarization output, can track each speaker's emotional trajectory across a conversation.
- **Latency**: Emotion models are relatively lightweight — ~10ms per segment on GPU.

---

## 3. NVIDIA NIM/NeMo Ecosystem Context

### What NVIDIA Provides for Speech AI

#### Production NIMs (Containerized, API-ready)

| NIM | Capability | Dalston Relevance |
|-----|-----------|-------------------|
| **Speech NIM (ASR)** | Parakeet/Canary/Whisper + Sortformer diarization | Already integrated as `riva` and `nemo` engines |
| **Maxine BNR NIM** | Background noise removal | New NOISE_REDUCE stage |
| **Speech NIM (TTS)** | Text-to-speech | Not relevant |
| **Speech NIM (NMT)** | Neural machine translation | Could be a future TRANSLATE stage |

#### NeMo Framework Models (Require Custom Deployment)

| Model | Capability | Dalston Relevance |
|-------|-----------|-------------------|
| **TitaNet** | Speaker embeddings | SPEAKER_RECOGNIZE stage |
| **MarbleNet** | Voice activity detection | Internal to ASR, not standalone stage |
| **Sortformer** | End-to-end diarization | Already available via ASR NIM profiles |
| **MSDD** | Multi-scale diarization decoder | Already integrated as `nemo-msdd` engine |

#### Gaps in NVIDIA's Offering

1. **No non-verbal event detection** — Need third-party models (BEATs, AST, PANNs)
2. **No speech emotion recognition** — Need third-party models (emotion2vec, Wav2Vec2-emotion)
3. **No standalone speaker recognition NIM** — TitaNet available but requires NeMo deployment
4. **No audio tagging/classification NIM** — Only binary VAD (MarbleNet)

### Parakeet vs. Canary Architecture

| Aspect | Parakeet | Canary |
|--------|----------|--------|
| Architecture | FastConformer + CTC/RNNT/TDT | FastConformer + Transformer decoder |
| Primary task | ASR (monolingual or multilingual) | ASR + speech-to-text translation |
| Languages | 1 (EN) or 25 (multilingual variants) | 4–25 depending on version |
| Streaming | Yes (all decoders) | Yes (chunked) |
| Timestamps | Yes (TDT variant is best) | Experimental (Flash variant) |
| Latest | parakeet-tdt-0.6b-v3 (multilingual, 25 langs) | canary-1b-v2 (25 langs, tops Open ASR Leaderboard) |
| Special | TDT decoder is 64% faster than RNNT | Canary-Qwen-2.5B adds LLM post-processing |

### How NVIDIA Composes These in NIMs

NVIDIA's ASR NIM profiles bundle multiple components into a single deployment:

```
ASR NIM Profile (e.g., parakeet-1-1b-ctc-en-us):
├── Silero VAD (utterance endpoint detection)
├── Parakeet CTC 1.1B (ASR model)
├── Punctuation & Capitalization (post-processor)
├── Sortformer (optional diarization, per-word speaker tags)
└── Triton Inference Server (orchestration)
```

This monolithic approach differs from Dalston's modular pipeline. For Dalston, we maintain the modular approach and use NIMs as individual engines rather than adopting their monolithic bundling.

---

## 4. Proposed Pipeline Extensions

### Extended Pipeline (All Optional Stages)

```
PREPARE
  ├──→ [NOISE_REDUCE] ──→ TRANSCRIBE
  │                           ├──→ [ALIGN]
  │                           ├──→ [DIARIZE] ──→ [SPEAKER_RECOGNIZE]
  │                           └──→ [EMOTION_DETECT]
  └──→ [NONVERBAL_DETECT] ─────────────────────────────────────────→ MERGE
                                                                       ↓
                                                               [PII_DETECT]
                                                                       ↓
                                                              [AUDIO_REDACT]
```

### Stage Dependencies

| Stage | Depends On | Optional? | Trigger Parameter |
|-------|-----------|-----------|-------------------|
| NOISE_REDUCE | PREPARE | Yes | `noise_reduction=true` |
| TRANSCRIBE | PREPARE or NOISE_REDUCE | No | Always |
| ALIGN | TRANSCRIBE | Yes | `timestamps_granularity="word"` (existing) |
| DIARIZE | PREPARE, TRANSCRIBE | Yes | `speaker_detection="diarize"` (existing) |
| NONVERBAL_DETECT | PREPARE | Yes | `nonverbal_detection=true` |
| SPEAKER_RECOGNIZE | DIARIZE | Yes | `speaker_recognition=true` |
| EMOTION_DETECT | TRANSCRIBE, [DIARIZE] | Yes | `emotion_detection=true` |
| PII_DETECT | Best transcript | Yes | `pii_detection=true` (existing) |
| AUDIO_REDACT | PII_DETECT, PREPARE | Yes | `redact_pii_audio=true` (existing) |
| MERGE | All completed stages | No | Always |

### Implementation Priority

**Recommended order based on user value, implementation complexity, and model maturity:**

1. **NOISE_REDUCE** — Highest impact on ASR quality; Maxine BNR NIM is production-ready; simplest integration (pre-processing, single dependency)
2. **NONVERBAL_DETECT** — High user demand for meeting/media transcription; mature models available (BEATs/AST); can run parallel to TRANSCRIBE
3. **EMOTION_DETECT** — Growing demand for call center analytics; emotion2vec is strong; relatively straightforward post-processing
4. **SPEAKER_RECOGNIZE** — Niche but high value for repeat-speaker scenarios; requires speaker enrollment infrastructure (API + storage) which adds scope

---

## 5. Integration Patterns

### Adding a New Stage to Dalston (Checklist)

Based on the existing architecture, each new stage requires:

1. **Data types** in `dalston/common/pipeline_types.py` — Input/output Pydantic models
2. **Engine directory** at `engines/stt-{category}/{engine-id}/` with:
   - `engine.yaml` — metadata, config_schema, output_schema
   - `engine.py` — `Engine` subclass implementing `process()`
   - `Dockerfile` and `requirements.txt`
3. **Docker Compose service** in `docker-compose.yml`
4. **DAG logic** in `dalston/orchestrator/dag.py` — conditional task creation, dependency wiring
5. **Selection logic** in `dalston/orchestrator/engine_selector.py` — stage inclusion conditions
6. **Catalog regeneration** via `python scripts/generate_catalog.py`

### Post-Processing vs. Core Pipeline

Some new stages could be implemented as **post-processing** (like PII_DETECT) rather than core pipeline stages:

- **Core pipeline** stages: NOISE_REDUCE (must run before TRANSCRIBE)
- **Could be post-processing**: EMOTION_DETECT, SPEAKER_RECOGNIZE, NONVERBAL_DETECT (don't affect transcription quality, can run after job "completion")

Post-processing stages run asynchronously after the job reaches COMPLETED status, avoiding any latency impact on the core transcription result.

### NONVERBAL_DETECT Parallelism

NONVERBAL_DETECT is unique because it can run **in parallel** with TRANSCRIBE since both only need the prepared audio. The DAG builder would need to:

1. Create the NONVERBAL_DETECT task with dependency only on PREPARE
2. Mark it as parallelizable with TRANSCRIBE
3. Have MERGE consume both TRANSCRIBE output and NONVERBAL_DETECT output
4. Interleave non-verbal event annotations into the final transcript at matching timestamps

This parallel execution pattern doesn't exist in the current pipeline but the DAG infrastructure supports it (tasks only block on their declared dependencies).

---

## 6. Open Questions

1. **Should NOISE_REDUCE produce a new audio artifact or modify in-place?** Recommendation: new artifact, so original is preserved for quality comparison.

2. **Should NONVERBAL_DETECT be core pipeline or post-processing?** If users want `[laughter]` in their real-time transcript, it must be core. If it's only for batch enrichment, post-processing is simpler.

3. **Speaker enrollment API scope** — SPEAKER_RECOGNIZE requires a speaker profile management API (CRUD for voice samples + embeddings). This is a significant API surface addition beyond just a pipeline stage.

4. **EMOTION_DETECT granularity** — Per-segment, per-utterance, or per-speaker-turn? Per-segment aligns with existing data model but may be too fine-grained for long conversations.

5. **Model hosting** — Should new ML models (BEATs, emotion2vec, TitaNet) be bundled into engine containers, or should we support external model servers (like we do with Riva NIM)?

6. **Cost of NVIDIA path** — Maxine BNR NIM requires NVIDIA AI Enterprise license for production use. DeepFilterNet is MIT and GPU-optional. Worth considering the licensing cost trade-off.
