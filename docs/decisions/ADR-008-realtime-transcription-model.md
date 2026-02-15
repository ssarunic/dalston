# ADR-008: Real-Time Transcription Model Selection

## Status

Proposed

## Context

Our current real-time transcription engine uses **faster-whisper** (`Systran/faster-distil-whisper-large-v3` / `Systran/faster-whisper-large-v3`) with Silero VAD. The architecture works as follows:

1. Audio arrives over WebSocket in small frames
2. Silero VAD buffers and detects speech boundaries (100ms chunks)
3. On speech end (500ms silence), the **entire accumulated utterance** is sent to faster-whisper for transcription
4. Result is returned as a `transcript.final` message

**The problem:** Whisper is an encoder-decoder model designed for 30-second fixed-length segments. It does not support true incremental/streaming inference. Our VAD-gated approach means:

- No transcript output until the speaker pauses for 500ms+
- Long continuous utterances accumulate unbounded audio before transcription
- A 30-second monologue produces zero output until the speaker pauses
- Effective end-to-end latency = utterance duration + inference time + 500ms silence threshold

This creates an unacceptable user experience for real-time applications like live captioning, voice assistants, and meeting transcription where users expect to see words appearing as they speak.

## Requirements

- **True streaming**: Words should appear within ~500ms-1s of being spoken, not after the utterance ends
- **Multilingual support**: Must handle multiple languages (ideally 20+)
- **Production-ready**: Actively maintained, reasonable resource requirements
- **Server-side GPU deployment**: Must run on NVIDIA GPUs (not edge/mobile focused)

## Models Investigated

### Models with Native Streaming Architecture

These models were architecturally designed for streaming and do not require wrapper hacks.

#### 1. NVIDIA Parakeet RNNT 1.1B Multilingual

- **Architecture**: FastConformer encoder + RNN-Transducer decoder (inherently streaming)
- **Languages**: 25 European languages with auto language detection
- **Latency**: Low, frame-level streaming via RNNT architecture
- **Parameters**: 1.1B
- **Deployment**: NVIDIA NeMo, Riva NIM
- **License**: Apache 2.0 (via NeMo)
- **Status**: Actively maintained by NVIDIA
- **Strengths**: Production-grade via Riva, native streaming, good accuracy, broad EU language support
- **Weaknesses**: European languages only (no CJK, Arabic, Hindi, etc.)

#### 2. Meta SeamlessStreaming

- **Architecture**: Encoder-decoder with Efficient Monotonic Multihead Attention (EMMA) for simultaneous output
- **Languages**: 96 languages for ASR
- **Latency**: Low, EMMA enables incremental output as audio arrives
- **Parameters**: ~2.5B (SeamlessM4T v2 base)
- **Deployment**: PyTorch, available on Hugging Face
- **License**: CC-BY-NC 4.0 (non-commercial restriction)
- **Status**: Meta research project, open-source
- **Strengths**: Broadest language coverage of any streaming model, includes translation capability
- **Weaknesses**: Primarily a translation model (ASR is one component of a larger pipeline), non-commercial license, complex deployment

#### 3. Kyutai STT (Delayed Streams Modeling)

- **Architecture**: "Delayed Streams" where text and audio are time-aligned parallel streams with configurable lookahead
- **Languages**: English and French only (stt-1b-en_fr)
- **Latency**: 500ms delay (125ms with end-of-speech flush)
- **Parameters**: 1B (en_fr), 2.6B (en-only)
- **Deployment**: PyTorch, Rust, MLX
- **License**: CC-BY 4.0
- **Status**: Active development by Kyutai
- **Strengths**: Lowest latency of any model tested, built-in semantic VAD, word timestamps, high throughput (400 concurrent streams on H100)
- **Weaknesses**: Only 2 languages. Unusable for general multilingual use case

#### 4. Moonshine Streaming (Useful Sensors)

- **Architecture**: Sliding-window Transformer encoder with bounded local attention, ergodic design
- **Languages**: 8 languages via separate monolingual models (EN, AR, ZH, JA, KO, UK, VI)
- **Latency**: Sub-200ms on edge hardware, designed to break Whisper's 500ms floor
- **Parameters**: 27M (tiny), 62M (base)
- **Deployment**: ONNX, TFLite, JavaScript, Python
- **License**: MIT
- **Status**: Active development
- **Strengths**: Tiny model size, true streaming architecture, runs on Raspberry Pi
- **Weaknesses**: Edge-focused (underutilizes server GPUs), separate monolingual models (not unified multilingual), only 8 languages

### Streaming Wrappers Around Whisper

These use Whisper as the backbone but add streaming policies on top.

#### 5. SimulStreaming / AlignAtt (UFAL, Charles University)

- **Architecture**: Uses Whisper's encoder-decoder attention patterns to determine how much source audio has been consumed, halting when attention reaches the buffer edge
- **Languages**: 99 languages (inherits from Whisper)
- **Latency**: ~5x faster than predecessor WhisperStreaming (~0.6-1s effective)
- **Parameters**: 1.5B+ (Whisper Large V3 backbone)
- **Deployment**: Python, integrates with faster-whisper
- **License**: MIT
- **Status**: Active development; won IWSLT 2025 Simultaneous Speech Translation task
- **Strengths**: State-of-the-art streaming quality, broadest language support, uses proven Whisper accuracy, genuine streaming policy (not simple chunking)
- **Weaknesses**: Still a wrapper (overhead vs native streaming), requires Whisper Large V3 GPU footprint

