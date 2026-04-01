# Top 100 ASR (Automatic Speech Recognition) Models

Curated list of the most popular, widely-used, and highest-performing ASR models as of early 2026. Includes Dalston compatibility assessment for each model.

## Dalston Engine Summary

Dalston has 6 transcription engines that determine model compatibility:

| Engine | Backend | Models Supported |
|--------|---------|-----------------|
| **faster-whisper** | CTranslate2 | Any Whisper-architecture model (OpenAI, Distil-Whisper, community fine-tunes) via CTranslate2 format |
| **hf-asr** | HuggingFace Transformers | Any model with `pipeline_tag=automatic-speech-recognition` (Whisper, Wav2Vec2, HuBERT, MMS, SeamlessM4T, etc.) |
| **nemo** | NVIDIA NeMo | Parakeet (RNNT/CTC/TDT 0.6b/1.1b), Nemotron streaming, + arbitrary NeMo-compatible HF model IDs |
| **onnx** | ONNX Runtime (onnx-asr) | Parakeet ONNX variants + passthrough to any onnx-asr model (Whisper, Vosk, Canary, GigaAM) |
| **vllm-asr** | vLLM | Any vLLM-compatible audio LLM (Voxtral, Qwen2-Audio, Phi-4 multimodal) |
| **riva** | NVIDIA Riva NIM gRPC | Whatever models Riva NIM deploys (Parakeet, Canary via TensorRT-LLM) |

---

## OpenAI Whisper Family

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 1 | `openai/whisper-large-v3` | **Fully supported.** faster-whisper (curated), hf-asr, onnx. Default model for hf-asr engine. |
| 2 | `openai/whisper-large-v3-turbo` | **Fully supported.** faster-whisper (curated, default model), hf-asr. |
| 3 | `openai/whisper-large-v2` | **Fully supported.** faster-whisper (curated), hf-asr. |
| 4 | `openai/whisper-large` | **Fully supported.** hf-asr. faster-whisper if CTranslate2 conversion exists. |
| 5 | `openai/whisper-medium` | **Fully supported.** faster-whisper (curated), hf-asr. |
| 6 | `openai/whisper-medium.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |
| 7 | `openai/whisper-small` | **Fully supported.** faster-whisper (curated), hf-asr. |
| 8 | `openai/whisper-small.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |
| 9 | `openai/whisper-base` | **Fully supported.** faster-whisper (curated), hf-asr. |
| 10 | `openai/whisper-base.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |
| 11 | `openai/whisper-tiny` | **Fully supported.** faster-whisper (curated), hf-asr. |
| 12 | `openai/whisper-tiny.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |

