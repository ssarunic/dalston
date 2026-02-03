# Dalston STT Model Reference

Comprehensive reference for speech-to-text model selection in Dalston's modular pipeline.

> **Last verified:** February 2026
> **Sources:** [Northflank STT Benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks), [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard), [NVIDIA NeMo](https://developer.nvidia.com/nemo), Model cards on HuggingFace

---

## Pipeline Stages & Primary Models

| Stage | Primary Model | Purpose | Swappable With |
|-------|---------------|---------|----------------|
| Transcription | faster-whisper (large-v3) | Core ASR | Canary-Qwen, Granite Speech, Parakeet |
| Alignment | WhisperX | Word-level timestamps | — |
| Diarization | pyannote 3.x | Speaker segmentation | NeMo MSDD |
| Streaming | Parakeet FastConformer | Real-time transcription | Kyutai STT, WeNet, Sherpa |
| Audio Analysis | SpeechBrain + YAMNet | Emotion + events (optional) | SenseVoice (all-in-one) |

---

## Top 20 Open-Source STT Models (2026)

### Offline Transcribers (Batch ASR)

#### 1. NVIDIA Canary-Qwen 2.5B

- **Developer:** NVIDIA NeMo
- **License:** CC-BY-4.0 (commercial OK with attribution)
- **Hardware:** ~8-10GB VRAM, A10G or higher
- **Languages:** English only
- **WER:** 5.63% (OpenASR #1), 1.6% LibriSpeech clean, 3.1% other
- **Speed:** RTFx 418 (~8.6s per 1h audio on A10G)
- **Architecture:** SALM (Speech-Augmented LM) – FastConformer encoder + Qwen-LLM decoder
- **Features:** Punctuation/capitalization, dual ASR/LLM modes (can summarize transcripts)
- **Pros:** SOTA English accuracy, integrated LLM capabilities
- **Cons:** English-only, high latency, no diarization, 2.5B params is memory-intensive

#### 2. IBM Granite Speech 3.3 8B

- **Developer:** IBM Watson AI
- **License:** Apache 2.0 (fully permissive)
- **Hardware:** ~15GB+ VRAM, H100/A100 recommended
- **Languages:** English, French, German, Spanish, Portuguese; translation to Japanese/Mandarin
- **WER:** ~5.85% (OpenASR #2)
- **Speed:** RTFx ~31 (~116s per 1h on A10G)
- **Architecture:** Conformer encoder + 8B Transformer decoder, two-pass design
- **Features:** Arbitrary-length audio (no 30s chunks), automatic punctuation
- **Pros:** Excellent multilingual accuracy, Apache license, handles long files
- **Cons:** Very slow (13× slower than Canary), massive model, no diarization

#### 3. OpenAI Whisper Large v3

- **Developer:** OpenAI
- **License:** MIT (fully open)
- **Hardware:** ~10GB VRAM, runs on A10G; CoreML for Apple Silicon
- **Languages:** 99 languages (including Croatian, Serbian, Slovenian)
- **WER:** ~10-12% average, varies by language
- **Speed:** RTFx ~216 (~17s per 1h on A10G); ~5-10× realtime on M3
- **Architecture:** Transformer encoder-decoder, 32+32 layers, trained on 1M hours
- **Features:** Transcription + translation, timestamped segments
- **Pros:** Best multilingual coverage, strong ecosystem (WhisperX, faster-whisper), MIT license
- **Cons:** Not real-time (30s chunks), higher WER than specialized models, no diarization

#### 4. OpenAI Whisper Large v3 Turbo

- **Developer:** OpenAI
- **License:** MIT
- **Hardware:** ~6GB VRAM
- **Languages:** 99 languages
- **WER:** ~1% higher than full v3
- **Speed:** 6-8× faster than large-v3; near real-time on M3
- **Architecture:** Same encoder, decoder pruned from 32 to 4 layers (809M params)
- **Pros:** Much faster with minimal accuracy loss, lower VRAM, multilingual
- **Cons:** Still chunk-based (not streaming), slight quality drop in some languages

#### 5. Hugging Face Distil-Whisper Large v3

- **Developer:** Hugging Face community
- **License:** MIT
- **Hardware:** ~5GB VRAM, CPU viable
- **Languages:** English only
- **WER:** ~9.7% short-form, ~10.8% long-form (within 1% of full Whisper)
- **Speed:** 6.3× faster than Whisper large-v3 (~10s per 1h on A10G)
- **Architecture:** Whisper encoder (frozen) + 2 decoder layers (756M params)
- **Pros:** Near SOTA English accuracy at fraction of compute, great for high-throughput
- **Cons:** English-only, no multilingual capability

#### 6. Microsoft Phi-4 Multimodal 5.6B

- **Developer:** Microsoft
- **License:** MIT
- **Hardware:** ~16GB VRAM, multi-GPU recommended for 128k context
- **Languages:** 8 for audio (EN, ZH, DE, FR, IT, JA, ES, PT)
- **WER:** Outperforms Whisper v3 on FLEURS, likely <5% on LibriSpeech
- **Speed:** Not real-time (capability over speed focus)
- **Architecture:** Phi-4-Mini backbone + vision/speech encoders, Mixture-of-LoRAs
- **Features:** Multimodal (speech + vision + text), 128k context, Q&A on transcripts
- **Pros:** SOTA accuracy, integrated reasoning, MIT license
- **Cons:** Limited language set, very large, no diarization, complex fine-tuning

#### 7. Meta Omnilingual ASR (300M-7B)

- **Developer:** Meta AI (November 2025)
- **License:** Apache 2.0 (fully permissive)
- **Hardware:** 300M runs on mobile/CPU; 7B needs ~17GB VRAM
- **Languages:** 1,600+ languages, zero-shot to 5,400+
- **WER:** CER <10 for 78% of 1600 languages; slight accuracy trade-off vs specialized models
- **Speed:** 300M real-time on CPU; 7B ~2-3min per 1h
- **Architecture:** Conformer-like encoder (up to 7B) + LLM-inspired decoder with in-context learning
- **Features:** Zero-shot new language support with few examples
- **Pros:** Unprecedented language coverage, Apache license, model sizes for any hardware
- **Cons:** Slightly lower accuracy on major languages vs specialized models, no diarization

#### 8. Meta MMS 1B (Massively Multilingual Speech)

- **Developer:** Meta AI
- **License:** CC-BY-NC 4.0 (non-commercial only)
- **Hardware:** ~14-16GB VRAM
- **Languages:** ~1,100 languages
- **WER:** ~14% average (vs Whisper ~8%); great on low-resource languages
- **Speed:** ~30-60s per 1h on A10G (CTC decoding)
- **Architecture:** wav2vec 2.0 XLS-R pretrained, CTC/seq2seq fine-tuned
- **Pros:** Pioneered 1000+ language ASR, good fine-tuning base
- **Cons:** Non-commercial license, superseded by Omnilingual ASR, needs fine-tuning

#### 9. nyrahealth CrisperWhisper

- **Developer:** Nyra Health
- **License:** CC-BY-NC 4.0 (non-commercial)
- **Hardware:** ~10GB VRAM (same as Whisper large)
- **Languages:** English, German
- **WER:** #1 on verbatim benchmarks; captures disfluencies ("um", "uh")
- **Speed:** Same as Whisper large (~17s per 1h)
- **Architecture:** Whisper large-v2 + custom Attention Loss for alignment
- **Features:** Verbatim transcription with precise word-level timestamps
- **Pros:** Best for legal/medical transcripts requiring exact speech capture
- **Cons:** Non-commercial, EN/DE only, not for "clean" transcription use cases

#### 10. Meta wav2vec 2.0 / XLS-R

- **Developer:** Meta AI
- **License:** Apache 2.0
- **Hardware:** 95M-317M params; 4-8GB VRAM; CPU viable for smaller models
- **Languages:** XLS-R covers 53 languages (adaptable to Croatian, Serbian, Slovenian)
- **WER:** 1.8%/3.3% on LibriSpeech with full training; varies with fine-tuning
- **Speed:** Very fast CTC decoding; 1h in ~10-15s on GPU
- **Architecture:** CNN feature extractor + Transformer encoder, self-supervised pretraining
- **Features:** Foundation model requiring fine-tuning + LM for best results
- **Pros:** Excellent for low-resource language fine-tuning, Apache license, fast
- **Cons:** Not plug-and-play (needs decoding setup), no punctuation, less robust than newer models

---

### Real-Time Transcribers (Streaming ASR)

#### 1. NVIDIA Parakeet FastConformer RNNT 0.6B

- **Developer:** NVIDIA NeMo
- **License:** NVIDIA Open Model License (commercial OK with attribution)
- **Hardware:** ~4GB VRAM, A10G or RTX 3080+
- **Languages:** English only
- **WER:** ~7.2% average, 2.3% LibriSpeech clean, 4.75% other
- **Speed:** RTFx >2000; latency ~100ms with cache-aware chunking
- **Architecture:** 24-layer FastConformer encoder (cache-aware) + RNNT decoder
- **Features:** Native streaming, punctuation/capitalization, configurable latency vs accuracy
- **Pros:** SOTA streaming accuracy, minimal latency, high throughput
- **Cons:** English-only, NVIDIA GPUs only, no diarization

#### 2. Kyutai STT 2.6B (Moshi-based)

- **Developer:** Kyutai Labs
- **License:** CC-BY-4.0
- **Hardware:** ~12-16GB VRAM
- **Languages:** English, French
- **WER:** ~6.4% English (on par with non-streaming SOTA)
- **Speed:** 2.5s initial delay; RTFx ~88; also 1B variant with 1.0s delay
- **Architecture:** Transformer consuming Mimi audio tokens, delayed streams modeling
- **Features:** Streaming with punctuation, word-level timestamps, handles 2h+ audio
- **Pros:** Low latency for large model, excellent accuracy, Rust production server
- **Cons:** EN/FR only, high memory, limited community support

#### 3. WeNet U2 Conformer

- **Developer:** Open-source community (Binbin Zhang et al.)
- **License:** Apache 2.0
- **Hardware:** ~120M params; runs real-time on CPU
- **Languages:** English, Mandarin (trainable for others)
- **WER:** ~4.5% with LM on LibriSpeech; ~5-6% streaming
- **Speed:** Real-time on CPU; sub-500ms latency
- **Architecture:** Unified Conformer with dynamic chunk attention, CTC/attention hybrid
- **Features:** Dual streaming/offline mode, production C++ runtime
- **Pros:** Open-source, production-ready, flexible language support, Apache license
- **Cons:** Limited pretrained models, needs ASR expertise to configure, no punctuation

#### 4. Kaldi / Vosk

- **Developer:** Kaldi community
- **License:** Apache 2.0
- **Hardware:** Runs on CPU, even Raspberry Pi; 40-200M params
- **Languages:** 20+ via community models (including Croatian, Serbian, Slovenian)
- **WER:** ~8-12% on LibriSpeech; higher on conversational speech
- **Speed:** Real-time on CPU; very low latency
- **Architecture:** TDNN/TDNN-F + WFST decoding (HMM/DNN hybrid)
- **Features:** Customizable vocabulary/LM, word timestamps
- **Pros:** Mature, runs anywhere, many language models, vocabulary customization
- **Cons:** Lower accuracy than E2E models, no punctuation, complex pipeline

#### 5. K2 Sherpa (Zipformer RNNT)

- **Developer:** K2/Next-gen Kaldi (Daniel Povey et al.)
- **License:** Apache 2.0
- **Hardware:** 20-100M params; real-time on CPU
- **Languages:** English, Mandarin primarily
- **WER:** ~2.0% LibriSpeech clean, ~5.0% other (nearly no streaming degradation)
- **Speed:** RTF tiny on GPU; ~0.3-0.5s latency
- **Architecture:** Zipformer encoder (efficient transformer variant) + RNNT
- **Features:** C++/ONNX runtime, pre-built binaries
- **Pros:** SOTA streaming accuracy, matches offline, Apache license, efficient
- **Cons:** Limited language models available, no punctuation, newer project

#### 6. Useful Sensors Moonshine

- **Developer:** Useful Sensors
- **License:** MIT (English); community license for other languages
- **Hardware:** 27M (tiny) to 62M (base) params; runs in browser, on mobile
- **Languages:** English (8 languages with restricted license)
- **WER:** Matches/beats Whisper Tiny/Small (~11-14%)
- **Speed:** 6× faster than Whisper large-v3; real-time in browser
- **Architecture:** Compressed transformer, variable-length processing (no padding)
- **Features:** On-device, privacy-preserving, millisecond latency
- **Pros:** Tiny footprint, edge deployment, MIT license for English
- **Cons:** English-only (MIT), lower accuracy ceiling, not for complex audio

#### 7. TorchAudio Emformer RNNT

- **Developer:** AWS/Amazon (via PyTorch)
- **License:** BSD-style (TorchAudio)
- **Hardware:** ~120M params; <4GB VRAM; real-time on modern CPU
- **Languages:** English (LibriSpeech model)
- **WER:** 2.5% LibriSpeech clean, 5.62% other
- **Speed:** >10× realtime on GPU; ~1.5× on 4-core CPU
- **Architecture:** Emformer encoder (memory-efficient transformer) + RNNT
- **Features:** Streaming with limited context, TorchAudio integration
- **Pros:** Ready-to-use via TorchAudio, good accuracy/efficiency balance
- **Cons:** English only, no punctuation, research baseline (limited production features)

---

## Dalston Primary Stack

### Current Implementation

| Stage | Model | Rationale |
|-------|-------|-----------|
| **Batch Transcription** | faster-whisper large-v3 | Best multilingual accuracy + speed balance |
| **Alignment** | WhisperX | Word-level timestamps via wav2vec2 |
| **Diarization** | pyannote 3.1 | Industry standard, MIT license |
| **Streaming** | Parakeet FastConformer | Best streaming accuracy for English |

### Future Considerations

| Use Case | Alternative | When to Switch |
|----------|-------------|----------------|
| Max English accuracy | Canary-Qwen 2.5B | English-only workloads with high accuracy requirements |
| Massive multilingual | Omnilingual ASR | Need 100+ languages beyond Whisper's 99 |
| Edge/mobile | Moonshine | On-device privacy requirements |
| Low-latency multilingual streaming | Kyutai STT | Need streaming in French + English |

---

## SE European Language Support

| Model | Serbian | Croatian | Slovenian | Notes |
|-------|---------|----------|-----------|-------|
| **Whisper large-v3** | ✓ Good | ✓ Good | ✓ Fair | Best multilingual option |
| **Omnilingual ASR** | ✓ | ✓ | ✓ | 1600+ languages, Apache 2.0 |
| **XLS-R 1B** | ✓ Fine-tune | ✓ Fine-tune | ✓ Fine-tune | Requires training data |
| **MMS 1B** | ✓ | ✓ | ✓ | CC-BY-NC (non-commercial) |
| **Vosk** | ✓ Community | ✓ Community | ✓ Community | Via CommonVoice models |

**Recommendation:** faster-whisper large-v3 for production; Omnilingual ASR for expanded coverage.

---

## Hardware Reference

### Production: AWS g5.xlarge (A10G 24GB)

| Configuration | Models That Fit | Notes |
|---------------|-----------------|-------|
| Single model | Any except Granite 8B | Plenty of headroom |
| Full pipeline | faster-whisper + WhisperX + pyannote | ~16GB peak |
| Premium accuracy | Canary-Qwen 2.5B | ~10GB |

### Development: CPU/Apple Silicon

| Configuration | Viable Models | Expected Speed |
|---------------|---------------|----------------|
| Transcription | Whisper small, Distil-Whisper, Moonshine | 0.5-2× realtime |
| Streaming | Vosk, Sherpa small, Moonshine | ~1× realtime |
| Diarization | pyannote (slow) | 0.1-0.3× realtime |

---

## License Summary

| License | Commercial | Models |
|---------|------------|--------|
| **MIT** | ✓ | Whisper, Distil-Whisper, pyannote, Phi-4, Moonshine (EN) |
| **Apache 2.0** | ✓ | Granite Speech, Omnilingual ASR, wav2vec2, WeNet, Sherpa, Vosk |
| **CC-BY-4.0** | ✓ (attribution) | Canary-Qwen, Kyutai STT |
| **NVIDIA Open** | ✓ (attribution) | Parakeet/Nemotron |
| **CC-BY-NC** | ✗ | MMS, CrisperWhisper |
| **Custom** | Verify | SenseVoice (FunAudioLLM) |

---

## Interface Specification

For pipeline stage interoperability, engines should produce/consume this standard format:

```python
@dataclass
class TranscriptSegment:
    start: float           # seconds
    end: float             # seconds
    text: str
    speaker: str | None    # from diarization
    confidence: float | None
    words: list[WordTiming] | None  # from alignment
    language: str | None   # detected language code

@dataclass
class WordTiming:
    start: float
    end: float
    word: str
    confidence: float | None

@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    language: str          # primary detected language
    duration: float        # total audio duration in seconds
    model: str             # model identifier used
    metadata: dict         # model-specific metadata
```

This interface allows swapping models at any pipeline stage without breaking downstream processing.
