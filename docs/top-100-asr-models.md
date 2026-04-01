# Top 100 ASR (Automatic Speech Recognition) Models

Curated list of the most popular, widely-used, and highest-performing ASR models as of early 2026. Ordered by overall popularity, download counts, and benchmark performance.

---

## OpenAI Whisper Family

| # | Model ID | Description |
|---|----------|-------------|
| 1 | `openai/whisper-large-v3` | 1.5B params, 100+ languages, most downloaded ASR model on HuggingFace (~11.7M downloads) |
| 2 | `openai/whisper-large-v3-turbo` | Distilled decoder (809M params), 6x faster than large-v3 with comparable accuracy |
| 3 | `openai/whisper-large-v2` | 1.5B params, predecessor to v3, still widely used in production |
| 4 | `openai/whisper-large` | 1.5B params, original large model release |
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
| 19 | `nvidia/canary-1b-v2` | 1B params, 25 EU languages, transcription + translation, permissive license |
| 20 | `nvidia/canary-1b` | 1B params, 4 languages (EN/DE/ES/FR), encoder-decoder AED architecture |
| 21 | `nvidia/canary-1b-flash` | 1B params, CTC-based fast variant, ~10x faster inference than autoregressive |

## NVIDIA NeMo Parakeet Family

| # | Model ID | Description |
|---|----------|-------------|
| 22 | `nvidia/parakeet-tdt-0.6b-v3` | 600M params, multilingual (25 EU languages), TDT decoder, auto language detection |
| 23 | `nvidia/parakeet-tdt-0.6b-v2` | 600M params, English, punctuation + capitalization + timestamps, up to 24min segments |
| 24 | `nvidia/parakeet-ctc-1.1b` | 1.1B params, CTC decoder, RTFx of 2793 — fastest on Open ASR Leaderboard |
| 25 | `nvidia/parakeet-rnnt-1.1b` | 1.1B params, RNN-Transducer decoder, streaming-capable |
| 26 | `nvidia/parakeet-ctc-0.6b` | 600M params, CTC decoder, compact fast model |
| 27 | `nvidia/parakeet-rnnt-0.6b` | 600M params, RNN-Transducer decoder |
| 28 | `nvidia/parakeet-tdt_ctc-110m` | 110M params, hybrid TDT+CTC decoders, lightweight |

## NVIDIA NeMo Other Models

| # | Model ID | Description |
|---|----------|-------------|
| 29 | `nvidia/stt_en_fastconformer_transducer_large` | FastConformer encoder + Transducer decoder, English |
| 30 | `nvidia/stt_en_fastconformer_ctc_large` | FastConformer + CTC, English, high-throughput |
| 31 | `nvidia/stt_en_fastconformer_hybrid_large_streaming_multi` | Streaming hybrid model, multi-lookahead |
| 32 | `nvidia/stt_en_conformer_transducer_xlarge` | XL Conformer + Transducer, highest accuracy NeMo legacy |
| 33 | `nvidia/stt_en_conformer_ctc_large` | Conformer + CTC, English |
| 34 | `nvidia/stt_en_citrinet_1024` | Citrinet 1024, efficient CNN-based ASR |
| 35 | `nvidia/stt_en_jasper10x5dr` | Jasper 10x5, legacy NVIDIA ASR model |
| 36 | `nvidia/stt_en_quartznet15x5` | QuartzNet 15x5, compact CNN encoder |
| 37 | `nvidia/stt_de_fastconformer_hybrid_large_pc` | German FastConformer with punctuation/capitalization |
| 38 | `nvidia/stt_es_fastconformer_hybrid_large_pc` | Spanish FastConformer with punctuation/capitalization |

## Meta / Facebook Models

