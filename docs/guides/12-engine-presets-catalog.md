# Engine presets catalog — what to pick for your workload

> Six engines. Each one is a different sweet spot on the
> **accuracy / latency / VRAM / language coverage / price** tradeoff.
> This page is the cheat sheet.

The `dalston-aws engine up <preset>` command (and the GPU worker section of
`launch`) takes one of six preset names. Each preset bundles an engine image,
default model, and recommended environment overrides. The source of truth is
`GPU_ENGINE_PRESETS` in [`infra/scripts/dalston-aws`](../../infra/scripts/dalston-aws#L81-L161).

---

## At a glance

| Preset | Stage | Default model | Streaming | Word ts | VRAM | RTF (GPU) | Warm-start | CPU OK? |
|---|---|---|---|---|---|---|---|---|
| `onnx` | transcribe | parakeet-tdt-0.6b ONNX | ✅ | ✅ | 2 GB | 0.03 | 50 ms | ✅ (RTF 0.12) |
| `faster-whisper` | transcribe | `large-v3-turbo` | ✅ | ❌ | 4 GB | 0.03 | 30 ms | ✅ (RTF 0.4) |
| `nemo` | transcribe | `parakeet-tdt-0.6b-v3` | ✅ | ✅ | 4 GB | 0.0006 | 100 ms | ❌ |
| `hf-asr` | transcribe | `whisper-large-v3` (any HF model) | ✅ | model-dep. | 4 GB | ~0.1 | 500 ms | ✅ (RTF 1.0) |
| `vllm-asr` | transcribe | `Voxtral-Mini-3B-2507` | ✅ | ❌ | 8 GB | 0.15 | 5000 ms | ❌ |
| `pyannote` | diarize | pyannote-community-1 | ❌ | n/a | 2 GB | 0.15 | 500 ms | ✅ (RTF 1.2, slow) |

All RTF values come straight from each engine's `engine.yaml` `performance:`
block. **RTF = processing-time / audio-duration**, so lower is faster
(0.03 means a 1-hour file in ~108 seconds; 0.0006 means a 1-hour file in
~2 seconds of pure model time, before audio I/O and chunking overhead).

---

## When to pick which

### `onnx` — the ultra-light option

- **Picture this:** you want a small, simple container that can run on a CPU
  laptop *or* a tiny GPU box with no PyTorch baggage. Perfect for embedded
  scenarios, lite installs, or air-gapped boxes.
- **Default model:** Parakeet TDT 0.6B exported to ONNX (`/models/onnx`).
- **Strengths:** tiny image (~1 GB vs NeMo's ~10 GB), CPU RTF 0.12 is the
  best of any preset on CPU, native streaming with word timestamps.
- **Tradeoffs:** English-only with the default model. INT8 quantization for
  CPU mode — accuracy is slightly behind full-precision NeMo.
- **Best AWS instance:** g4dn.xlarge spot (~$0.20/hr) or even a CPU `t3.large`.

### `faster-whisper` — multilingual workhorse

- **Picture this:** podcasts, meetings, mixed-language calls. 99 languages.
  Industry-standard Whisper accuracy with a CTranslate2 inference engine that
  blows the official OpenAI implementation out of the water on GPU.
- **Default model:** `large-v3-turbo` (`/models/faster-whisper`). Smaller
  variants like `base`, `small`, `medium` are also supported.
- **Strengths:** language coverage, robustness on noisy audio, optional GPU
  (the same image runs on CPU at RTF 0.4).
- **Tradeoffs:** Whisper does not produce reliable word-level timestamps on
  its own — the `engine.yaml` correctly declares `word_timestamps: false`.
  If you need word-level boundaries, run an `align` stage after, or use the
  combo engine.
- **Best AWS instance:** g4dn.xlarge spot for medium-quality, g6.xlarge for
  large-v3-turbo at full GPU speed.

### `nemo` — fastest English

- **Picture this:** real-time English captions, or transcribing thousands of
  hours overnight. Nothing else comes close on RTF.
- **Default model:** `nvidia/parakeet-tdt-0.6b-v3` (`/models/nemo`).
- **Strengths:** **RTF 0.0006** GPU (yes, four-digit speedup). Native
  cache-aware streaming with word timestamps. ~100 ms end-to-end latency.
- **Tradeoffs:** English-only. **Requires a GPU** (`supports_cpu: false`).
  Big container image (~10 GB).
- **Best AWS instance:** g4dn.xlarge spot is plenty; g6.xlarge if co-locating
  with pyannote.
- **Note on co-location:** the preset is configured with
  `DALSTON_VRAM_BUDGET_MB=20000` so it caps itself at 20 GB of an L4's 24 GB,
  leaving 4 GB for pyannote on the same GPU.

### `hf-asr` — bring-your-own model

- **Picture this:** you found a fine-tuned medical Whisper, a new MMS
  language model, or your team trained a custom Wav2Vec2. You want to run it
  in production today, not later.
- **Default model:** `openai/whisper-large-v3`, but **any** HuggingFace model
  with `pipeline_tag=automatic-speech-recognition` works. Set
  `DALSTON_DEFAULT_MODEL=...` to override.
- **Strengths:** universal compatibility — Whisper, Wav2Vec2, HuBERT, MMS,
  community fine-tunes, all via the standard Transformers ASR pipeline.
- **Tradeoffs:** RTF varies wildly by model (0.1 GPU is a typical Whisper
  number; small Wav2Vec2 models can be much faster). Word timestamps are
  model-dependent (Whisper provides them; Wav2Vec2 doesn't natively).
- **Best AWS instance:** g4dn.xlarge spot is the sweet spot for most HF
  models; bigger boxes for very large models.

### `vllm-asr` — audio LLMs (Voxtral, Qwen2-Audio)

- **Picture this:** you want a transcription model that *also* answers
  questions about the audio, or you want Voxtral's long-context multilingual
  reasoning. Audio-capable LLMs served via vLLM.
- **Default model:** `mistralai/Voxtral-Mini-3B-2507` (`/models/huggingface`).
- **Strengths:** new-generation model family with cross-lingual understanding;
  same vLLM serving infrastructure your GenAI stack already uses.
- **Tradeoffs:** **5-second cold start** (loading a 3B model + warming
  KV cache); 8 GB VRAM floor; **requires GPU compute ≥ 8.0** so T4 is out.
  Slowest RTF of the bunch (0.15) — these are LLMs, not optimized ASR
  models.
- **Best AWS instance:** g6.xlarge (L4) spot, or g5.xlarge (A10G) if L4 isn't
  available in your region. **Not g4dn (T4) — won't boot.**

### `pyannote` — speaker diarization

- **Picture this:** "who said what when" — turn a podcast into per-speaker
  transcripts. Pairs with any transcription engine.
- **Default model:** `pyannote/speaker-diarization-community-1` (Community-1
  pipeline with VBx clustering, new in pyannote 4.0).
- **Strengths:** state-of-the-art speaker diarization. Optional **exclusive
  mode** that emits one speaker per segment (better Whisper alignment).
  Speaker count hints (`min_speakers`, `max_speakers`) for known scenarios.
- **Tradeoffs:** **Requires `HF_TOKEN`** — the model is gated. CPU mode
  works (`supports_cpu: true`) but at RTF 1.2 it's not viable for batch.
  Stage is `diarize`, not `transcribe` — you still need a transcription
  engine to get the words.
- **Best AWS instance:** g4dn.xlarge spot. Co-locate with NeMo on a single
  L4 (g6.xlarge) for a complete transcribe + diarize box.

---

## The combo trick: one container, three stages

Sometimes you want **transcribe + align + diarize** in a single GPU container
— either to save VRAM or to deploy on a single laptop GPU. Two combo engines
exist:

- **`hf-asr-align-pyannote`** ([engines/stt-transcribe/hf-asr-align-pyannote/](../../engines/stt-transcribe/hf-asr-align-pyannote/))
  — single Python process, **10 GB VRAM**, handles all three stages.
  `max_concurrency: 1`. Ideal for Mac MPS development or single-GPU prod.
- **`whisper-align-pyannote`** ([engines/stt-combo/whisper-align-pyannote/](../../engines/stt-combo/whisper-align-pyannote/))
  — *composite* engine that runs three children (faster-whisper +
  phoneme-align + pyannote-4.0) over HTTP. Higher throughput, but you need
  three engines deployed.

Decision rule:

- < 1 hour of audio + 1 GPU + dev box → **hf-asr-align-pyannote**
- Production throughput, parallel transcription → **separate engines** (faster-whisper + phoneme-align + pyannote, possibly via the `whisper-align-pyannote` composite)

More: [32-diarization-vs-transcription.md](32-diarization-vs-transcription.md).

---

## Hourly costs (AWS, eu-west-2, indicative)

These come from the same numbers used in [51-aws-cost-estimator.md](51-aws-cost-estimator.md).

| Preset | Min instance | On-demand $/hr | Spot $/hr | 1-hour podcast (spot) |
|---|---|---|---|---|
| `onnx` | g4dn.xlarge | $0.526 | ~$0.20 | ~$0.20 (or run on CPU for $0.05) |
| `faster-whisper` | g4dn.xlarge | $0.526 | ~$0.20 | ~$0.20 |
| `nemo` | g4dn.xlarge | $0.526 | ~$0.20 | RTF 0.0006 → finishes in seconds, ~$0.01 |
| `hf-asr` | g4dn.xlarge | $0.526 | ~$0.20 | ~$0.20 |
| `vllm-asr` | g6.xlarge | $1.05 | ~$0.34 | ~$0.34 |
| `pyannote` | g4dn.xlarge | $0.526 | ~$0.20 | ~$0.20 |

Spot pricing fluctuates — check `dalston-aws status` after launch for the
locked-in price.

---

## See also

- [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md) — `engine up <preset>` walkthrough
- [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md) — what reclaims look like
- [30-how-models-are-fetched.md](30-how-models-are-fetched.md) — S3 vs HF, where the cache lives
- [50-performance-and-rtf.md](50-performance-and-rtf.md) — sizing math
- [`docs/specs/MODELS.md`](../specs/MODELS.md) — engineering deep-dive on each model family
