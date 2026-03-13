# TTS Architecture Plan for Dalston

## 1. TTS Model Landscape (2026)

### Tier 1 — Strong candidates for Dalston engines

| Model | Params | Voice Cloning | Emotion/Style | Non-speech Sounds | Streaming | Languages | Architecture | GPU | License |
|---|---|---|---|---|---|---|---|---|---|
| **Fish Audio OpenAudio S1** | 4B | Yes (zero-shot) | Yes (natural language) | Yes | Yes | Multilingual | LLM + dual-codebook (SNAC) + RLHF | 16–24 GB VRAM | Apache 2.0 |
| **Qwen3-TTS** | 0.6B–1.7B | Yes (3s sample) | Yes | Partial | Yes (97ms TTFA) | 10 (EN, ZH, JA, KO, DE, FR, RU, PT, ES, IT) | Transformer, dual-track streaming | 8–16 GB VRAM | Apache 2.0 |
| **CosyVoice 3** | ~0.5B | Yes (zero-shot) | Yes | Partial | Yes (150ms TTFA) | EN, ZH, JA, KO | Flow matching + GRPO post-training | 8–12 GB VRAM | Apache 2.0 |
| **F5-TTS** | ~300M | Yes (10s sample, zero-shot) | Partial | No | Yes | Multilingual | Non-autoregressive, flow-matching DiT | 6–8 GB VRAM | MIT |
| **Kokoro** | 82M | No | Limited (voice presets) | No | Yes (96x RT) | Limited | StyleTTS2-based | 2–4 GB VRAM (runs on CPU) | Apache 2.0 |
| **Dia** | 1.6B | Yes | Yes (tag-based) | Yes — `(laughs)`, `(coughs)`, `(gasps)` | No (batch) | English only | Autoregressive + DAC codec | 8–12 GB VRAM | Apache 2.0 |
| **Chatterbox** | ~0.5B | Yes (zero-shot) | Yes | Partial | Yes | 23 languages | Built-in watermarking | 6–8 GB VRAM | MIT |

### Tier 2 — Promising but with caveats

| Model | Params | Notes |
|---|---|---|
| **Sesame CSM** | 1B | Excellent for conversational AI and non-verbal cues. Llama-based. Multi-speaker. Quality trails F5 for cloning. Restrictive license (non-commercial). |
| **Orpheus** | 150M–3B | Llama-based, zero-shot cloning, guided emotion tags, streaming. English-focused. |
| **Spark-TTS** | 0.5B | Novel BiCodec architecture (semantic + global tokens). EN+ZH. Interesting design but slow generation, weak cloning in current release. |
| **Zonos** | ~0.5B | Emotion control via conditioning. Zero-shot cloning. EN only in initial release. eSSMLlike rate/pitch control. |
| **OuteTTS** | ~0.5B | Pure LLM approach (token prediction). Voice cloning. Moderate quality. |
| **MaskGCT** | ~300M | Non-autoregressive, masked generative codec transformer. Fast inference. |
| **CosyVoice 2** (Alibaba) | ~0.5B | Zero-shot cloning, streaming, EN+ZH+JA+KO. Based on flow matching. |

### Tier 3 — Commercial APIs (for comparison / fallback routing)

| Service | Standout Feature | Latency | Voice Cloning | Emotion | Non-speech |
|---|---|---|---|---|---|
| **ElevenLabs v3** | Best naturalness, Audio Tags (`[laughs]`, `[whispers]`, `[excited]`), 70+ langs | ~200ms | Yes (instant) | Yes (tag-based) | Yes |
| **Cartesia Sonic 3** | Lowest latency (40ms TTFA), SSM architecture, 42 langs | 40–90ms | Yes | Yes (SSML) | Yes (laughter) |
| **OpenAI TTS** | Simple API, good defaults | ~300ms | No | Limited | No |
| **Google Cloud TTS** | WaveNet/Neural2 voices, SSML, 50+ langs | ~200ms | No | SSML-based | No |
| **Azure TTS** | SSML, custom neural voice training, 140+ langs | ~150ms | Yes (training) | SSML-based | No |
| **PlayHT** | Voice cloning, emotion | ~200ms | Yes | Yes | Limited |

### Key takeaways

1. **Fish Audio OpenAudio S1** is #1 on TTS-Arena-V2 (March 2026) — 4B params, LLM-based, RLHF-trained, best overall quality. Open-sourced after dominating benchmarks as the commercial S2-Pro model.
2. **Qwen3-TTS** is the best balance of quality, speed, and features — cloning, streaming (97ms), multilingual, Apache 2.0
3. **CosyVoice 3** (Alibaba, May 2025) — state-of-the-art content consistency, GRPO post-training, 150ms streaming
4. **F5-TTS** is the best pure cloning engine — fast, high quality, MIT license
5. **Kokoro** is the speed king — tiny model, CPU-friendly, great for low-latency where cloning isn't needed
6. **Dia** is unique for non-speech sounds and dialogue — `(laughs)`, `(coughs)` tags are exactly what you described wanting
7. **Chatterbox** is the dark horse — beats ElevenLabs in blind tests, 23 langs, MIT, built-in audio watermarking