| # | Model ID | Description |
|---|----------|-------------|
| 39 | `facebook/wav2vec2-large-960h` | 315M params, wav2vec2 fine-tuned on LibriSpeech 960h, pioneering self-supervised ASR |
| 40 | `facebook/wav2vec2-base-960h` | 95M params, base wav2vec2 fine-tuned on LibriSpeech |
| 41 | `facebook/wav2vec2-large-960h-lv60-self` | wav2vec2-large with Libri-Light 60k pre-training |
| 42 | `facebook/wav2vec2-large-xlsr-53` | 315M params, cross-lingual pre-training on 53 languages |
| 43 | `facebook/wav2vec2-xls-r-300m` | 300M params, XLS-R pre-trained on 128 languages, 436K hours |
| 44 | `facebook/wav2vec2-xls-r-1b` | 1B params, XLS-R large variant |
| 45 | `facebook/wav2vec2-xls-r-2b` | 2B params, XLS-R largest variant |
| 46 | `facebook/hubert-large-ls960-ft` | 315M params, HuBERT fine-tuned on LibriSpeech, strong self-supervised baseline |
| 47 | `facebook/hubert-xlarge-ls960-ft` | 964M params, XL HuBERT, best self-supervised accuracy |
| 48 | `facebook/hubert-base-ls960` | 95M params, base HuBERT pre-trained model |
| 49 | `facebook/mms-1b-all` | 1B params, Massively Multilingual Speech, 1100+ languages |
| 50 | `facebook/mms-1b-fl102` | 1B params, MMS fine-tuned on FLEURS 102 languages |
| 51 | `facebook/data2vec-audio-large-960h` | 315M params, data2vec self-supervised, fine-tuned on LibriSpeech |
| 52 | `facebook/seamless-m4t-v2-large` | 2.3B params, SeamlessM4T v2, speech-to-text + translation in 100 languages |
| 53 | `facebook/w2v-bert-2.0` | 600M params, Wav2Vec2-BERT, state-of-the-art self-supervised encoder |

## IBM Models

| # | Model ID | Description |
|---|----------|-------------|
| 54 | `ibm-granite/granite-speech-3.3-8b` | 8B params, #2 on Open ASR Leaderboard (5.85% WER), EN/FR/DE/ES |
| 55 | `ibm-granite/granite-speech-3.3-2b` | 2B params, smaller Granite speech model |

## Microsoft Models

| # | Model ID | Description |
|---|----------|-------------|
| 56 | `microsoft/phi-4-multimodal-instruct` | Phi-4 multimodal with speech input, top-3 on ASR leaderboard |
| 57 | `microsoft/wavlm-large` | 315M params, WavLM pre-trained, excels at speech processing tasks |
| 58 | `microsoft/wavlm-base-plus` | 95M params, WavLM base model, versatile speech encoder |
| 59 | `microsoft/unispeech-sat-large` | 315M params, UniSpeech-SAT, speaker-aware pre-training |

## Mistral AI Models

| # | Model ID | Description |
|---|----------|-------------|
| 60 | `mistralai/Voxtral-Mini-3B-2507` | 3B params, outperforms Whisper large-v3, edge deployment |
| 61 | `mistralai/Voxtral-24B-2507` | 24B params, production-scale, beats GPT-4o mini Transcribe |
| 62 | `mistralai/Voxtral-Small-3B-2503` | 3B params, earlier Voxtral release |

## Google / DeepMind Models

| # | Model ID | Description |
|---|----------|-------------|
| 63 | `google/usm` | Universal Speech Model, 2B params, 300+ languages (API-based) |
| 64 | `google/chirp` | Cloud Speech-to-Text v2 model, 100+ languages (API-based) |
| 65 | `google/long-t5-tglobal-large` | T5 variant used for long-form audio tasks |

## SpeechBrain Models

| # | Model ID | Description |
|---|----------|-------------|
| 66 | `speechbrain/asr-wav2vec2-commonvoice-en` | wav2vec2 + CTC fine-tuned on CommonVoice English |
| 67 | `speechbrain/asr-wav2vec2-commonvoice-fr` | wav2vec2 + CTC fine-tuned on CommonVoice French |
| 68 | `speechbrain/asr-crdnn-rnnlm-librispeech` | CRDNN + RNN-LM trained on LibriSpeech |
| 69 | `speechbrain/asr-transformer-transformerlm-librispeech` | Transformer encoder-decoder on LibriSpeech |

## Faster-Whisper / CTranslate2

| # | Model ID | Description |
|---|----------|-------------|
| 70 | `Systran/faster-whisper-large-v3` | CTranslate2 conversion of Whisper large-v3, 4x faster inference |
| 71 | `Systran/faster-whisper-large-v2` | CTranslate2 conversion of Whisper large-v2 |
| 72 | `Systran/faster-whisper-medium` | CTranslate2 conversion of Whisper medium |
| 73 | `Systran/faster-whisper-small` | CTranslate2 conversion of Whisper small |
| 74 | `Systran/faster-whisper-base` | CTranslate2 conversion of Whisper base |
| 75 | `Systran/faster-whisper-tiny` | CTranslate2 conversion of Whisper tiny |

