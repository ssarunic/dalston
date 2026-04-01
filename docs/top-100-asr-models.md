# Top 100 ASR (Automatic Speech Recognition) Models

Curated list of the most popular, widely-used, and highest-performing ASR models as of early 2026. Ordered by overall popularity, download counts, and benchmark performance.

---

## OpenAI Whisper Family

| # | Model ID | Description |
|---|----------|-------------|
| 1 | `openai/whisper-large-v3` | 1.5B params, 100+ languages, most downloaded ASR model on HuggingFace (~11.7M downloads) |
| 2 | `openai/whisper-large-v3-turbo` | Distilled decoder (809M params), 6x faster than large-v3 with comparable accuracy |
| 3 | `openai/whisper-large-v2` | 1.5B params, predecessor to v3, still widely used in production |
| 4 | `openai/whisper-large` | 1.5B params, original large model release (Sept 2022) |
| 5 | `openai/whisper-medium` | 769M params, good accuracy-speed tradeoff for multilingual use |
| 6 | `openai/whisper-medium.en` | 769M params, English-only fine-tuned variant |
| 7 | `openai/whisper-small` | 244M params, popular for resource-constrained deployments |
| 8 | `openai/whisper-small.en` | 244M params, English-only variant |
| 9 | `openai/whisper-base` | 74M params, lightweight baseline model |
| 10 | `openai/whisper-base.en` | 74M params, English-only variant |
| 11 | `openai/whisper-tiny` | 39M params, smallest Whisper model for edge/embedded |
| 12 | `openai/whisper-tiny.en` | 39M params, English-only variant |

## Distil-Whisper (HuggingFace)

| # | Model ID | Description |
|---|----------|-------------|
| 13 | `distil-whisper/distil-large-v3.5` | Latest distilled Whisper, 756M params, near large-v3 accuracy at 6x speed |
| 14 | `distil-whisper/distil-large-v3` | Distilled from large-v3, optimized for English long-form transcription |
| 15 | `distil-whisper/distil-large-v2` | Distilled from large-v2, 50% fewer params with ~1% WER degradation |
| 16 | `distil-whisper/distil-medium.en` | Distilled medium model, English-only, fast inference |
| 17 | `distil-whisper/distil-small.en` | Distilled small model, smallest distil-whisper variant |

## NVIDIA NeMo Canary Family

| # | Model ID | Description |
|---|----------|-------------|
| 18 | `nvidia/canary-qwen-2.5b` | 2.5B params, FastConformer + Qwen3-1.7B decoder, #1 on Open ASR Leaderboard (5.63% WER) |
| 19 | `nvidia/canary-1b-v2` | 1B params, 25 EU languages, transcription + translation, CC-BY-4.0 license |
| 20 | `nvidia/canary-1b` | 1B params, 4 languages (EN/DE/ES/FR), encoder-decoder AED architecture |
| 21 | `nvidia/canary-1b-flash` | 1B params, CTC-based fast variant, 1000+ RTFx, ~10x faster inference |
| 22 | `nvidia/canary-180m` | 180M params, compact Canary variant for lightweight deployment |

## NVIDIA NeMo Parakeet Family

| # | Model ID | Description |
|---|----------|-------------|
| 23 | `nvidia/parakeet-tdt-0.6b-v3` | 600M params, multilingual (25 EU languages), TDT decoder, auto language detection |
| 24 | `nvidia/parakeet-tdt-0.6b-v2` | 600M params, English, punctuation + capitalization + timestamps, up to 24min segments |
| 25 | `nvidia/parakeet-tdt-1.1b` | 1.1B params, XXL FastConformer + TDT decoder, English |
| 26 | `nvidia/parakeet-ctc-1.1b` | 1.1B params, CTC decoder, RTFx of 2793 — fastest on Open ASR Leaderboard |
| 27 | `nvidia/parakeet-rnnt-1.1b` | 1.1B params, RNN-Transducer decoder, streaming-capable |
| 28 | `nvidia/parakeet-ctc-0.6b` | 600M params, CTC decoder, compact fast model |
| 29 | `nvidia/parakeet-rnnt-0.6b` | 600M params, RNN-Transducer decoder |
| 30 | `nvidia/parakeet-tdt_ctc-1.1b` | 1.1B params, dual TDT+CTC decoder heads |
| 31 | `nvidia/parakeet-tdt_ctc-110m` | 110M params, hybrid TDT+CTC decoders, lightweight |