### Key trends (2025–2026)

- **LLM backbones** dominate TTS now (Qwen2.5, Llama) — same architecture as chat models but outputting audio codec tokens
- **Neural audio codecs** (DAC, SNAC, Mimi) have replaced mel-spectrograms as intermediate representation — no separate vocoder needed
- **RLHF/GRPO post-training** is becoming standard for quality (Fish Audio, CosyVoice 3)
- **Emotion control** is shifting from tag-based to natural language instruction-based ("speak warmly with a slight smile")
- **The quality gap between open-source and commercial has nearly closed** — Chatterbox beats ElevenLabs in blind tests, Fish Audio S1 competes with best commercial offerings

---

## 2. Do We Need Stages Like ASR?

**Short answer: No.** TTS pipelines are fundamentally simpler than ASR.

### Why ASR needs stages

ASR has multiple independent concerns that benefit from specialization:
```
PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → AUDIO_REDACT → MERGE
```
Each stage has different model types (VAD, ASR, forced alignment, speaker embedding clustering, NER) and different engines excel at different stages.

### Why TTS is different

Modern TTS models are **end-to-end** — they go from text directly to waveform in a single model. The traditional pipeline (text normalization → phonemization → duration modeling → mel spectrogram → vocoder) has been collapsed into single neural networks. Qwen3-TTS, F5-TTS, Dia, etc. all do this internally.

### Proposed TTS stages (minimal)

```
TEXT_PREPARE → SYNTHESIZE → [AUDIO_ENHANCE]
```

| Stage | Purpose | When needed |
|---|---|---|
| **TEXT_PREPARE** | Text normalization (numbers, abbreviations, SSML parsing), chunking long text, multi-speaker script parsing | Always (but can be lightweight / in-gateway) |
| **SYNTHESIZE** | Core TTS inference — text → audio | Always |
| **AUDIO_ENHANCE** | Post-processing — noise reduction, loudness normalization (LUFS), format conversion, concatenation of chunks | Optional, batch only |

**Why not more stages?**
- Voice cloning (speaker embedding extraction) happens inside the SYNTHESIZE engine — it's a model feature, not a separate stage
- Emotion/prosody control is an input parameter to SYNTHESIZE, not a separate stage
- Vocoding is internal to modern models (no separate vocoder stage)

### Comparison with ASR flow

```
ASR:  audio-in  → [7 stages] → text-out
TTS:  text-in   → [1-3 stages] → audio-out
```

The asymmetry makes sense: understanding speech is harder than generating it. Recognition requires decomposing a complex signal; synthesis just needs to produce one plausible realization.

---

## 3. Architectural Mapping to Dalston

### 3.1 Batch TTS

Mirrors the existing batch STT pattern but reversed:

```
Client: POST text + params → Gateway → Orchestrator → DAG:

  TEXT_PREPARE ──→ SYNTHESIZE ──→ [AUDIO_ENHANCE] ──→ result.wav on S3
```

**API endpoint:** `POST /v1/audio/syntheses`

```json
{
  "text": "Hello world. (laughs) That was funny.",
  "voice_id": "custom-voice-abc123",
  "voice_sample_url": "s3://samples/reference.wav",   // for zero-shot cloning
  "model": "qwen3-tts-1.7b",
  "language": "en",
  "output_format": "mp3",
  "sample_rate": 24000,
  "emotion_tags": true,           // enable (laughs), [excited], etc.
  "speed": 1.0,
  "parameters": {
    "synthesize": { "temperature": 0.7 },
    "enhance": { "target_lufs": -16.0 }
  }
}
```

**Response (async):** `GET /v1/audio/syntheses/{job_id}` → status + audio URL

### 3.2 Real-time TTS (streaming)

This is the higher-value use case (voice agents, live narration):

```
Client: WS /v1/audio/syntheses/stream
  → sends text chunks (or full sentences)
  ← receives PCM audio chunks in real-time
```

**WebSocket protocol messages:**

```
Client → Server:
  session.begin    { voice_id, language, sample_rate, model, encoding }
  text.chunk       { text: "Hello, how are you?", final: false }
  text.chunk       { text: " I'm doing great.", final: true }
  session.end      {}

Server → Client:
  session.created  { session_id, voice_id, model }
  audio.chunk      { data: <base64 PCM>, sequence: 1 }
  audio.chunk      { data: <base64 PCM>, sequence: 2 }
  synthesis.done   { total_audio_seconds, total_characters }
  session.ended    {}
```