## Moonshine (Useful Sensors)

| # | Model ID | Description |
|---|----------|-------------|
| 76 | `UsefulSensors/moonshine-base` | 61M params, optimized for edge/real-time ASR |
| 77 | `UsefulSensors/moonshine-tiny` | 27M params, smallest footprint for embedded devices |

## Kyutai / Moshi

| # | Model ID | Description |
|---|----------|-------------|
| 78 | `kyutai/moshiko-pytorch-bf16` | Moshi speech-to-speech model with ASR capabilities |

## ESPnet Models

| # | Model ID | Description |
|---|----------|-------------|
| 79 | `espnet/pengcheng_guo_wenetspeech_asr_train` | ESPnet Conformer trained on WenetSpeech (Mandarin) |
| 80 | `espnet/kan-bayashi_librispeech_asr_train` | ESPnet Transformer on LibriSpeech |

## Alibaba / FunASR

| # | Model ID | Description |
|---|----------|-------------|
| 81 | `FunAudioLLM/SenseVoiceSmall` | Multi-language ASR + emotion detection + audio events, compact |
| 82 | `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn` | Paraformer large with VAD + punctuation, Mandarin |
| 83 | `iic/speech_seaco_paraformer_large_asr_nat-zh-cn` | SeACo-Paraformer, contextual biasing for Mandarin |

## Cohere / Aya

| # | Model ID | Description |
|---|----------|-------------|
| 84 | `CohereForAI/aya-speech` | Multilingual speech model from Cohere for AI |

## WhisperX Ecosystem

| # | Model ID | Description |
|---|----------|-------------|
| 85 | `WAV2VEC2_ASR_LARGE_LV60K_960H` (torchaudio) | wav2vec2 alignment model used by WhisperX for word timestamps |

## Community Fine-tuned Models (High Downloads)

| # | Model ID | Description |
|---|----------|-------------|
| 86 | `jonatasgrosman/wav2vec2-large-xlsr-53-english` | XLSR-53 fine-tuned on English CommonVoice |
| 87 | `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` | XLSR-53 fine-tuned on Chinese |
| 88 | `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` | XLSR-53 fine-tuned on Japanese |
| 89 | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | XLSR-53 fine-tuned on Arabic |
| 90 | `bofenghuang/whisper-large-v3-french-distil-dec2` | Distilled Whisper for French |
| 91 | `primeline/whisper-large-v3-german` | Whisper large-v3 fine-tuned for German |

## Proprietary / API-Based ASR Services

| # | Provider | Description |
|---|----------|-------------|
| 92 | AssemblyAI Universal-2 | Production ASR API, best-in-class for English, supports 18 languages |
| 93 | Deepgram Nova-3 | Low-latency streaming ASR API, 50+ languages |
| 94 | ElevenLabs Scribe | Top long-form accuracy on Open ASR Leaderboard, 99 languages |
| 95 | Rev AI | Human-hybrid ASR, strong long-form performance |
| 96 | Speechmatics | Enterprise ASR, strong multilingual + long-form |
| 97 | Google Cloud Speech-to-Text v2 (Chirp) | Managed ASR service, 125+ languages |
| 98 | AWS Transcribe | Amazon managed ASR, 100+ languages, streaming support |
| 99 | Azure Speech-to-Text | Microsoft managed ASR with custom model training |
| 100 | Aqua Voice | Specialized high-accuracy ASR, on Open ASR Leaderboard |

---

## Key Takeaways

- **Highest accuracy (English):** NVIDIA Canary-Qwen-2.5B (5.63% WER), IBM Granite Speech 3.3 8B, Microsoft Phi-4
- **Most downloaded:** OpenAI Whisper family dominates with millions of cumulative downloads
- **Fastest inference:** NVIDIA Parakeet CTC 1.1B (RTFx 2793), Parakeet TDT models
- **Best multilingual:** Whisper large-v3 (100+ languages), MMS (1100+ languages), Canary-1b-v2 (25 EU languages)
- **Best for edge/embedded:** Moonshine Tiny, Whisper Tiny, faster-whisper variants
- **Best self-supervised:** wav2vec2, HuBERT, WavLM (ideal for low-resource fine-tuning)

Sources: [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard), [HuggingFace ASR Models](https://huggingface.co/models?pipeline_tag=automatic-speech-recognition&sort=downloads), [Open ASR Leaderboard Paper (arXiv:2510.06961)](https://arxiv.org/abs/2510.06961)