## NVIDIA NeMo Other Models

| # | Model ID | Description |
|---|----------|-------------|
| 32 | `nvidia/stt_en_fastconformer_transducer_large` | FastConformer + Transducer decoder, English |
| 33 | `nvidia/stt_en_fastconformer_ctc_large` | FastConformer + CTC, English, high-throughput |
| 34 | `nvidia/stt_en_fastconformer_hybrid_large_streaming_multi` | Streaming hybrid model, multi-lookahead |
| 35 | `nvidia/stt_en_conformer_transducer_xlarge` | XL Conformer + Transducer, highest accuracy NeMo legacy |
| 36 | `nvidia/stt_en_conformer_ctc_large` | Conformer + CTC, English |
| 37 | `nvidia/stt_en_citrinet_1024` | Citrinet 1024, efficient CNN-based ASR |
| 38 | `nvidia/stt_en_quartznet15x5` | QuartzNet 15x5, compact CNN encoder |

## Meta / Facebook wav2vec2 Family

| # | Model ID | Description |
|---|----------|-------------|
| 39 | `facebook/wav2vec2-large-960h` | 315M params, fine-tuned on LibriSpeech 960h, pioneering self-supervised ASR |
| 40 | `facebook/wav2vec2-base-960h` | 95M params, base wav2vec2 fine-tuned on LibriSpeech |
| 41 | `facebook/wav2vec2-large-960h-lv60-self` | wav2vec2-large with Libri-Light 60k pre-training |
| 42 | `facebook/wav2vec2-large-xlsr-53` | 315M params, cross-lingual pre-training on 53 languages |
| 43 | `facebook/wav2vec2-xls-r-300m` | 300M params, XLS-R pre-trained on 128 languages, 436K hours |
| 44 | `facebook/wav2vec2-xls-r-1b` | 1B params, XLS-R large variant |
| 45 | `facebook/wav2vec2-xls-r-2b` | 2B params, XLS-R largest variant |
| 46 | `facebook/wav2vec2-large-robust` | 315M params, pre-trained on multi-domain data (LibriLight, CommonVoice, Switchboard) |

## Meta / Facebook HuBERT & data2vec

| # | Model ID | Description |
|---|----------|-------------|
| 47 | `facebook/hubert-large-ls960-ft` | 315M params, HuBERT fine-tuned on LibriSpeech, strong self-supervised baseline |
| 48 | `facebook/hubert-xlarge-ls960-ft` | 964M params, XL HuBERT, best self-supervised accuracy |
| 49 | `facebook/hubert-base-ls960` | 95M params, base HuBERT pre-trained model |
| 50 | `facebook/data2vec-audio-large-960h` | 315M params, data2vec self-supervised, fine-tuned on LibriSpeech |
| 51 | `facebook/w2v-bert-2.0` | 600M params, Wav2Vec2-BERT, state-of-the-art self-supervised encoder |

## Meta / Facebook MMS & Seamless

| # | Model ID | Description |
|---|----------|-------------|
| 52 | `facebook/mms-1b-all` | 1B params, Massively Multilingual Speech, ASR in 1162 languages |
| 53 | `facebook/mms-1b-fl102` | 1B params, MMS fine-tuned on FLEURS 102 languages |
| 54 | `facebook/seamless-m4t-v2-large` | 2.3B params, SeamlessM4T v2, speech-to-text + translation in 100 languages |
| 55 | `facebook/hf-seamless-m4t-medium` | Medium SeamlessM4T, speech/text translation and ASR |

## IBM Granite Speech