This maps directly to the existing `SessionAllocator` + `RealtimeEngine` pattern, just with reversed data flow.

### 3.3 Hybrid mode (ASR + TTS in one pipeline)

The real power comes from combining both:

```
Audio in → [ASR pipeline] → transcript → [LLM processing] → [TTS pipeline] → Audio out
```

Use cases:
- **Meeting transcription with audio summary** — transcribe → summarize → synthesize narration
- **Audio translation** — transcribe (EN) → translate → synthesize (target language)
- **Voice agents** — real-time ASR → LLM → real-time TTS (full duplex)
- **Podcast enhancement** — transcribe → clean up → re-synthesize with different voice

### 3.4 Engine implementations (first wave)

Following the unified engine pattern from `engines/stt-unified/`:

```
engines/
  tts-unified/
    kokoro/                    # Speed-focused, no cloning
      engine.yaml
      batch_engine.py
      rt_engine.py
      core.py                  # Shared inference
      Dockerfile
      variants/
        default.yaml           # 82M model
    qwen3-tts/                 # Full-featured, cloning + streaming
      engine.yaml
      batch_engine.py
      rt_engine.py
      core.py
      Dockerfile
      variants/
        base.yaml              # 0.6B
        large.yaml             # 1.7B
    f5-tts/                    # Best cloning quality
      engine.yaml
      batch_engine.py
      rt_engine.py
      core.py
      Dockerfile
    dia/                       # Dialogue + sound effects
      engine.yaml
      batch_engine.py          # Batch only (no streaming support)
      core.py
      Dockerfile
  tts-prepare/
    text-prepare/              # Text normalization, SSML, chunking
      engine.yaml
      engine.py
      Dockerfile
  tts-enhance/
    audio-enhance/             # Loudness normalization, format conversion
      engine.yaml
      engine.py
      Dockerfile
```

### 3.5 New types needed

```python
# dalston/common/pipeline_types.py additions

class SynthesizeInput(BaseModel):
    text: str
    voice_id: str | None = None
    voice_sample_uri: str | None = None      # S3 path for zero-shot cloning
    language: str = "en"
    speed: float = 1.0
    emotion_tags: bool = True                 # parse (laughs), [excited], etc.
    output_format: AudioFormat = "wav"
    sample_rate: int = 24000

class SynthesizeOutput(BaseModel):
    audio_uri: str                            # S3 path to generated audio
    duration_seconds: float
    sample_rate: int
    characters_processed: int

class TextPrepareInput(BaseModel):
    text: str
    language: str = "en"
    normalize_numbers: bool = True
    normalize_abbreviations: bool = True
    parse_ssml: bool = False
    max_chunk_chars: int = 500               # split long text for parallel synthesis

class TextPrepareOutput(BaseModel):
    chunks: list[TextChunk]
    speakers: list[SpeakerScript] | None     # for multi-speaker scripts
```

### 3.6 Pipeline configuration

```python
# dalston/common/pipeline_types.py

TTS_STAGES = ["text_prepare", "synthesize", "audio_enhance"]

DEFAULT_TTS_ENGINES = {
    "text_prepare": "text-prepare",
    "synthesize": "qwen3-tts",          # default to most capable
    "audio_enhance": "audio-enhance",
}
```

### 3.7 Model selection

```python
# New model selection key
MODEL_PARAM_SYNTHESIZE = "model"        # reuse "model" since it's a different endpoint

# Capability matching
# "kokoro" → engine="kokoro", fast but no cloning
# "qwen3-tts-1.7b" → engine="qwen3-tts", variant="large"
# "dia-1.6b" → engine="dia", dialogue mode
# "f5-tts" → engine="f5-tts", best cloning
```

---

## 4. Feature: Voice Cloning

### How it works in practice

1. **Upload reference audio:** `POST /v1/voices` with 3–30 seconds of audio
2. **Extract speaker embedding:** Stored in S3 + metadata in Postgres
3. **Use at synthesis time:** `voice_id` parameter in synthesis request
4. **Zero-shot (inline):** Pass `voice_sample_url` directly — no pre-registration needed

### Engine support matrix

| Engine | Cloning Method | Min Sample | Quality |
|---|---|---|---|
| Qwen3-TTS | Zero-shot, 3s sample | 3s | High |
| F5-TTS | Zero-shot, 10s sample | 10s | Highest |
| Dia | Reference audio | 5s | Good |
| Chatterbox | Zero-shot | 5s | High |
| Kokoro | Not supported | N/A | N/A |

### Voice management API

```
POST   /v1/voices                    # Upload reference audio, create voice profile
GET    /v1/voices                    # List voices for tenant
GET    /v1/voices/{voice_id}         # Get voice details
DELETE /v1/voices/{voice_id}         # Delete voice
GET    /v1/voices/{voice_id}/sample  # Download reference audio
```