#### 6. WhisperLiveKit

- **Architecture**: Unified framework integrating both SimulStreaming (AlignAtt) and LocalAgreement backends
- **Languages**: 99 languages (via Whisper)
- **Latency**: Configurable, ultra-low with SimulStreaming backend
- **Deployment**: Python, WebSocket server included
- **License**: MIT
- **Status**: Active development
- **Strengths**: Drop-in streaming solution, multiple backend options, WebSocket server included
- **Weaknesses**: Inherits Whisper limitations, additional abstraction layer

### Models NOT Suitable for Streaming (but notable)

| Model | Why Not Streaming | Notable Strength |
|---|---|---|
| **NVIDIA Canary Qwen 2.5B** | Encoder-decoder, no streaming | #1 on Open ASR Leaderboard (5.63% WER) |
| **SenseVoice (Alibaba)** | CTC-based, no streaming API | Multi-task: ASR + emotion + audio events; excellent CJK |
| **Whisper Large V3 Turbo** | 30s fixed segments | 6x faster than Large V3, 99 languages |
| **Distil-Whisper** | 30s fixed segments | 49% smaller, within 1% WER of Large V3 |

## Evaluation

| Criterion | Parakeet RNNT | SeamlessStreaming | SimulStreaming | Kyutai STT | Moonshine |
|---|---|---|---|---|---|
| True streaming | Native | Native | Policy-based | Native | Native |
| Languages | 25 (EU) | 96 | 99 (via Whisper) | 2 | 8 |
| Latency | Low | Low | ~0.6-1s | 500ms/125ms | <200ms |
| Accuracy | Good | Competitive | SOTA (IWSLT) | 6.4% WER | Good for size |
| Production readiness | High (Riva) | Medium | Medium | Medium | Low (edge) |
| GPU efficiency | Good | Heavy (2.5B) | Heavy (1.5B) | Good (batching) | Overkill on GPU |
| License | Apache 2.0 | CC-BY-NC | MIT | CC-BY 4.0 | MIT |
| Deployment complexity | Medium (NeMo/Riva) | High | Low | Low | Low |

## Recommendation

### Short-term: SimulStreaming (AlignAtt) via WhisperLiveKit

**Why:** This is the lowest-friction path from our current architecture. We already use faster-whisper as our backend. SimulStreaming/WhisperLiveKit:

- Uses the same faster-whisper backend we already deploy
- Covers 99 languages (matching our current Whisper coverage)
- Replaces our VAD-gated "transcribe on silence" approach with genuine streaming output via attention-based policy
- Won IWSLT 2025, so the streaming quality is state-of-the-art
- MIT licensed, actively maintained
- Our existing WebSocket protocol (`transcript.partial` / `transcript.final`) already supports interim results

**Integration path:**
1. Add `whisper-streaming` / `SimulStreaming` as a dependency to the realtime engine
2. Replace the current `_transcribe_and_send()` flow with the AlignAtt streaming policy
3. Emit `transcript.partial` messages as the policy produces incremental output
4. Emit `transcript.final` on utterance completion
5. Keep Silero VAD for speech detection events but decouple it from transcription triggering

### Medium-term: NVIDIA Parakeet RNNT 1.1B Multilingual

**Why:** If our language requirements are primarily European, Parakeet RNNT offers a native streaming architecture purpose-built for production deployment:

- RNNT is inherently streaming (no wrapper needed)
- NVIDIA Riva provides production-grade serving infrastructure
- 25 European languages with auto-detection
- Better GPU utilization than Whisper-based approaches
- Apache 2.0 license

**Blocker:** Only covers European languages. If CJK, Arabic, or other non-European languages are required, this cannot be the sole engine.

### Watch list

- **Kyutai STT**: If they expand beyond English/French, this becomes the top contender due to its 125ms latency and high-throughput batching architecture (400 concurrent streams on H100)
- **NVIDIA Canary streaming variant**: NVIDIA may release streaming-capable Canary models via Riva, which would combine best-in-class accuracy with streaming
- **SenseVoice v2**: If Alibaba adds streaming support, the multi-task capabilities (ASR + emotion + audio events) would be valuable for meeting transcription

## References

- [SimulStreaming (UFAL)](https://github.com/ufal/SimulStreaming) - IWSLT 2025 winner
- [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) - Unified streaming framework
- [NVIDIA Parakeet RNNT 1.1B](https://build.nvidia.com/nvidia/parakeet-1_1b-rnnt-multilingual-asr/modelcard)
- [Meta SeamlessStreaming](https://github.com/facebookresearch/seamless_communication)
- [Kyutai STT](https://github.com/kyutai-labs/delayed-streams-modeling)
- [Moonshine](https://github.com/moonshine-ai/moonshine)
- [Hugging Face Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard)
- [NVIDIA Canary-1B-v2](https://huggingface.co/nvidia/canary-1b-v2)
- [SenseVoice (Alibaba)](https://github.com/FunAudioLLM/SenseVoice)