| # | Model ID | Description |
|---|----------|-------------|
| 56 | `ibm-granite/granite-4.0-1b-speech` | 1B params, 6 languages, Apache 2.0, latest Granite speech model |
| 57 | `ibm-granite/granite-speech-3.3-8b` | 8B params, #2 on Open ASR Leaderboard (5.85% WER), EN/FR/DE/ES |
| 58 | `ibm-granite/granite-speech-3.3-2b` | 2B params, mid-size Granite speech model |

## Microsoft Models

| # | Model ID | Description |
|---|----------|-------------|
| 59 | `microsoft/phi-4-multimodal-instruct` | 5.6B params, multimodal (text+vision+audio), Conformer speech encoder, MIT license |
| 60 | `microsoft/wavlm-large` | 315M params, WavLM pre-trained, excels at speech processing tasks |
| 61 | `microsoft/wavlm-base-plus` | 95M params, WavLM base model, versatile speech encoder |
| 62 | `microsoft/unispeech-sat-large` | 315M params, UniSpeech-SAT, speaker-aware pre-training |

## Alibaba / Qwen ASR

| # | Model ID | Description |
|---|----------|-------------|
| 63 | `Qwen/Qwen3-ASR-1.7B` | 1.7B params, 52 languages, SOTA among open-source ASR, streaming + offline unified |
| 64 | `Qwen/Qwen3-ASR-0.6B` | 600M params, 52 languages, 92ms TTFT, extremely fast inference, Apache 2.0 |
| 65 | `FunAudioLLM/SenseVoiceSmall` | Non-autoregressive, 50+ languages, ASR+LID+SER+AED, 15x faster than Whisper-Large |

## Alibaba / FunASR (ModelScope)

| # | Model ID | Description |
|---|----------|-------------|
| 66 | `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn` | Paraformer large with VAD + punctuation, Mandarin |
| 67 | `iic/speech_seaco_paraformer_large_asr_nat-zh-cn` | SeACo-Paraformer, contextual biasing for Mandarin |

## Mistral AI Models

| # | Model ID | Description |
|---|----------|-------------|
| 68 | `mistralai/Voxtral-Mini-3B-2507` | 3B params, outperforms Whisper large-v3, edge deployment |
| 69 | `mistralai/Voxtral-24B-2507` | 24B params, production-scale, beats GPT-4o mini Transcribe |
| 70 | `mistralai/Voxtral-Small-3B-2503` | 3B params, earlier Voxtral release |

## Faster-Whisper / CTranslate2

| # | Model ID | Description |
|---|----------|-------------|
| 71 | `Systran/faster-whisper-large-v3` | CTranslate2 conversion of Whisper large-v3, 4x faster inference |
| 72 | `Systran/faster-whisper-large-v2` | CTranslate2 conversion of Whisper large-v2 |
| 73 | `Systran/faster-whisper-medium` | CTranslate2 conversion of Whisper medium |
| 74 | `Systran/faster-whisper-small` | CTranslate2 conversion of Whisper small |
| 75 | `Systran/faster-whisper-base` | CTranslate2 conversion of Whisper base |
| 76 | `Systran/faster-whisper-tiny` | CTranslate2 conversion of Whisper tiny |

## SpeechBrain Models

| # | Model ID | Description |
|---|----------|-------------|
| 77 | `speechbrain/asr-wav2vec2-commonvoice-en` | wav2vec2 + CTC fine-tuned on CommonVoice English |
| 78 | `speechbrain/asr-wav2vec2-commonvoice-fr` | wav2vec2 + CTC fine-tuned on CommonVoice French |
| 79 | `speechbrain/asr-crdnn-rnnlm-librispeech` | CRDNN + RNN-LM trained on LibriSpeech |
| 80 | `speechbrain/asr-streaming-conformer-librispeech` | Conformer + RNN-T with dynamic chunk training, supports streaming |

## Moonshine (Useful Sensors)

| # | Model ID | Description |
|---|----------|-------------|
| 81 | `UsefulSensors/moonshine-base` | ~200M params, RoPE-based, optimized for real-time edge ASR |
| 82 | `UsefulSensors/moonshine-tiny` | ~27M params, smallest competitive ASR model for embedded devices |
| 83 | `UsefulSensors/moonshine-medium-streaming` | 250M params, lower WER than Whisper Large-v3, streaming support |