---

## 5. Feature: Emotion & Non-speech Sounds

### Tag-based approach (recommended)

Follow the pattern established by ElevenLabs v3 and Dia:

```
Input text:  "I can't believe it! (laughs) That's amazing. [whispers] Don't tell anyone."
```

Tags parsed in TEXT_PREPARE, forwarded to SYNTHESIZE engine.

### Supported tag types (engine-dependent)

| Tag Type | Example | Dia | Qwen3 | Chatterbox | ElevenLabs |
|---|---|---|---|---|---|
| Laughter | `(laughs)` | Yes | Partial | Partial | Yes |
| Coughing | `(coughs)` | Yes | No | No | Yes |
| Gasping | `(gasps)` | Yes | No | No | Yes |
| Whispering | `[whispers]` | No | Partial | No | Yes |
| Excitement | `[excited]` | No | Yes | Yes | Yes |
| Sadness | `[sad]` | No | Yes | Yes | Yes |
| Emphasis | `<emphasis>word</emphasis>` | No | SSML | No | SSML |

### Fallback behavior

If an engine doesn't support a tag, TEXT_PREPARE strips it and logs a warning. No failure — graceful degradation.

---

## 6. Feature: Batch vs Real-time

### Batch use cases
- Long-form content (articles, books, documentation)
- Multi-speaker dialogue scripts
- High-quality output where latency doesn't matter
- Post-processing (loudness normalization, format conversion)
- Parallel chunk synthesis for long texts

### Real-time use cases
- Voice agents (ASR → LLM → TTS loop)
- Live narration / accessibility
- Interactive applications
- Sentence-by-sentence streaming

### Engine streaming support

| Engine | Batch | Real-time Streaming | Notes |
|---|---|---|---|
| Qwen3-TTS | Yes | Yes (97ms TTFA) | Best all-rounder |
| Kokoro | Yes | Yes (fastest) | No cloning, but lowest latency |
| F5-TTS | Yes | Yes | Good balance |
| Dia | Yes | No | Batch-only, but best for dialogue |
| Chatterbox | Yes | Yes | Good quality |

### Recommended defaults
- **Real-time default:** Kokoro (speed) or Qwen3-TTS (features)
- **Batch default:** Qwen3-TTS (quality + features) or F5-TTS (cloning quality)
- **Dialogue/audiobook:** Dia (sound effects + multi-speaker)

---

## 7. Implementation Phases

### Phase 1: Core TTS (batch)
- Add `SynthesizeInput`/`SynthesizeOutput` types
- Add `text_prepare` and `synthesize` stages to pipeline
- Implement Kokoro engine (simplest, CPU-friendly, good for proving the pipeline)
- `POST /v1/audio/syntheses` endpoint
- Basic DAG: `TEXT_PREPARE → SYNTHESIZE`

### Phase 2: Voice cloning + more engines
- Voice management API (`/v1/voices`)
- F5-TTS engine (best cloning)
- Qwen3-TTS engine (multilingual + cloning + streaming)
- Speaker embedding storage in Postgres + S3

### Phase 3: Real-time streaming
- TTS WebSocket endpoint (`/v1/audio/syntheses/stream`)
- Extend `RealtimeEngine` base for TTS
- Kokoro real-time engine
- Qwen3-TTS real-time engine
- Session allocator support for TTS workers

### Phase 4: Advanced features
- Dia engine (sound effects, multi-speaker dialogue)
- Emotion/tag parsing in TEXT_PREPARE
- Audio enhance stage (LUFS normalization, format conversion)
- Hybrid ASR→TTS pipelines
- ElevenLabs-compatible TTS API surface

### Phase 5: Production hardening
- Voice cloning abuse prevention (consent verification)
- Rate limiting per voice/tenant
- Audio watermarking
- Model caching / warm pools
- Cost tracking per synthesis character

---

## 8. Open Questions

1. **ElevenLabs API compatibility** — Do we want to mirror their TTS API surface the way we mirror their STT API? Their v3 Audio Tags format is becoming a de facto standard.

2. **Multi-speaker scripts** — Should we support a structured dialogue format (e.g., screenplay-style) or rely on Dia's natural tag parsing?

3. **Voice consent/safety** — Voice cloning needs guardrails. Do we want watermarking (e.g., Resemble AI's approach) or consent verification workflows?

4. **GPU sharing** — TTS models are smaller than ASR models. Can we colocate TTS + ASR engines on the same GPU? Kokoro at 82M params could easily share with a Whisper instance.

5. **Which engine first?** — Kokoro is simplest to integrate (small, fast, CPU, no cloning complexity). Qwen3-TTS is most feature-complete. F5-TTS has the best cloning. Dia has the sound effects you want.
