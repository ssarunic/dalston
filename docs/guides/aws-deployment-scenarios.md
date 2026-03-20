# AWS Deployment Scenarios

Deployment options for Dalston on AWS, ordered from simplest/cheapest to most capable. Each scenario builds on the previous one.

The engine_id-based engine architecture loads models dynamically from HuggingFace — 12 engine_ids, 18 cataloged models, and any HF-compatible model via `hf-asr`. The orchestrator's engine selector automatically picks the best engine_id and downloaded model for each job based on language, capabilities, and hardware. The core question is: **how much GPU do you need, and which engine_ids do you want running?**

---

## Quick Reference

| Scenario | Instance | GPU | Monthly (8h/day) | w/ Spot | Runtimes |
|----------|----------|-----|-------------------|---------|----------|
| 1. CPU-only | t3.xlarge | None | ~$35 | N/A | onnx (EN), faster-whisper (multi) |
| 2. Single GPU | g5.xlarge | 1x A10G 24GB | ~$100 | ~$35 | All batch + realtime + diarization |
| 3. Dual-purpose GPU | g5.2xlarge | 1x A10G 24GB | ~$150 | ~$50 | Higher throughput, concurrent batch+RT |
| 4. Multi-GPU | g5.12xlarge | 4x A10G 96GB | ~$500 | ~$170 | Full parallel pipeline + vllm-asr |
| 5. Split CPU/GPU | t3.medium + g5.xlarge | 1x A10G 24GB | ~$87 (spot) | GPU spot | Always-on API + GPU on demand |
| 6. Auto-scaling | ECS + g5 | Varies | ~$200+ | Mixed | Auto-scaling engines |