## Kyutai

| # | Model ID | Description |
|---|----------|-------------|
| 84 | `kyutai/moshiko-pytorch-bf16` | Moshi speech-to-speech model with ASR capabilities |

## ESPnet Models

| # | Model ID | Description |
|---|----------|-------------|
| 85 | `espnet/pengcheng_guo_wenetspeech_asr_train` | ESPnet Conformer trained on WenetSpeech (Mandarin) |
| 86 | `espnet/kan-bayashi_librispeech_asr_train` | ESPnet Transformer on LibriSpeech |

## Other Notable Open-Source Models

| # | Model ID | Description |
|---|----------|-------------|
| 87 | `pyannote/speaker-diarization-3.1` | Essential companion model for multi-speaker ASR pipelines (~11.8M downloads) |
| 88 | `Vosk (alphacephei)` | Kaldi-based DNN-HMM, 20+ languages, optimized for mobile/offline/embedded |
| 89 | `FireRedTeam/FireRedASR` | Industrial-grade Mandarin + English, SOTA on public Mandarin benchmarks |

## Community Fine-tuned Models (High Downloads)

| # | Model ID | Description |
|---|----------|-------------|
| 90 | `jonatasgrosman/wav2vec2-large-xlsr-53-english` | XLSR-53 fine-tuned on English CommonVoice |
| 91 | `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` | XLSR-53 fine-tuned on Chinese |
| 92 | `jonatasgrosman/wav2vec2-large-xlsr-53-spanish` | XLSR-53 fine-tuned on Spanish |
| 93 | `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` | XLSR-53 fine-tuned on Japanese |
| 94 | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | XLSR-53 fine-tuned on Arabic |
| 95 | `bofenghuang/whisper-large-v3-french-distil-dec2` | Distilled Whisper for French |
| 96 | `vasista22/whisper-hindi-large-v2` | Whisper large-v2 fine-tuned for Hindi |

## Proprietary / API-Based ASR Services

| # | Provider | Description |
|---|----------|-------------|
| 97 | AssemblyAI Universal-2 / Slam-1 | Production ASR API, best-in-class English formatting, PII detection, 18+ languages |
| 98 | Deepgram Nova-3 | Low-latency streaming ASR API, 50+ languages, call center analytics |
| 99 | ElevenLabs Scribe | Top long-form accuracy on Open ASR Leaderboard, 99 languages, $0.40/hr |
| 100 | Speechmatics | Enterprise ASR, 50+ languages, on-prem and cloud, strong accent handling |

---

## Key Takeaways

- **Highest accuracy (English):** NVIDIA Canary-Qwen-2.5B (5.63% WER), IBM Granite Speech, Microsoft Phi-4
- **Most downloaded:** OpenAI Whisper family dominates with millions of cumulative downloads
- **Fastest inference:** NVIDIA Parakeet CTC 1.1B (RTFx 2793), Parakeet TDT models
- **Best multilingual:** Whisper large-v3 (100+ languages), MMS (1100+ languages), Qwen3-ASR (52 languages)
- **Best for edge/embedded:** Moonshine Tiny, Whisper Tiny, faster-whisper variants, Vosk
- **Best self-supervised:** wav2vec2, HuBERT, WavLM (ideal for low-resource fine-tuning)
- **Best for Mandarin:** Alibaba Paraformer, FunASR, FireRedASR

Sources: [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard), [HuggingFace ASR Models](https://huggingface.co/models?pipeline_tag=automatic-speech-recognition&sort=downloads), [Open ASR Leaderboard Paper (arXiv:2510.06961)](https://arxiv.org/abs/2510.06961), [Top Open Source STT Models - Modal](https://modal.com/blog/open-source-stt), [NVIDIA Speech AI Blog](https://developer.nvidia.com/blog/nvidia-speech-ai-models-deliver-industry-leading-accuracy-and-performance/)