## Distil-Whisper (HuggingFace)

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 13 | `distil-whisper/distil-large-v3.5` | **Fully supported.** hf-asr. faster-whisper if CTranslate2 conversion available. |
| 14 | `distil-whisper/distil-large-v3` | **Fully supported.** faster-whisper (curated as `distil-large-v3`), hf-asr. |
| 15 | `distil-whisper/distil-large-v2` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |
| 16 | `distil-whisper/distil-medium.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |
| 17 | `distil-whisper/distil-small.en` | **Fully supported.** hf-asr. faster-whisper via HF model ID. |

## NVIDIA NeMo Canary Family

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 18 | `nvidia/canary-qwen-2.5b` | **Not supported.** Canary uses an encoder-decoder architecture (FastConformer + Qwen LLM decoder) not handled by any current engine. **Gap:** NeMo engine only loads `EncDecRNNTBPEModel` and `EncDecCTCModelBPE` — Canary requires `EncDecMultiTaskModel`. Add a Canary model class loader to `NeMoModelManager.ARCHITECTURE_LOADERS` and handle the multi-task prompt format (task/language tokens). Alternatively, deploy via Riva NIM. |
| 19 | `nvidia/canary-1b-v2` | **Not supported.** Same gap as canary-qwen-2.5b — requires `EncDecMultiTaskModel` loader. ONNX engine doc mentions Canary passthrough but no curated alias exists. **Gap:** Add Canary loader to nemo engine or add onnx-asr Canary alias. Riva NIM can serve this today as a workaround. |
| 20 | `nvidia/canary-1b` | **Not supported.** Same as above. |
| 21 | `nvidia/canary-1b-flash` | **Not supported.** Same architecture gap. CTC-based Canary Flash could potentially be loaded as `EncDecCTCModelBPE` but requires validation and the multi-task prompt token handling. |
| 22 | `nvidia/canary-180m` | **Not supported.** Same architecture gap. |

## NVIDIA NeMo Parakeet Family

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 23 | `nvidia/parakeet-tdt-0.6b-v3` | **Fully supported.** nemo engine (curated), onnx engine (curated alias). |
| 24 | `nvidia/parakeet-tdt-0.6b-v2` | **Supported via ONNX.** onnx engine has curated alias. Not in nemo engine's curated list (v2 omitted, only v3). **Gap:** Add v2 alias to `NeMoModelManager.SUPPORTED_MODELS` if needed. |
| 25 | `nvidia/parakeet-tdt-1.1b` | **Fully supported.** nemo engine (curated). |
| 26 | `nvidia/parakeet-ctc-1.1b` | **Fully supported.** nemo engine (curated), onnx engine (curated alias). |
| 27 | `nvidia/parakeet-rnnt-1.1b` | **Fully supported.** nemo engine (curated). |
| 28 | `nvidia/parakeet-ctc-0.6b` | **Fully supported.** nemo engine (curated), onnx engine (curated alias). |
| 29 | `nvidia/parakeet-rnnt-0.6b` | **Fully supported.** nemo engine (curated, default model), onnx engine (curated alias). |
| 30 | `nvidia/parakeet-tdt_ctc-1.1b` | **Partial.** nemo engine accepts arbitrary HF NeMo model IDs but no curated alias exists. Should work if passed as full HF ID. **Gap:** Add curated alias for convenience. |
| 31 | `nvidia/parakeet-tdt_ctc-110m` | **Partial.** Same — should work via full HF model ID passthrough in nemo engine. No curated alias. |

## NVIDIA NeMo Other Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 32 | `nvidia/stt_en_fastconformer_transducer_large` | **Supported.** nemo engine accepts arbitrary NeMo HF model IDs via passthrough. No curated alias. |
| 33 | `nvidia/stt_en_fastconformer_ctc_large` | **Supported.** nemo engine passthrough. |
| 34 | `nvidia/stt_en_fastconformer_hybrid_large_streaming_multi` | **Partial.** nemo engine passthrough may work, but hybrid models (joint CTC+RNNT) may need specific loader logic. **Gap:** Validate hybrid model loading; may need `EncDecHybridRNNTCTCBPEModel` in `ARCHITECTURE_LOADERS`. |
| 35 | `nvidia/stt_en_conformer_transducer_xlarge` | **Supported.** nemo engine passthrough. |
| 36 | `nvidia/stt_en_conformer_ctc_large` | **Supported.** nemo engine passthrough. |
| 37 | `nvidia/stt_en_citrinet_1024` | **Partial.** Citrinet uses `EncDecCTCModelBPE` — should work via nemo passthrough. Untested. |
| 38 | `nvidia/stt_en_quartznet15x5` | **Partial.** QuartzNet uses `EncDecCTCModel` (char-based, not BPE). **Gap:** nemo engine only has `EncDecCTCModelBPE` in loaders. Add `EncDecCTCModel` for char-based models. |

## Meta / Facebook wav2vec2 Family

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 39 | `facebook/wav2vec2-large-960h` | **Fully supported.** hf-asr engine (HF ASR pipeline). No word timestamps (wav2vec2 CTC doesn't produce them via HF pipeline). |
| 40 | `facebook/wav2vec2-base-960h` | **Fully supported.** hf-asr engine. |
| 41 | `facebook/wav2vec2-large-960h-lv60-self` | **Fully supported.** hf-asr engine. |
| 42 | `facebook/wav2vec2-large-xlsr-53` | **Supported.** hf-asr engine. Requires fine-tuning for specific language before use — base model alone has no CTC head. |
| 43 | `facebook/wav2vec2-xls-r-300m` | **Supported.** hf-asr engine. Same caveat — needs fine-tuned checkpoint with CTC head. |
| 44 | `facebook/wav2vec2-xls-r-1b` | **Supported.** hf-asr engine. Needs fine-tuned checkpoint. High VRAM (~6GB). |
| 45 | `facebook/wav2vec2-xls-r-2b` | **Supported.** hf-asr engine. Needs fine-tuned checkpoint. Very high VRAM (~12GB). |
| 46 | `facebook/wav2vec2-large-robust` | **Supported.** hf-asr engine. Needs fine-tuned checkpoint. |

## Meta / Facebook HuBERT & data2vec

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 47 | `facebook/hubert-large-ls960-ft` | **Fully supported.** hf-asr engine. CTC-based, no word timestamps from pipeline. |
| 48 | `facebook/hubert-xlarge-ls960-ft` | **Fully supported.** hf-asr engine. High VRAM. |
| 49 | `facebook/hubert-base-ls960` | **Partial.** hf-asr engine. Pre-trained only (no CTC head) — needs fine-tuned version for ASR. |
| 50 | `facebook/data2vec-audio-large-960h` | **Fully supported.** hf-asr engine. |
| 51 | `facebook/w2v-bert-2.0` | **Partial.** hf-asr engine. Pre-trained encoder — needs fine-tuned checkpoint with CTC head. |

## Meta / Facebook MMS & Seamless

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 52 | `facebook/mms-1b-all` | **Fully supported.** hf-asr engine. Adapter-based — set language via `language` param to select adapter. |
| 53 | `facebook/mms-1b-fl102` | **Fully supported.** hf-asr engine. |
| 54 | `facebook/seamless-m4t-v2-large` | **Partial.** hf-asr engine may load it but SeamlessM4T uses a different pipeline (`audio-text-to-text`), not `automatic-speech-recognition`. **Gap:** hf-asr engine hardcodes `automatic-speech-recognition` pipeline. Add `audio-text-to-text` pipeline support or create a new engine adapter. |
| 55 | `facebook/hf-seamless-m4t-medium` | **Partial.** Same gap as above — different HF pipeline tag. |

## IBM Granite Speech

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 56 | `ibm-granite/granite-4.0-1b-speech` | **Not supported.** Granite Speech is a speech-augmented LLM (audio encoder + LLM decoder). **Gap:** Could potentially work via vllm-asr engine if vLLM adds Granite Speech support, or needs a dedicated engine with HF `pipeline("audio-text-to-text")`. |
| 57 | `ibm-granite/granite-speech-3.3-8b` | **Not supported.** Same architecture gap — speech-augmented LLM. **Gap:** Requires vLLM support or dedicated engine. 8B params needs significant VRAM (~20GB). |
| 58 | `ibm-granite/granite-speech-3.3-2b` | **Not supported.** Same gap. |

## Microsoft Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 59 | `microsoft/phi-4-multimodal-instruct` | **Supported via vllm-asr.** vLLM supports Phi-4 multimodal. Set `DALSTON_DEFAULT_MODEL_ID=microsoft/phi-4-multimodal-instruct`. No word timestamps (LLM output is plain text). GPU-only, ~12GB VRAM. |
| 60 | `microsoft/wavlm-large` | **Partial.** hf-asr engine. Pre-trained encoder only — needs fine-tuned checkpoint with CTC head for ASR. |
| 61 | `microsoft/wavlm-base-plus` | **Partial.** Same — needs fine-tuned version. |
| 62 | `microsoft/unispeech-sat-large` | **Partial.** Same — needs fine-tuned version. |

## Alibaba / Qwen ASR

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 63 | `Qwen/Qwen3-ASR-1.7B` | **Not supported.** Qwen3-ASR uses a custom architecture (audio encoder + Qwen LLM). Not a standard HF ASR pipeline model. **Gap:** vllm-asr engine could work if vLLM adds Qwen3-ASR support. Otherwise needs dedicated engine or adapter that handles Qwen's chat template with audio input. |
| 64 | `Qwen/Qwen3-ASR-0.6B` | **Not supported.** Same gap as Qwen3-ASR-1.7B. |
| 65 | `FunAudioLLM/SenseVoiceSmall` | **Not supported.** SenseVoice uses a custom non-autoregressive architecture with FunASR toolkit. **Gap:** Needs dedicated engine wrapping FunASR inference or an HF-compatible wrapper. Not a standard `transformers` pipeline model. |

## Alibaba / FunASR (ModelScope)

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 66 | `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn` | **Not supported.** ModelScope/FunASR model, not HuggingFace `transformers` compatible. **Gap:** Needs a `funasr` engine that wraps the FunASR Python SDK. |
| 67 | `iic/speech_seaco_paraformer_large_asr_nat-zh-cn` | **Not supported.** Same gap — FunASR ecosystem. |

## Mistral AI Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 68 | `mistralai/Voxtral-Mini-3B-2507` | **Fully supported.** vllm-asr engine (default model). GPU-only, ~8GB VRAM. |
| 69 | `mistralai/Voxtral-24B-2507` | **Supported via vllm-asr.** Requires large GPU (~50GB VRAM) or multi-GPU. Set `DALSTON_DEFAULT_MODEL_ID`. |
| 70 | `mistralai/Voxtral-Small-3B-2503` | **Supported via vllm-asr.** Set `DALSTON_DEFAULT_MODEL_ID`. |

## Faster-Whisper / CTranslate2

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 71 | `Systran/faster-whisper-large-v3` | **Fully supported.** faster-whisper engine accepts HF CTranslate2 model IDs directly. |
| 72 | `Systran/faster-whisper-large-v2` | **Fully supported.** faster-whisper engine. |
| 73 | `Systran/faster-whisper-medium` | **Fully supported.** faster-whisper engine. |
| 74 | `Systran/faster-whisper-small` | **Fully supported.** faster-whisper engine. |
| 75 | `Systran/faster-whisper-base` | **Fully supported.** faster-whisper engine. |
| 76 | `Systran/faster-whisper-tiny` | **Fully supported.** faster-whisper engine. |

## SpeechBrain Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 77 | `speechbrain/asr-wav2vec2-commonvoice-en` | **Not supported.** SpeechBrain models use their own inference API (`model.transcribe_file()`), not HF `transformers` pipeline. **Gap:** hf-asr engine uses `pipeline("automatic-speech-recognition")` which doesn't load SpeechBrain models. Needs a dedicated `speechbrain` engine or an adapter that wraps `speechbrain.inference.ASR`. |
| 78 | `speechbrain/asr-wav2vec2-commonvoice-fr` | **Not supported.** Same gap. |
| 79 | `speechbrain/asr-crdnn-rnnlm-librispeech` | **Not supported.** Same gap. |
| 80 | `speechbrain/asr-streaming-conformer-librispeech` | **Not supported.** Same gap. |

## Moonshine (Useful Sensors)

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 81 | `UsefulSensors/moonshine-base` | **Partial.** hf-asr engine — Moonshine has `transformers` integration (documented in HF docs). Should work if the model's HF pipeline tag is `automatic-speech-recognition`. **Gap:** Untested. May need `trust_remote_code=True` in pipeline creation — hf-asr engine may not pass this flag. Validate and add flag if needed. |
| 82 | `UsefulSensors/moonshine-tiny` | **Partial.** Same as above. |
| 83 | `UsefulSensors/moonshine-medium-streaming` | **Partial.** Same. Streaming capability is model-native, not exploited by hf-asr engine (uses VAD-chunked mode). |

## Kyutai

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 84 | `kyutai/moshiko-pytorch-bf16` | **Not supported.** Moshi is a speech-to-speech model, not a standard ASR model. **Gap:** Entirely different paradigm (full-duplex voice). Would need a dedicated engine. Low priority for ASR use case. |

## ESPnet Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 85 | `espnet/pengcheng_guo_wenetspeech_asr_train` | **Not supported.** ESPnet models use their own inference toolkit. **Gap:** Needs a dedicated `espnet` engine wrapping `espnet2.bin.asr_inference`. Open ASR Leaderboard has ESPnet eval scripts that could inform implementation. |
| 86 | `espnet/kan-bayashi_librispeech_asr_train` | **Not supported.** Same gap. |

## Other Notable Open-Source Models

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 87 | `pyannote/speaker-diarization-3.1` | **Supported (diarization, not ASR).** Dalston has a dedicated `pyannote-4.0` diarization engine. This is a companion model used in the transcription pipeline, not a transcription model itself. |
| 88 | `Vosk (alphacephei)` | **Partial.** onnx engine's onnx-asr library supports Vosk models via passthrough. **Gap:** No curated alias; user must know the onnx-asr model name. CPU-only. |
| 89 | `FireRedTeam/FireRedASR` | **Not supported.** Custom architecture with its own inference code. **Gap:** Needs a dedicated engine or wrapper. Niche use case (Mandarin-focused). |

## Community Fine-tuned Models (High Downloads)

| # | Model ID | Dalston Support |
|---|----------|-----------------|
| 90 | `jonatasgrosman/wav2vec2-large-xlsr-53-english` | **Fully supported.** hf-asr engine. Standard wav2vec2 CTC fine-tune. |
| 91 | `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` | **Fully supported.** hf-asr engine. |
| 92 | `jonatasgrosman/wav2vec2-large-xlsr-53-spanish` | **Fully supported.** hf-asr engine. |
| 93 | `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` | **Fully supported.** hf-asr engine. |
| 94 | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | **Fully supported.** hf-asr engine. |
| 95 | `bofenghuang/whisper-large-v3-french-distil-dec2` | **Fully supported.** hf-asr engine. faster-whisper if CTranslate2 conversion exists. |
| 96 | `vasista22/whisper-hindi-large-v2` | **Fully supported.** hf-asr engine. faster-whisper if CTranslate2 conversion exists. |

## Proprietary / API-Based ASR Services

| # | Provider | Dalston Support |
|---|----------|-----------------|
| 97 | AssemblyAI Universal-2 / Slam-1 | **Not applicable.** API-only service. **Gap:** Could add an `api-proxy` engine that forwards audio to AssemblyAI's API and normalizes the response to Dalston's transcript format. |
| 98 | Deepgram Nova-3 | **Not applicable.** API-only. Same potential api-proxy engine pattern. |
| 99 | ElevenLabs Scribe | **Not applicable.** API-only. Same pattern. |
| 100 | Speechmatics | **Not applicable.** API-only. Same pattern. |

---

## Gap Summary

### High-Impact Gaps (Top Leaderboard Models)

| Gap | Models Affected | Effort | How to Close |
|-----|----------------|--------|--------------|
| **Canary loader in nemo engine** | #18-22 (Canary family, #1 on leaderboard) | Medium | Add `EncDecMultiTaskModel` to `NeMoModelManager.ARCHITECTURE_LOADERS`. Handle task/language prompt tokens in transcribe path. Alternatively, Riva NIM can serve Canary today. |
| **Speech-augmented LLM support** | #56-58 (Granite), #63-64 (Qwen3-ASR) | Medium-High | Extend vllm-asr engine or add `audio-text-to-text` pipeline support to hf-asr. Depends on vLLM adding model support. |
| **SeamlessM4T pipeline tag** | #54-55 | Low | Add `audio-text-to-text` as alternative pipeline tag in hf-asr engine's `_load_model`. |

### Medium-Impact Gaps

| Gap | Models Affected | Effort | How to Close |
|-----|----------------|--------|--------------|
| **SpeechBrain engine** | #77-80 | Medium | New engine wrapping `speechbrain.inference.ASR`. SpeechBrain has 100+ pretrained models. |
| **FunASR engine** | #65-67 (SenseVoice, Paraformer) | Medium | New engine wrapping FunASR Python SDK. Important for Mandarin market. |
| **ESPnet engine** | #85-86 | Medium | New engine wrapping `espnet2` inference. Low priority unless targeting research models. |
| **trust_remote_code in hf-asr** | #81-83 (Moonshine) | Low | Add `trust_remote_code=True` to pipeline creation in `HFTransformersModelManager._load_model()`. Validate with Moonshine. |

### Low-Impact Gaps

| Gap | Models Affected | Effort | How to Close |
|-----|----------------|--------|--------------|
| **Char-based NeMo CTC loader** | #38 (QuartzNet) | Low | Add `EncDecCTCModel` to nemo engine's `ARCHITECTURE_LOADERS`. |
| **Hybrid NeMo loader** | #34 (FastConformer hybrid) | Low | Add `EncDecHybridRNNTCTCBPEModel` to loaders. |
| **API proxy engine** | #97-100 (proprietary APIs) | Medium | Generic engine that proxies audio to third-party ASR APIs. |
| **Parakeet TDT v2 alias in nemo** | #24 | Trivial | Add `"parakeet-tdt-0.6b-v2": "nvidia/parakeet-tdt-0.6b-v2"` to `SUPPORTED_MODELS`. |

### Overall Coverage

- **Fully supported:** 53 models (all Whisper/Distil-Whisper, Parakeet, wav2vec2/HuBERT fine-tunes, Voxtral, faster-whisper)
- **Partially supported (works with caveats):** 18 models (pre-trained encoders needing fine-tuned checkpoints, passthrough model IDs, untested paths)
- **Not supported (needs new engine or loader):** 25 models (Canary, Granite, Qwen3-ASR, SpeechBrain, FunASR, ESPnet, Moshi)
- **Not applicable (API-only):** 4 services

Sources: [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard), [HuggingFace ASR Models](https://huggingface.co/models?pipeline_tag=automatic-speech-recognition&sort=downloads), [Open ASR Leaderboard Paper (arXiv:2510.06961)](https://arxiv.org/abs/2510.06961)