Spot instances save ~65% on GPU instances. See [Spot Instances](#spot-instances) below.

### Runtimes at a glance

Runtimes are inference engines that load models dynamically. Each engine_id is a Docker container that can serve multiple models from the same family.

**Batch engine_ids** (queue-based, file I/O):

| Runtime | Stage | Languages | GPU required | Key models |
|---------|-------|-----------|-------------|------------|
| **nemo** | transcribe | EN | No (slow on CPU) | parakeet-tdt-1.1b, parakeet-ctc-0.6b |
| **onnx** | transcribe | EN | No | Same models, 12x smaller image, better CPU perf |
| **faster-whisper** | transcribe | 99 langs | No | large-v3-turbo, large-v3, medium, small, base, tiny |
| **hf-asr** | transcribe | Varies | No | Any HuggingFace ASR model (10,000+) |
| **vllm-asr** | transcribe | 8-12 langs | **Yes** | Voxtral Mini 3B, Qwen2-Audio 7B, Voxtral Small 24B |
| **pyannote-4.0** | diarize | All | No (slow on CPU) | Community-1 pipeline (needs HF_TOKEN) |
| **nemo-msdd** | diarize | All | No (slow on CPU) | NeMo MSDD (open CC-BY-4.0, no HF_TOKEN) |
| **phoneme-align** | align | 35+ langs | No | wav2vec2-based forced alignment |
| **pii-presidio** | pii_detect | All | No | GLiNER NER + Presidio checksums |
| **audio-redactor** | audio_redact | N/A | No | FFmpeg silence/beep replacement |
| **audio-prepare** | prepare | N/A | No | FFmpeg format conversion |
| **final-merger** | merge | N/A | No | Transcript assembly |

**Realtime engine_ids** (WebSocket streaming, low-latency):

| Runtime | Languages | GPU required | Latency | Key detail |
|---------|-----------|-------------|---------|------------|
| **parakeet** (RNNT 0.6B) | EN | **Yes** | Sub-200ms | True streaming via cache-aware encoder |
| **parakeet** (RNNT 1.1B) | EN | **Yes** | ~120ms | Higher accuracy, ~6 GB VRAM |
| **parakeet-onnx** (TDT/CTC/RNNT) | EN | No | ~50ms warm | VAD-chunked (not true streaming), CPU-friendly |
| **faster-whisper** | 99 langs | No (slow) | ~200ms | VAD-chunked, multilingual, distil-whisper for speed |
| **voxtral** (Mini 4B) | 13 langs | **Yes** | <500ms | Native streaming LLM, ~16 GB VRAM |

The engine selector automatically picks the best engine_id for each pipeline stage based on job language, requested model, and what's currently running. See `dalston/orchestrator/engine_selector.py`.

---

## Scenario 1: CPU-Only (`t3.xlarge`)

**For**: Experimentation, low-volume English transcription, cost-sensitive.

```
┌──────────────────────────────────────────────┐
│  EC2: t3.xlarge (4 vCPU, 16 GB RAM)         │
│                                              │
│  ┌────────────┐  ┌──────────────┐            │
│  │  Gateway   │  │ Orchestrator │            │
│  └────────────┘  └──────────────┘            │
│  ┌────────────┐  ┌──────────────┐            │
│  │   Redis    │  │   Postgres   │            │
│  └────────────┘  └──────────────┘            │
│                                              │
│  Runtimes (CPU):                             │
│  ┌──────────────────────────────────┐        │
│  │ onnx (parakeet-tdt-0.6b-v3)│ 4 GB   │
│  └──────────────────────────────────┘        │
│  ┌──────────────────────────────────┐        │
│  │ faster-whisper (large-v3-turbo)  │ 8 GB   │
│  └──────────────────────────────────┘        │
│  ┌──────────┐  ┌────────┐  ┌───────┐        │
│  │ prepare  │  │ align  │  │ merge │        │
│  └──────────┘  └────────┘  └───────┘        │
│                                              │
│  + Tailscale                                 │
└──────────────────────────────────────────────┘
```

### What you get

- **English**: onnx with parakeet-tdt-0.6b-v3 — native word timestamps, punctuation + capitalization, ~8x realtime on CPU (RTF 0.12). The ~1 GB container image starts in seconds vs ~12 GB for full NeMo.
- **Multilingual**: faster-whisper with large-v3-turbo — 99 languages, ~3.3x realtime on CPU (RTF 0.3)
- **Realtime (limited)**: parakeet-onnx realtime works on CPU (VAD-chunked, not true streaming) — usable for single-session English. faster-whisper realtime works on CPU for multilingual but is slow (RTF 0.5).
- Batch-only for throughput; realtime is single-session and latency-sensitive on CPU
- The engine selector auto-routes: English jobs → onnx, non-English → faster-whisper

**onnx model choices** (all English, all have native word timestamps):

| Model | RTF (CPU) | Punctuation | Size |
|-------|-----------|-------------|------|
| parakeet-tdt-0.6b-v3 | 0.12 | Yes | 0.6 GB |
| parakeet-ctc-0.6b | 0.15 | No | 0.6 GB |
| parakeet-ctc-1.1b | 0.20 | No | 1.2 GB |
| parakeet-rnnt-0.6b | 0.12 | No | 0.6 GB |

parakeet-tdt-0.6b-v3 is the best default — same speed as rnnt, better accuracy, and includes punctuation/capitalization so you skip the refine stage.

### How to deploy

Use `dalston-aws` with a CPU instance type:

```bash
dalston-aws setup --instance-type t3.xlarge

# On the instance — CPU-only, no --profile gpu
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-prepare stt-transcribe-onnx \
  stt-transcribe-faster-whisper stt-align-phoneme-cpu stt-merge
```

### Memory budget (16 GB)

Only one transcription engine_id loads at a time — the orchestrator routes tasks to the right engine_id, and idle engine_ids consume minimal memory until a task arrives.

| Component | RAM |
|-----------|-----|
| OS + Docker | ~1.5 GB |
| Redis + Postgres | ~1 GB |
| Gateway + Orchestrator | ~0.5 GB |
| onnx (parakeet-tdt-0.6b-v3) | ~4 GB |
| faster-whisper (large-v3-turbo, idle) | ~1 GB |
| prepare + align + merge | ~1 GB |
| **Headroom** | **~7 GB** |

When faster-whisper actively processes a task, it loads the model (~8 GB peak). The onnx engine_id is lightweight enough that both can coexist, but for sustained mixed workloads consider running only one transcription engine_id and relying on model swapping.

### Cost

| State | Monthly |
|-------|---------|
| Running 24/7 | ~$135 |
| Running 8h/day weekdays | ~$35 |
| Stopped | ~$6 |

### Limitations

- Transcription is slow (~3-8x realtime, i.e., a 10min file takes 1.25-3.3 min)
- No realtime/streaming
- No diarization — pyannote and nemo-msdd both work on CPU but are slow (~1-2x realtime, meaning a 10min file takes 10-20 min). Acceptable for occasional use, not for every job
- t3 is burstable: sustained back-to-back transcription will exhaust CPU credits and drop to 40% baseline. For sustained CPU workloads, use m5.xlarge (same price, non-burstable)

---

## Scenario 2: Single GPU — The Sweet Spot (`g5.xlarge`)

**For**: Your primary use case. Parakeet for English, faster-whisper for other languages, realtime streaming, diarization.

```
┌──────────────────────────────────────────────────────┐
│  EC2: g5.xlarge (4 vCPU, 16 GB RAM, 1x A10G 24 GB)  │
│                                                      │
│  CPU side:                                           │
│  ┌────────────┐  ┌──────────────┐                    │
│  │  Gateway   │  │ Orchestrator │                    │
│  └────────────┘  └──────────────┘                    │
│  ┌────────────┐  ┌──────────────┐                    │
│  │   Redis    │  │   Postgres   │                    │
│  └────────────┘  └──────────────┘                    │
│  ┌──────────┐  ┌────────┐  ┌───────┐                │
│  │ prepare  │  │ align  │  │ merge │                │
│  └──────────┘  └────────┘  └───────┘                │
│                                                      │
│  GPU (24 GB VRAM, shared):                           │
│  ┌──────────────────────────────────────┐            │
│  │ nemo (parakeet-tdt-1.1b)            │ ~6 GB VRAM │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ faster-whisper (large-v3-turbo)      │ ~4 GB VRAM │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ pyannote-4.0 or nemo-msdd (diarize) │ ~2-4 VRAM  │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ RT: parakeet-rnnt-0.6b (streaming)  │ ~2 GB VRAM │
│  └──────────────────────────────────────┘            │
│                                                      │
│  + Tailscale                                         │
└──────────────────────────────────────────────────────┘
```

### What you get

- **English batch**: Parakeet TDT 1.1B via NeMo — best English accuracy (<7% avg WER), native word timestamps, RTF 0.0006 (~1667x realtime on GPU)
- **Multilingual batch**: faster-whisper large-v3-turbo — 99 languages, RTF 0.03 (~33x realtime on GPU)
- **Realtime streaming** — multiple options depending on needs:

  | Realtime engine | Languages | VRAM | Latency | Notes |
  |-----------------|-----------|------|---------|-------|
  | parakeet RNNT 0.6B | EN | ~2 GB | <200ms | True streaming, lightweight |
  | parakeet RNNT 1.1B | EN | ~6 GB | ~120ms | Higher accuracy |
  | parakeet-onnx TDT 0.6B v3 | EN | ~2 GB | ~50ms | VAD-chunked, punctuation |
  | faster-whisper | 99 langs | ~6 GB | ~200ms | Multilingual realtime |
  | voxtral Mini 4B | 13 langs | ~16 GB | <500ms | LLM streaming (uses most of the VRAM budget) |

  Default recommendation: **parakeet RNNT 0.6B** for English (true streaming, small VRAM footprint). Add **faster-whisper RT** if you need multilingual realtime.

- **Diarization**: pyannote 4.0 (RTF 0.08) or nemo-msdd (RTF 0.05, no HF_TOKEN needed)
- **Full pipeline**: prepare → transcribe → align → diarize → merge
- **Auto-routing**: The engine selector picks the best engine_id per job — English → nemo, non-English → faster-whisper. Parakeet's native word timestamps skip the align stage entirely.

### VRAM budget (24 GB)

The key constraint. Runtimes load models on demand and report loaded state via heartbeat. Plan for what's loaded simultaneously:

| Concurrent load | VRAM |
|-----------------|------|
| nemo (parakeet-tdt-1.1b) | ~6 GB |
| faster-whisper (large-v3-turbo) | ~4 GB |
| pyannote-4.0 | ~2 GB |
| RT parakeet-rnnt-0.6b | ~2 GB |
| **Total if all loaded** | **~14 GB** |
| CUDA overhead + buffers | ~2 GB |
| **Available headroom** | **~8 GB** |

All four fit simultaneously on 24 GB — comfortable. Adding phoneme-align GPU (~2 GB) or nemo-msdd (~4 GB) still fits.

**If you only run English** (nemo for batch + RT parakeet-rnnt-0.6b + diarization): ~10-12 GB used, 12-14 GB free. Plenty of room to experiment with additional models.

**Realtime VRAM trade-offs**: If you add faster-whisper RT for multilingual streaming (+6 GB) or voxtral Mini 4B for LLM streaming (+16 GB), the budget gets tighter. voxtral RT alone would consume ~16 GB, leaving only ~6 GB for batch — enough for one transcription engine_id but not both nemo and faster-whisper simultaneously. For voxtral RT + full batch, Scenario 3 (g5.2xlarge) gives more CPU/RAM headroom, or Scenario 4 with dedicated GPUs.

**Lighter alternative**: Use onnx instead of full NeMo — same Parakeet models, ~2 GB VRAM, ~12x smaller container image. Slightly slower on GPU but negligible for single-file workloads. Frees VRAM for other engine_ids.

### How to deploy

```bash
dalston-aws setup --instance-type g5.xlarge

# On the instance — full GPU profile
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra --profile gpu up -d
```

Or cherry-pick engine_ids:

```bash
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-prepare stt-merge \
  stt-transcribe-nemo \
  stt-transcribe-faster-whisper \
  stt-align-phoneme \
  stt-diarize-pyannote-4.0 \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

**Using nemo-msdd instead of pyannote** (no HF_TOKEN required, open CC-BY-4.0 license):

```bash
# Replace stt-diarize-pyannote-4.0 with:
  stt-diarize-nemo-msdd
```

### Cost

| State | Monthly |
|-------|---------|
| Running 24/7 | ~$300 |
| Running 8h/day weekdays | ~$100 |
| Stopped | ~$6 |

### Why this is the sweet spot

1. **Parakeet TDT 1.1B** is the best English model — native word timestamps skip the align stage, first model to achieve <7.0% avg WER on the HuggingFace Open ASR leaderboard
2. **faster-whisper** handles everything non-English at ~33x realtime
3. **Engine selector auto-routing** — submit a job and the orchestrator picks the best downloaded model for the job's language. Or explicitly request `model=nvidia/parakeet-tdt-1.1b` to pin a specific model.
4. **Model swapping** — both engine_ids share the GPU. The nemo engine_id loads parakeet-tdt-1.1b for one job, could load parakeet-ctc-0.6b for the next. No container restart needed.
5. **Two diarization options**: pyannote 4.0 (battle-tested, needs HF_TOKEN) or nemo-msdd (open license, built-in overlap detection)
6. Realtime streaming for live English transcription
7. One machine, one `dalston-aws setup`, done

---

## Scenario 3: More Headroom (`g5.2xlarge`)

**For**: Higher throughput, or running batch + realtime simultaneously without contention.

Same architecture as Scenario 2, but:

| | g5.xlarge | g5.2xlarge |
|---|-----------|------------|
| vCPU | 4 | 8 |
| RAM | 16 GB | 32 GB |
| GPU | 1x A10G 24 GB | 1x A10G 24 GB |
| Cost (24/7) | ~$300/mo | ~$450/mo |

The GPU is identical — the extra spend buys more CPU and RAM for:

- Running more utility engines concurrently (prepare, align, merge, PII detection run CPU-side)
- Higher batch throughput (CPU-bound stages don't bottleneck)
- Comfortable headroom for web console, monitoring stack (Prometheus/Grafana)
- Could add onnx as a secondary CPU transcription engine_id alongside GPU nemo
- PII detection (pii-presidio) + audio redaction without RAM pressure

```bash
dalston-aws setup --instance-type g5.2xlarge
```

Everything else is identical to Scenario 2. Only worth it if you're hitting CPU/RAM limits.

---

## Scenario 4: Multi-GPU Power (`g5.12xlarge`)

**For**: Production workloads, parallel pipeline stages, multiple concurrent realtime sessions, audio LLMs (Voxtral, Qwen2-Audio).

```
┌───────────────────────────────────────────────────────────────┐
│  EC2: g5.12xlarge (48 vCPU, 192 GB RAM, 4x A10G 96 GB)      │
│                                                               │
│  GPU 0 (24 GB): Batch transcription                          │
│  ┌─────────────────────────┐  ┌────────────────────────────┐  │
│  │ nemo (parakeet-tdt-1.1b)│  │ faster-whisper (lg-v3-turbo)│ │
│  └─────────────────────────┘  └────────────────────────────┘  │
│                                                               │
│  GPU 1 (24 GB): Realtime + diarization                       │
│  ┌─────────────────────────┐  ┌────────────────────────────┐  │
│  │ RT parakeet-rnnt (EN)   │  │ RT faster-whisper (multi)  │  │
│  └─────────────────────────┘  └────────────────────────────┘  │
│  ┌─────────────────────────┐                                  │
│  │ nemo-msdd (diarize)     │                                  │
│  └─────────────────────────┘                                  │
│                                                               │
│  GPU 2 (24 GB): Audio LLM — batch + realtime                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ vllm-asr batch (Voxtral-Mini-3B or Qwen2-Audio-7B)     │ │
│  │ + voxtral RT (Mini 4B, 13-lang streaming)               │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  GPU 3 (24 GB): Alignment + PII + spare                      │
│  ┌─────────────────────────┐  ┌────────────────────────────┐  │
│  │ phoneme-align (GPU)     │  │ pii-presidio (GPU)         │  │
│  └─────────────────────────┘  └────────────────────────────┘  │
│                                                               │
│  CPU: Gateway, Orchestrator, Redis, Postgres, prepare, merge  │
└───────────────────────────────────────────────────────────────┘
```

### What this enables

- **True parallel pipeline**: Transcribe job N while aligning job N-1 and diarizing job N-2
- **Multiple concurrent realtime sessions**: Dedicated GPU for streaming (EN + multilingual), no batch contention. Parakeet RNNT for English, faster-whisper RT for multilingual, voxtral RT for 13-language LLM streaming.
- **Audio LLMs via vllm-asr**: Highest-accuracy transcription for supported languages. Three model options:

  | Model | VRAM | Languages | RTF (GPU) | Notes |
  |-------|------|-----------|-----------|-------|
  | Voxtral Mini 3B | ~8 GB | en, es, fr, pt, hi, de, nl, it | 0.12 | Best speed/accuracy trade-off |
  | Qwen2-Audio 7B | ~16 GB | en, zh, ja, ko, fr, de, es, pt, it, nl, ru, ar | 0.18 | Best CJK language support |
  | Voxtral Small 24B | ~55 GB | en, es, fr, pt, hi, de, nl, it | 0.20 | Needs 3x A10G (multi-GPU) |

  Audio LLMs produce text only — no word timestamps. Chain with phoneme-align stage for timing.

- **nemo-msdd diarization**: Neural end-to-end system with built-in overlap detection, open CC-BY-4.0 license (no HF_TOKEN). Or use pyannote-4.0 if you prefer.
- **PII detection + audio redaction on GPU**: GLiNER NER inference + FFmpeg redaction
- **No model swapping delays**: Each engine_id gets its own GPU, models stay loaded

### GPU assignment

Use `CUDA_VISIBLE_DEVICES` in a compose override to pin engine_ids to GPUs:

```yaml
# In docker-compose.aws-multigpu.yml (override)
services:
  stt-transcribe-nemo:
    environment:
      CUDA_VISIBLE_DEVICES: "0"

  stt-transcribe-faster-whisper:
    environment:
      CUDA_VISIBLE_DEVICES: "0"

  stt-rt-transcribe-parakeet-rnnt-0.6b:
    environment:
      CUDA_VISIBLE_DEVICES: "1"

  stt-diarize-nemo-msdd:
    environment:
      CUDA_VISIBLE_DEVICES: "1"

  stt-transcribe-vllm-asr:
    environment:
      CUDA_VISIBLE_DEVICES: "2"

  stt-align-phoneme:
    environment:
      CUDA_VISIBLE_DEVICES: "3"

  stt-pii-detect-presidio-gpu:
    environment:
      CUDA_VISIBLE_DEVICES: "3"
```

### Cost

| State | Monthly |
|-------|---------|
| Running 24/7 | ~$1,500 |
| Running 8h/day weekdays | ~$500 |
| Stopped | ~$10 |

This is serious money. Only justified for production workloads with throughput requirements.

---

## Scenario 5: Split CPU/GPU (Two EC2 Instances)

**For**: Keep control plane + CPU engines running cheaply 24/7, only pay for GPU when processing.

```
┌─────────────────────────────────────────┐     ┌──────────────────────────────────────┐
│  EC2: t3.medium (2 vCPU, 4 GB RAM)     │     │  EC2: g5.xlarge (4 vCPU, 16 GB RAM)  │
│  "Control Plane" — runs 24/7            │     │  "GPU Worker" — runs on demand / spot │
│                                         │     │                                      │
│  ┌────────────┐  ┌──────────────┐       │     │  GPU (24 GB VRAM):                   │
│  │  Gateway   │  │ Orchestrator │       │     │  ┌─────────────────────────────────┐  │
│  └────────────┘  └──────────────┘       │     │  │ nemo (parakeet-tdt-1.1b)       │  │
│  ┌────────────┐  ┌──────────────┐       │     │  └─────────────────────────────────┘  │
│  │   Redis    │  │   Postgres   │       │     │  ┌─────────────────────────────────┐  │
│  └────────────┘  └──────────────┘       │     │  │ faster-whisper (lg-v3-turbo)    │  │
│                                         │     │  └─────────────────────────────────┘  │
│  CPU engines:                           │     │  ┌─────────────────────────────────┐  │
│  ┌──────────┐  ┌─────────┐              │     │  │ phoneme-align (GPU)             │  │
│  │ prepare  │  │  merge  │              │     │  └─────────────────────────────────┘  │
│  └──────────┘  └─────────┘              │     │  ┌─────────────────────────────────┐  │
│  ┌─────────────┐  ┌───────────────────┐ │     │  │ pyannote-4.0 / nemo-msdd       │  │
│  │ onnx   │  │ pii-presidio      │ │     │  └─────────────────────────────────┘  │
│  │ (CPU batch) │  │ (CPU detection)   │ │     │  ┌─────────────────────────────────┐  │
│  └─────────────┘  └───────────────────┘ │     │  │ RT parakeet-rnnt (streaming)    │  │
│  ┌───────────────────┐                  │     │  └─────────────────────────────────┘  │
│  │ audio-redactor    │                  │     │                                      │
│  └───────────────────┘                  │     │  REDIS_URL=redis://<ctrl-plane>:6379 │
│                                         │     │                                      │
│  + Tailscale                            │     │  + Tailscale                          │
└─────────────────────────────────────────┘     └──────────────────────────────────────┘
         ▲                                                    ▲
         └──────────── Same VPC / Tailscale network ──────────┘
```

### Why split

The control plane (Gateway, Orchestrator, Redis, Postgres) and CPU engines (prepare, merge, PII, audio-redact) need to be always-on to accept API requests and manage jobs. But they barely need any compute. Meanwhile the GPU is expensive and only needed during actual transcription/diarization/alignment.

**Align and diarize belong on the GPU instance.** Both are 15x faster on GPU:

| Stage | GPU (T4/A10G) | CPU | Speedup |
|-------|---------------|-----|---------|
| phoneme-align | RTF 0.02 (~12s for 10-min file) | RTF 0.3 (~3 min) | 15x |
| pyannote-4.0 | RTF 0.08 (~48s for 10-min file) | RTF 1.2 (~12 min) | 15x |
| nemo-msdd | RTF 0.05 (~30s for 10-min file) | RTF 2.0 (~20 min) | 40x |

Running diarize on CPU means a 1-hour audio file takes **72 minutes** to diarize. On the GPU that's already there, it takes **5 minutes**. Same story for alignment. Since you're paying for the GPU anyway, there's no cost benefit to running these stages on CPU — only a massive performance penalty.

Splitting lets you:

- **Run the control plane 24/7 on a ~$30/month t3.medium** — Gateway accepts uploads, Orchestrator queues tasks
- **Start/stop the GPU instance on demand** — or use spot ($0.35/hr vs $1.01/hr)
- **All model-heavy stages on GPU** — transcribe, align, diarize run where they're 15-40x faster
- **CPU-side processing continues while GPU is off** — prepare, PII detection, audio-redact, merge, and onnx transcription (English, ~8x realtime) all run on the control plane
- **Queue buffering** — if the GPU is off, tasks sit in Redis queues. Start the GPU instance, engines drain the backlog automatically

### How it works

All Dalston engines connect to Redis directly via `REDIS_URL`. Put Redis on the control plane, point GPU engines to it:

```bash
# On the control plane (t3.medium)
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-prepare stt-merge \
  stt-pii-detect-presidio \
  stt-audio-redact-audio \
  stt-transcribe-onnx

# On the GPU instance (g5.xlarge)
# .env.gpu points REDIS_URL and DATABASE_URL to the control plane
REDIS_URL=redis://10.0.1.10:6379 \
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.gpu --profile gpu up -d \
  stt-transcribe-nemo \
  stt-transcribe-faster-whisper \
  stt-align-phoneme \
  stt-diarize-nemo-msdd \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

The GPU engines register themselves in Redis via heartbeat. The orchestrator sees them and routes tasks. When the GPU instance stops, heartbeats expire after 60s, stale tasks get reclaimed and re-queued.

### Network setup

Two options:

1. **Same VPC, private subnet**: Both instances in the same VPC. Security group allows Redis port (6379) between them. Lowest latency (~0.1ms).

2. **Tailscale mesh**: Both instances join the Tailscale network. GPU engines reach Redis at `redis://ctrl-plane:6379`. Works across AZs, regions, or even with a local dev machine as the GPU worker. Simpler than VPC peering.

### Cost

| Component | Monthly (GPU 8h/day) | Monthly (GPU spot 8h/day) | Monthly (GPU off) |
|-----------|---------------------|--------------------------|-------------------|
| t3.medium (24/7 control plane) | ~$30 | ~$30 | ~$30 |
| g5.xlarge (GPU worker) | ~$165 | ~$57 | ~$6 (EBS only) |
| **Total** | **~$195** | **~$87** | **~$36** |

Compare to Scenario 2 (single g5.xlarge 24/7): ~$300/month. The split saves money because the control plane doesn't need GPU pricing, and the GPU instance can truly stop when idle.

### When to use this vs Scenario 2

| | Scenario 2 (single box) | Scenario 5 (split) |
|---|---|---|
| Simplicity | One machine, one compose | Two machines, two composes |
| Always-on API | Pays GPU rate 24/7 | Pays CPU rate for API, GPU on demand |
| Realtime | Always available | RT only when GPU is running |
| Batch queueing | Immediate processing | Tasks queue until GPU starts |
| Cost (8h/day workday) | ~$100 (spot ~$35) | ~$87 (spot) |
| Cost (sporadic use) | ~$300 or stop entirely | ~$36 base + GPU hours |

**Best for**: Workloads where you receive uploads throughout the day but only process in batches — the control plane accepts and queues, you start the GPU for processing runs. Also good when you want the API always available even without GPU.

### Scaling up

The split pattern extends naturally:

- **Multiple GPU workers**: Launch 2-3 spot g5.xlarge instances, all pointing at the same Redis. Tasks distribute automatically via consumer groups. Drain the queue faster.
- **Heterogeneous workers**: One g5.xlarge for transcription, one focused on align+diarize. Each engine type on the right hardware.
- **Smaller GPU for align+diarize only**: If onnx handles transcription on CPU fast enough, the GPU worker only runs align + diarize engines. A smaller g4dn.xlarge (~$0.53/hr) is sufficient — align needs ~2 GB VRAM and diarize needs 2-4 GB.

---

## Scenario 6: Auto-Scaling Architecture (ECS/Fargate + GPU instances)

**For**: Production with auto-scaling, cost optimization, team use.

```
┌─────────────────────────────────────────────────────────────────┐
│  AWS VPC                                                        │
│                                                                 │
│  ECS Fargate (CPU, auto-scaling):                              │
│  ┌────────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────┐  │
│  │  Gateway   │  │ Orchestrator │  │ prepare  │  │  merge  │  │
│  │  (2 tasks) │  │  (1 task)    │  │ (1 task) │  │ (1 task)│  │
│  └────────────┘  └──────────────┘  └──────────┘  └─────────┘  │
│                                                                 │
│  ECS on EC2 (GPU, capacity provider):                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  g5.xlarge ASG (0-2 instances, scale on queue depth)    │   │
│  │  ┌───────────────────────┐  ┌────────────────────────┐  │   │
│  │  │ nemo transcribe      │  │ faster-whisper         │  │   │
│  │  └───────────────────────┘  └────────────────────────┘  │   │
│  │  ┌───────────────────────┐  ┌────────────────────────┐  │   │
│  │  │ pyannote diarize     │  │ phoneme align          │  │   │
│  │  └───────────────────────┘  └────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Managed Services:                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ ElastiCache  │  │     RDS      │  │         S3           │  │
│  │ (Redis)      │  │  (Postgres)  │  │  (artifacts)         │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ALB → Gateway (HTTPS)                                         │
└─────────────────────────────────────────────────────────────────┘
```

### Why split

- **Scale to zero**: GPU instances stop when queue is empty — no idle GPU cost
- **Independent scaling**: Gateway scales on request rate, engines on queue depth
- **Managed data stores**: No Postgres/Redis maintenance
- **HTTPS + auth**: ALB with ACM certificate, proper endpoint

### Cost (varies with usage)

| Component | Monthly (low use) | Monthly (heavy use) |
|-----------|-------------------|---------------------|
| Fargate (gateway + orchestrator + CPU engines) | ~$50 | ~$100 |
| g5.xlarge on-demand (when processing) | ~$0-150 | ~$300 |
| ElastiCache (cache.t3.micro) | ~$15 | ~$15 |
| RDS (db.t3.micro) | ~$15 | ~$30 |
| S3 | ~$1 | ~$5 |
| ALB | ~$20 | ~$25 |
| **Total** | **~$100** | **~$475** |

Significant infrastructure complexity increase. Only worth it when you need multi-user access or scale-to-zero economics.

---

## Recommendation

The right scenario depends on whether you need realtime streaming, diarization, and for which languages.

### Batch-only: Scenario 2 (`g5.xlarge`)

If you primarily do batch transcription with occasional realtime English streaming:

**`g5.xlarge`** is the sweet spot. Here's why:

1. **All batch engine_ids + English RT fit in 24 GB VRAM** — nemo + faster-whisper + diarization + parakeet-rnnt-0.6b with 8 GB headroom
2. **Engine selector auto-routing** — submit a job and the orchestrator picks the best downloaded model. English → nemo (parakeet-tdt-1.1b), non-English → faster-whisper. Or pin a specific model per request.
3. **Model registry** — `curl -X POST .../models/{id}/pull` downloads any compatible model from HuggingFace without rebuilding images
4. **~$100/month** at 8h/day usage (~$35 with spot)
5. **Upgrade path is clear**: bump to g5.2xlarge (`dalston-aws setup --instance-type g5.2xlarge`) or add vllm-asr for audio LLMs on g5.12xlarge

### Multilingual realtime: Scenario 2 still works

If you need multilingual realtime streaming, add faster-whisper RT (~6 GB VRAM). Total VRAM for batch + multilingual RT: ~20 GB of 24 GB. Tight but it fits. The faster-whisper RT engine supports distil-whisper for low-latency and large-v3 for accuracy.

### LLM-class realtime: Scenario 3 or 4

If you want voxtral Mini 4B for 13-language LLM streaming (~16 GB VRAM), Scenario 2's VRAM budget gets too tight for batch + RT simultaneously. Options:

- **Scenario 3** (g5.2xlarge): Same GPU, more CPU/RAM. voxtral RT fits alongside one batch engine_id but not all.
- **Scenario 4** (g5.12xlarge): Dedicated GPU per function. voxtral RT gets its own A10G. Overkill unless you also need parallel pipeline.

### Diarization: GPU strongly recommended

Diarization on CPU is painfully slow (10-20 min for a 10-min file). On GPU it takes seconds. If diarization is part of your standard pipeline:

- **English**: Scenario 2 with `nemo + pyannote` — two stages, ~8 GB VRAM, fastest pipeline (no alignment needed)
- **Multilingual**: Scenario 2 with `faster-whisper + phoneme-align + pyannote` — three stages, ~8 GB VRAM, alignment adds seconds on GPU
- **Occasional diarization on CPU**: Scenario 1 works but expect 12-22 min per 10-min file. Acceptable for low volume.

See [Diarization Pipeline](#diarization-pipeline) for full details.

### Always-on API with GPU on demand: Scenario 5

If your workload is sporadic — uploads come in throughout the day but processing can batch — the split CPU/GPU architecture is cost-effective:

- Control plane on t3.medium (~$30/mo) keeps API, Redis, and CPU engines always available
- GPU worker (g5.xlarge spot) starts when you have a queue to drain
- All model-heavy stages (transcribe, align, diarize) run on GPU where they're 15-40x faster
- Align and diarize tasks queue until the GPU starts — don't bother running them on CPU (too slow)
- For light English-only work, onnx on the control plane handles transcription without GPU

### English-only on a budget: Scenario 1 (`t3.xlarge`)

If it's only English and batch latency is acceptable:

- onnx with parakeet-tdt-0.6b-v3 transcribes at ~8x realtime on CPU with punctuation — a 10-minute file takes ~75 seconds
- parakeet-onnx RT handles single-session English realtime on CPU (VAD-chunked)
- ~$35/month. No GPU needed.

This is surprisingly capable for low-volume English workloads.

### Concrete config (Scenario 2)

```bash
dalston-aws setup --instance-type g5.xlarge --spot
```

Start these services:

```bash
# English-primary with multilingual fallback
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-prepare \
  stt-transcribe-nemo \
  stt-transcribe-faster-whisper \
  stt-align-phoneme \
  stt-diarize-nemo-msdd \
  stt-merge \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

Pre-pull models so the first transcription doesn't wait for download:

```bash
# Via the model registry API
curl -X POST http://localhost:8000/v1/models/nvidia/parakeet-tdt-1.1b/pull
curl -X POST http://localhost:8000/v1/models/Systran/faster-distil-whisper-large-v3/pull

# Check download status
curl http://localhost:8000/v1/models | jq '.models[] | {id, status, size_bytes}'
```

The model registry tracks download state (`not_downloaded` → `downloading` → `ready`) and the engine selector only routes to models with status `ready`.

---

## Diarization Pipeline

Adding speaker diarization (`speaker_detection: "diarize"`) changes the pipeline shape and resource requirements significantly depending on which transcription engine you use.

### Pipeline shape depends on the transcriber

The orchestrator's DAG builder (`dag.py`) automatically determines whether the alignment stage is needed:

```
Parakeet (nemo/onnx):  prepare → transcribe → diarize → merge
                            (native word timestamps — alignment skipped)

faster-whisper:             prepare → transcribe → align → diarize → merge
                            (no native word timestamps — alignment required)

vllm-asr:                   prepare → transcribe → align → diarize → merge
                            (text only — alignment required)
```

Parakeet models produce native word-level timestamps, so the `phoneme-align` stage is skipped entirely. faster-whisper and vllm-asr don't produce word timestamps, so `phoneme-align` must run first to give the diarizer word-level timing for speaker assignment.

### Stage resource requirements (from catalog)

| Stage | Engine | VRAM (GPU) | RTF (GPU) | RTF (CPU) | RAM |
|-------|--------|-----------|-----------|-----------|-----|
| align | phoneme-align | 2 GB | 0.02 | 0.3 | 4 GB |
| diarize | pyannote-4.0 | 2 GB | 0.08 | 1.2 | 6 GB |
| diarize | nemo-msdd | 4 GB | 0.05 | 2.0 | 8 GB |

### Impact on GPU VRAM budget

**English with Parakeet + diarization** (best case — no alignment needed):

| Component | VRAM |
|-----------|------|
| nemo (parakeet-tdt-1.1b) | ~6 GB |
| pyannote-4.0 | ~2 GB |
| **Total** | **~8 GB** |

Fits easily on 24 GB. Leaves 14+ GB for realtime engines or other models.

**Multilingual with faster-whisper + diarization** (alignment required):

| Component | VRAM |
|-----------|------|
| faster-whisper (large-v3-turbo) | ~4 GB |
| phoneme-align | ~2 GB |
| pyannote-4.0 | ~2 GB |
| **Total** | **~8 GB** |

Also fits on 24 GB. The alignment stage adds ~2 GB VRAM and processing time (RTF 0.02 on GPU — negligible).

**Combined English + multilingual + diarization** (both transcribers loaded):

| Component | VRAM |
|-----------|------|
| nemo (parakeet-tdt-1.1b) | ~6 GB |
| faster-whisper (large-v3-turbo) | ~4 GB |
| phoneme-align | ~2 GB |
| pyannote-4.0 | ~2 GB |
| **Total** | **~14 GB** |

Fits on 24 GB with ~8 GB headroom — room for a realtime engine too.

### Impact on processing time

For a 10-minute audio file:

| Pipeline | GPU time | CPU time |
|----------|----------|----------|
| Parakeet → diarize (pyannote) | ~5s | ~14 min |
| Parakeet → diarize (nemo-msdd) | ~3s | ~22 min |
| faster-whisper → align → diarize (pyannote) | ~7s | ~18 min |
| faster-whisper → align → diarize (nemo-msdd) | ~5s | ~26 min |

On GPU, diarization adds seconds. On CPU, it adds **minutes** — pyannote runs at 1.2x realtime (10-min file takes 12 min to diarize), nemo-msdd at 2.0x realtime (20 min). This is the main reason **diarization pushes you toward GPU**.

### How this changes the recommendation

| Scenario | Diarization viability |
|----------|----------------------|
| **1 (CPU-only)** | Technically works but painfully slow. A 10-min file takes 12-22 min just for diarization. Only acceptable for occasional, non-urgent use. |
| **2 (g5.xlarge)** | Sweet spot. Both diarizers fit in VRAM alongside transcription. Processing time is seconds, not minutes. |
| **3 (g5.2xlarge)** | Same GPU — no VRAM benefit. Extra CPU helps if running align on CPU while GPU diarizes. |
| **4 (g5.12xlarge)** | Diarization gets its own GPU. Transcribe + diarize run in parallel on different files. |
| **5 (Split CPU/GPU)** | Run align + diarize on GPU worker (15x faster). If GPU is off, tasks queue in Redis until the GPU starts — don't bother with CPU diarize/align. |

**Bottom line**: If you need diarization on every job, you need GPU (Scenario 2+). If diarization is occasional, CPU works but plan for 10-20 min per file.

### Choosing a diarizer

| | pyannote-4.0 | nemo-msdd |
|---|---|---|
| License | Gated (needs HF_TOKEN) | Open CC-BY-4.0 |
| VRAM | 2 GB | 4 GB |
| GPU speed | RTF 0.08 | RTF 0.05 (faster) |
| CPU speed | RTF 1.2 (faster) | RTF 2.0 |
| Overlap detection | Via VBx clustering | Built-in neural MSDD |
| Speaker counting | Good | Good |

**pyannote-4.0**: Smaller VRAM, faster on CPU, battle-tested. Requires `HF_TOKEN` for gated model access.

**nemo-msdd**: Slightly faster on GPU, better overlap detection, fully open license. Uses more VRAM (4 GB vs 2 GB).

For Scenario 5 (split), diarization runs on the GPU worker — so pick based on GPU performance and license. nemo-msdd's speed (RTF 0.05) and open CC-BY-4.0 license make it the simpler choice. pyannote is battle-tested but requires `HF_TOKEN` for gated model access.

### Whisper + diarization: the alignment tax

When using faster-whisper or vllm-asr with diarization, the `phoneme-align` stage is automatically inserted. This is the "alignment tax":

- **On GPU**: Negligible. RTF 0.02 — a 10-min file aligns in ~12 seconds. 2 GB VRAM.
- **On CPU**: Noticeable but tolerable. RTF 0.3 — a 10-min file takes ~3 minutes. The alignment model (wav2vec2, ~1.2 GB) fits in RAM.

The alignment tax matters most on CPU. On GPU, it's invisible. This is another reason diarization workflows benefit from GPU — both the diarizer and the aligner run fast.

**Parakeet avoids this entirely** — native word timestamps mean no alignment stage. For English diarization, `nemo + pyannote` is the most efficient pipeline: two stages instead of three, less VRAM, less processing time.

---

## Model Cache Sizing

The `/data/models` volume needs enough space for downloaded weights. Sizes from the model catalog:

**Transcription models (nemo engine_id — full NeMo toolkit)**:

| Model | Size | Notes |
|-------|------|-------|
| nvidia/parakeet-tdt-1.1b | 4.2 GB | Best English accuracy |
| nvidia/parakeet-ctc-1.1b | 4.2 GB | Fast CTC decoder, no punctuation |
| nvidia/parakeet-tdt-0.6b-v3 | 1.8 GB | Punctuation + capitalization |
| nvidia/parakeet-ctc-0.6b | 1.8 GB | Lightest NeMo model |

**Transcription models (onnx engine_id — lightweight ONNX)**:

| Model | Size | Notes |
|-------|------|-------|
| parakeet-onnx-tdt-0.6b-v3 | 0.6 GB | Best for CPU, has punctuation |
| parakeet-onnx-ctc-0.6b | 0.6 GB | Fastest CPU inference |
| parakeet-onnx-rnnt-0.6b | 0.6 GB | RNNT decoder |

**Transcription models (faster-whisper engine_id)**:

| Model | Size | Notes |
|-------|------|-------|
| Systran/faster-distil-whisper-large-v3 | 1.6 GB | Default multilingual |
| Systran/faster-whisper-large-v3 | 3.0 GB | Best Whisper accuracy |
| Systran/faster-whisper-medium | 1.5 GB | Good mid-range option |
| Systran/faster-whisper-small | 0.5 GB | Resource-constrained |
| Systran/faster-whisper-base | 0.3 GB | Testing / low resource |
| Systran/faster-whisper-tiny | 0.1 GB | Minimal |

**Audio LLM models (vllm-asr engine_id)**:

| Model | Size | Notes |
|-------|------|-------|
| mistralai/Voxtral-Mini-3B-2507 | 6.0 GB | Best speed/accuracy |
| Qwen/Qwen2-Audio-7B-Instruct | 15.0 GB | Best CJK support |
| mistralai/Voxtral-Small-24B-2507 | 48.0 GB | Needs multi-GPU |

**Other**:

| Model | Size | Notes |
|-------|------|-------|
| pyannote 4.0 | ~0.3 GB | Diarization (needs HF_TOKEN) |
| nemo-msdd | ~0.5 GB | Diarization (open license) |
| phoneme-align | ~1.2 GB | Word alignment (wav2vec2) |

**Typical setups**:

| Setup | Models | Total |
|-------|--------|-------|
| CPU-only (EN) | onnx tdt-0.6b-v3 + faster-whisper lg-v3-turbo | ~2.2 GB |
| Scenario 2 (EN + multi) | parakeet-tdt-1.1b + fw lg-v3-turbo + pyannote + align | ~9.3 GB |
| Scenario 4 (everything) | All above + Voxtral Mini 3B | ~15 GB |

50 GB data volume is sufficient for any single-GPU setup. 100 GB gives room for experimentation with multiple model variants and audio LLMs.

---

## Upgrade Path

```
Scenario 1 (CPU)          → EN batch + basic RT, ~$35/mo
    ↓ change instance_type
Scenario 2 (g5.xlarge)    → Full batch + EN RT + diarization, ~$100/mo (~$35 spot)
    ↓ change instance_type
Scenario 3 (g5.2xlarge)   → + voxtral RT or heavy concurrent load, ~$150/mo
    ↓ change instance type
Scenario 4 (g5.12xlarge)  → Parallel pipeline, audio LLMs, multi-lang RT, ~$500/mo
    ↓ split into two instances
Scenario 5 (CPU + GPU)    → Always-on API + GPU on demand, ~$87/mo (spot)
    ↓ architecture change
Scenario 6 (ECS split)    → Auto-scaling, managed services, production
```

Each step is additive. Scenarios 1→3 are a `dalston-aws setup` with a different `--instance-type`. Scenario 4 needs a compose override for GPU pinning. Scenario 5 is a second EC2 instance. Scenario 6 uses ECS for auto-scaling.

**Decision axes**:

- Batch only, English? → Scenario 1 (CPU) is fine
- Batch + English RT? → Scenario 2
- Multilingual RT or voxtral RT? → Scenario 2-3
- Parallel pipeline + audio LLMs? → Scenario 4
- Always-on API, GPU only when processing? → Scenario 5
- Multi-user, scale-to-zero? → Scenario 6

---

## Observability

Every Dalston service emits structured logs, Prometheus metrics, and (optionally) OpenTelemetry traces out of the box. The question is where to collect and view them.

### What's built in (no extra services needed)

Every service already provides:

- **Structured JSON logs** via `structlog` — correlation IDs (job_id, task_id, request_id) propagate across Gateway → Orchestrator → Engines. Configure with `DALSTON_LOG_LEVEL` and `DALSTON_LOG_FORMAT`.
- **Prometheus `/metrics` endpoint** on every service — Gateway (:8000), Orchestrator (:8001), all engines (:9100). Key metrics include request latency, job duration, task processing time, queue wait time, S3 I/O, and engine redeliveries.
- **Health endpoints** — `GET /health` on Gateway and engines.
- **Web console** — built-in React dashboard at `/console` with 24h throughput chart, per-engine performance table, success rates, and queue depths. No extra setup.
- **CLI tools** — `make health`, `make status`, `make queues` for quick checks.

### Built-in monitoring stack (add `--profile observability`)

The `observability` compose profile adds Prometheus, Grafana, Jaeger, and a queue metrics exporter. Zero configuration needed — scrape targets and Grafana dashboards are pre-provisioned.

```
┌──────────────────────────────────────────────────┐
│  Observability stack (--profile observability)    │
│                                                  │
│  ┌──────────────┐   ┌───────────────────────┐    │
│  │  Prometheus   │──▶│  Grafana (:3001)      │    │
│  │  (:9090)      │   │  dalston-overview.json │   │
│  └──────┬───────┘   └───────────────────────┘    │
│         │ scrapes every 15s                      │
│         ├── gateway:8000/metrics                 │
│         ├── orchestrator:8001/metrics            │
│         ├── metrics-exporter:9100/metrics        │
│         ├── stt-*:9100/metrics             │
│         └── stt-rt-*:9100/metrics                │
│                                                  │
│  ┌──────────────┐                                │
│  │  Jaeger       │  OTLP traces (when enabled)   │
│  │  (:16686 UI)  │  ← gateway, orchestrator      │
│  │  (:4317 gRPC) │  ← engines                    │
│  └──────────────┘                                │
│                                                  │
│  ┌──────────────┐                                │
│  │ Metrics      │  Queue depth + oldest task age  │
│  │ Exporter     │  from Redis stream metadata     │
│  │ (:9100)      │                                │
│  └──────────────┘                                │
└──────────────────────────────────────────────────┘
```

#### How to enable

```bash
# Local dev
make dev-observability

# AWS (add the profile to your compose command)
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra --profile observability up -d \
  gateway orchestrator ...engines...
```

Then access:

- **Grafana**: `http://<host>:3001` (admin/dalston, anonymous viewer enabled)
- **Prometheus**: `http://<host>:9090`
- **Jaeger**: `http://<host>:16686` (requires `OTEL_ENABLED=true`)

Via Tailscale, these are accessible as `http://dalston:3001`, etc.

#### Grafana dashboard

The pre-provisioned `dalston-overview.json` dashboard shows:

- Job throughput (completed/failed per hour)
- Task processing duration by engine (avg, p95, p99)
- Queue depth per stage (transcribe, align, diarize, merge)
- Oldest waiting task age (spot potential stale tasks)
- Engine health (heartbeat status, loaded models)
- Gateway request rate and latency
- S3 upload/download times
- WebSocket connection count (realtime sessions)

#### Key Prometheus metrics

| Metric | Type | What it tells you |
|--------|------|-------------------|
| `dalston_gateway_request_duration_seconds` | Histogram | API latency by endpoint |
| `dalston_orchestrator_job_duration_seconds` | Histogram | Total job time including all stages |
| `dalston_engine_task_duration_seconds` | Histogram | Per-engine processing time |
| `dalston_engine_queue_wait_seconds` | Histogram | Time tasks sit in queue before pickup |
| `dalston_engine_task_redelivery_total` | Counter | Crash recovery events (spot interruptions show up here) |
| `dalston_queue_depth` | Gauge | Tasks waiting per engine stream |
| `dalston_queue_oldest_task_age_seconds` | Gauge | Oldest unprocessed task (alerts on stale tasks) |
| `dalston_orchestrator_tasks_timed_out_total` | Counter | Timeout failures |
| `dalston_session_router_sessions_active` | Gauge | Active realtime sessions per worker |

#### OpenTelemetry tracing

Distributed tracing is off by default (zero overhead). Enable it to trace requests across services:

```bash
# In .env.aws or docker-compose override
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
```

Traces show the full path: API request → job creation → task scheduling → engine processing → S3 upload → completion event. Useful for debugging slow jobs or understanding pipeline bottlenecks.

### Per-scenario recommendations

#### Scenario 1-3, 5: Built-in stack is sufficient

The `observability` compose profile runs Prometheus, Grafana, and Jaeger on the same instance alongside your engines. Resource overhead is minimal:

| Component | RAM | CPU |
|-----------|-----|-----|
| Prometheus (1-day retention, 500 MB) | ~200 MB | Negligible |
| Grafana | ~100 MB | Negligible |
| Jaeger (in-memory) | ~100 MB | Negligible |
| Metrics exporter | ~50 MB | Negligible |
| **Total** | **~450 MB** | |

On Scenario 1 (t3.xlarge, 16 GB), this fits within the ~7 GB headroom. On Scenario 2-3 with GPU, it's trivial.

**Retention**: Default Prometheus retention is 1 day / 500 MB. For longer history on a single instance, increase in the compose override:

```yaml
# docker-compose.override.yml
services:
  prometheus:
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.enable-lifecycle'
      - '--storage.tsdb.retention.time=30d'
      - '--storage.tsdb.retention.size=2GB'
    volumes:
      - /data/prometheus:/prometheus  # Persist to data volume
```

#### Scenario 4, 6: Consider managed services

For multi-GPU or split architecture, co-located monitoring has limitations:

- **Prometheus retention**: 30 days of metrics from 4 GPUs + many engines fills up fast. Consider shipping to a remote backend:
  - **Amazon Managed Prometheus (AMP)**: Drop-in Prometheus remote write. ~$0.30/million samples ingested + $0.03/million queries. Add `remote_write` to prometheus.yml.
  - **Grafana Cloud free tier**: 10k series, 14-day retention. Sufficient for a single Dalston deployment.

- **Log aggregation**: JSON logs from `docker compose logs` are fine for debugging, but for multi-instance deployments, ship to CloudWatch Logs or Loki:
  - **CloudWatch**: Add the `awslogs` log driver to compose services. No extra containers. ~$0.50/GB ingested.
  - **Loki**: Add a Loki container to the observability profile. Grafana already supports it as a datasource. Light on resources.

- **Tracing**: Jaeger all-in-one with in-memory storage loses traces on restart. For production:
  - **AWS X-Ray**: OpenTelemetry exporter supports X-Ray natively. No extra infrastructure.
  - **Jaeger with Elasticsearch backend**: Persistent trace storage.
  - **Grafana Tempo**: OTLP-compatible, integrates with Grafana.

#### Tailscale access

All monitoring UIs are accessible over Tailscale without exposing ports to the internet:

```
http://dalston:3001  → Grafana
http://dalston:9090  → Prometheus
http://dalston:16686 → Jaeger
http://dalston:8000  → Gateway (includes /console web UI)
```

No ALB, no HTTPS certificates, no security groups to manage. The Tailscale ACL controls who can access what.

### Quick start checklist

1. Add `--profile observability` to your compose command
2. Open Grafana at `:3001` — the dalston-overview dashboard loads automatically
3. Submit a test job and watch metrics flow
4. Optionally enable tracing: `OTEL_ENABLED=true` in your env file
5. For alerting: add Prometheus alerting rules and configure a notification channel in Grafana

---

## Spot Instances

Spot saves 60-70% on GPU instances. Dalston's architecture is already built for it.

### Why it works

The engine SDK was designed assuming engines can die at any time:

1. **Unique instance IDs**: Each engine startup generates `{engine_id}-{uuid4[:12]}` (`runner.py:124`). A spot replacement is a fresh instance — no identity collision with the terminated one.

2. **60-second heartbeat TTL**: Engine keys auto-expire in Redis if the engine stops heartbeating (`HEARTBEAT_TTL = 60`). A spot termination looks identical to a crash — the key expires, the engine disappears from the registry.

3. **Stale task recovery**: The `StaleTaskScanner` runs every 60s in the orchestrator. It checks the Redis Streams Pending Entries List (PEL) for tasks owned by dead engines (`is_engine_alive()` checks heartbeat key existence). Dead engine tasks are marked FAILED and re-queued automatically.

4. **Idempotent task processing**: Tasks download input from S3, process, upload output to S3. No local state that would be lost. A re-delivered task (up to `MAX_DELIVERIES = 3`) produces the same result.

5. **Model cache on EBS**: Model weights live on `/data/models` (EBS volume). EBS survives spot termination. When the replacement instance boots, models are already cached — no re-download delay.

### What spot termination looks like

```
t=0s    AWS sends 2-minute interruption notice
t=0s    Instance receives SIGTERM → Docker stops containers gracefully
t=0-5s  Engines deregister from Redis (or just stop heartbeating)
t=60s   Heartbeat TTL expires, engine keys vanish
t=60s   StaleTaskScanner detects orphaned tasks in PEL
t=60s   Tasks marked FAILED, orchestrator retries them
t=120s  Spot instance terminated
t=?     Replacement instance launches (if using ASG/Fleet)
t=?+60s New engine containers register, pull tasks from queue
```

The worst case: a task that was 90% done gets terminated and must restart from scratch. For a typical 10-minute audio file with GPU transcription (RTF 0.03), that's ~18 seconds of wasted compute. Negligible.

### Cost savings

| Instance | On-Demand | Spot (typical) | Savings |
|----------|-----------|----------------|---------|
| g5.xlarge | $1.006/hr | ~$0.35/hr | ~65% |
| g5.2xlarge | $1.512/hr | ~$0.50/hr | ~67% |
| g5.12xlarge | $5.672/hr | ~$1.90/hr | ~66% |

For Scenario 2 at 8h/day weekdays:

| | On-Demand | Spot |
|---|-----------|------|
| Monthly | ~$100 | ~$35 |

That's `g5.xlarge` GPU transcription for the price of a `t3.xlarge` CPU instance.

### Spot interruption frequency

`g5.xlarge` in `eu-west-2` has historically low interruption rates (<5%). GPU instances generally have lower interruption rates than popular CPU instances because the spot pool is larger relative to demand.

You can further reduce interruptions by:

- Using **capacity-optimized** allocation strategy (picks the pool least likely to be interrupted)
- Allowing multiple instance types: `g5.xlarge`, `g5.2xlarge`, `g6.xlarge` — the fleet picks whichever has capacity
- Choosing availability zones with more capacity

### How to use spot instances

#### Option A: Simple — Spot on the single EC2 (Scenarios 1-3)

The `dalston-aws` script supports spot instances natively:

```bash
dalston-aws setup --instance-type g5.xlarge --spot
```

The script configures spot with `stop` interruption behavior (EBS preserved) and uses the on-demand price as the max bid. Internally, it sets up the EC2 launch with:

```
    dynamic "spot_options" {
      for_each = var.use_spot ? [1] : []
      content {
        spot_instance_type             = "persistent"
        instance_interruption_behavior = "stop"
        max_price                      = var.spot_max_price != "" ? var.spot_max_price : null
      }
    }
  }
}
```

Key detail: `instance_interruption_behavior = "stop"` means spot interruption **stops** the instance (like `dalston-down`) rather than terminating it. The EBS volumes, Tailscale IP, and all state are preserved. When spot capacity returns, AWS restarts it automatically. This is the simplest path — it behaves exactly like your manual start/stop workflow, just triggered by AWS pricing instead of your shell alias.

#### Option B: Spot Fleet with fallback (more robust)

For uninterrupted availability, use an EC2 Fleet that tries spot first, falls back to on-demand:

```hcl
resource "aws_ec2_fleet" "dalston" {
  type = "maintain"

  target_capacity_specification {
    default_target_capacity_type = "spot"
    total_target_capacity        = 1
    spot_target_capacity         = 1
    on_demand_target_capacity    = 0
  }

  spot_options {
    allocation_strategy = "capacity-optimized"

    # Fall back to on-demand if spot unavailable
    maintenance_strategies {
      capacity_rebalance {
        replacement_strategy = "launch-before-terminate"
      }
    }
  }

  launch_template_config {
    launch_template_specification {
      launch_template_id = aws_launch_template.dalston.id
      version            = "$Latest"
    }

    override {
      instance_type = "g5.xlarge"
    }
    override {
      instance_type = "g5.2xlarge"  # Fallback if g5.xlarge unavailable
    }
  }
}
```

This is more complex but provides near-zero downtime: when a spot interruption is predicted, AWS launches a replacement *before* terminating the old one. The new instance boots, Docker starts, engines register in Redis, and tasks resume flowing — usually within 2-3 minutes.

### What needs care

#### Realtime sessions

Spot termination kills active WebSocket connections. This is disruptive for realtime streaming. Two approaches:

1. **Accept it**: If you use realtime infrequently, a 2-minute interruption every few days is tolerable. The client reconnects and starts a new session.

2. **Hybrid spot/on-demand** (Scenario 5): Run gateway + realtime workers on a small on-demand CPU instance, batch/GPU engines on spot. Realtime sessions survive interruptions; batch tasks auto-retry. This is exactly the split CPU/GPU pattern.

For your use case (primarily batch with occasional realtime), approach 1 is fine.

#### Model loading after restart

If using `instance_interruption_behavior = "stop"` (Option A), the EBS volume persists. Model cache is intact. Boot → Docker starts → engines load cached models → ready in ~60-90 seconds.

If using fleet replacement (Option B), the new instance needs the EBS volume. Options:

- **Shared EBS**: Not possible across AZs. Only works if fleet is pinned to one AZ.
- **EFS for model cache**: Mount an EFS volume at `/data/models`. Slightly slower than EBS but survives instance replacement. ~$0.30/GB/month for infrequent access tier.
- **S3 model storage**: Already implemented in `S3ModelStorage`. Engines download from S3 on first use. Adds 1-5 minutes cold start for model download, but subsequent tasks use the local cache.

For Option A (the recommended path), this isn't an issue — EBS stays attached.

### Recommendation

**Start with Option A**: Run `dalston-aws setup --spot`. That's it.

- Same `dalston-aws` workflow, same Docker Compose, same `dalston-up`/`dalston-down`
- Instance stops on interruption, restarts when capacity returns
- 65% cost savings on the GPU instance
- EBS and Tailscale IP preserved
- Worst case: you're interrupted during a batch job, it auto-retries in ~2 minutes

If you find spot interruptions too frequent (unlikely for g5), re-run `dalston-aws setup` without `--spot` and you're back to on-demand. Zero architectural changes needed.
