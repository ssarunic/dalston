# AWS Deployment Scenarios

Deployment options for Dalston on AWS, ordered from simplest/cheapest to most capable. Each scenario builds on the previous one.

The runtime-based engine architecture loads models dynamically from HuggingFace — 12 runtimes, 18 cataloged models, and any HF-compatible model via `hf-asr`. The orchestrator's engine selector automatically picks the best runtime and downloaded model for each job based on language, capabilities, and hardware. The core question is: **how much GPU do you need, and which runtimes do you want running?**

---

## Quick Reference

| Scenario | Instance | GPU | Monthly (8h/day) | w/ Spot | Runtimes |
|----------|----------|-----|-------------------|---------|----------|
| 1. CPU-only | t3.xlarge | None | ~$35 | N/A | nemo-onnx (EN), faster-whisper (multi) |
| 2. Single GPU | g5.xlarge | 1x A10G 24GB | ~$100 | ~$35 | All batch + realtime + diarization |
| 3. Dual-purpose GPU | g5.2xlarge | 1x A10G 24GB | ~$150 | ~$50 | Higher throughput, concurrent batch+RT |
| 4. Multi-GPU | g5.12xlarge | 4x A10G 96GB | ~$500 | ~$170 | Full parallel pipeline + vllm-asr |
| 5. Split infra | ECS + g5 | Varies | ~$200+ | Mixed | Auto-scaling engines |

Spot instances save ~65% on GPU instances. See [Spot Instances](#spot-instances) below.

### Runtimes at a glance

Runtimes are inference engines that load models dynamically. Each runtime is a Docker container that can serve multiple models from the same family.

| Runtime | Stage | Languages | GPU required | Key models |
|---------|-------|-----------|-------------|------------|
| **nemo** | transcribe | EN | No (slow on CPU) | parakeet-tdt-1.1b, parakeet-ctc-0.6b |
| **nemo-onnx** | transcribe | EN | No | Same models, 12x smaller image, better CPU perf |
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

The engine selector automatically picks the best runtime for each pipeline stage based on job language, requested model, and what's currently running. See `dalston/orchestrator/engine_selector.py`.

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
│  │ nemo-onnx (parakeet-tdt-0.6b-v3)│ 4 GB   │
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

- **English**: nemo-onnx with parakeet-tdt-0.6b-v3 — native word timestamps, punctuation + capitalization, ~8x realtime on CPU (RTF 0.12). The ~1 GB container image starts in seconds vs ~12 GB for full NeMo.
- **Multilingual**: faster-whisper with large-v3-turbo — 99 languages, ~3.3x realtime on CPU (RTF 0.3)
- **No realtime streaming** (CPU too slow for streaming inference)
- Batch-only, sequential processing
- The engine selector auto-routes: English jobs → nemo-onnx, non-English → faster-whisper

**nemo-onnx model choices** (all English, all have native word timestamps):

| Model | RTF (CPU) | Punctuation | Size |
|-------|-----------|-------------|------|
| parakeet-tdt-0.6b-v3 | 0.12 | Yes | 0.6 GB |
| parakeet-ctc-0.6b | 0.15 | No | 0.6 GB |
| parakeet-ctc-1.1b | 0.20 | No | 1.2 GB |
| parakeet-rnnt-0.6b | 0.12 | No | 0.6 GB |

parakeet-tdt-0.6b-v3 is the best default — same speed as rnnt, better accuracy, and includes punctuation/capitalization so you skip the refine stage.

### How to deploy

Existing Terraform + `make aws-start` with the GPU profile disabled:

```bash
# In terraform.tfvars
instance_type = "t3.xlarge"

# On the instance — CPU-only, no --profile gpu
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-batch-prepare stt-batch-transcribe-nemo-onnx \
  stt-batch-transcribe-faster-whisper stt-batch-align-phoneme-cpu stt-batch-merge
```

### Memory budget (16 GB)

Only one transcription runtime loads at a time — the orchestrator routes tasks to the right runtime, and idle runtimes consume minimal memory until a task arrives.

| Component | RAM |
|-----------|-----|
| OS + Docker | ~1.5 GB |
| Redis + Postgres | ~1 GB |
| Gateway + Orchestrator | ~0.5 GB |
| nemo-onnx (parakeet-tdt-0.6b-v3) | ~4 GB |
| faster-whisper (large-v3-turbo, idle) | ~1 GB |
| prepare + align + merge | ~1 GB |
| **Headroom** | **~7 GB** |

When faster-whisper actively processes a task, it loads the model (~8 GB peak). The nemo-onnx runtime is lightweight enough that both can coexist, but for sustained mixed workloads consider running only one transcription runtime and relying on model swapping.

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
- **English realtime**: Parakeet RNNT 0.6B streaming — sub-200ms latency
- **Diarization**: pyannote 4.0 (RTF 0.08) or nemo-msdd (RTF 0.05, no HF_TOKEN needed)
- **Full pipeline**: prepare → transcribe → align → diarize → merge
- **Auto-routing**: The engine selector picks the best runtime per job — English → nemo, non-English → faster-whisper. Parakeet's native word timestamps skip the align stage entirely.

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

**If you only run English** (nemo for batch + RT parakeet + diarization): ~10-12 GB used, 12-14 GB free. Plenty of room to experiment with additional models.

**Lighter alternative**: Use nemo-onnx instead of full NeMo — same Parakeet models, ~2 GB VRAM, ~12x smaller container image. Slightly slower on GPU but negligible for single-file workloads. Frees VRAM for other runtimes.

### How to deploy

```bash
# In terraform.tfvars
instance_type = "g5.xlarge"

# On the instance — full GPU profile
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra --profile gpu up -d
```

Or cherry-pick runtimes:

```bash
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-batch-prepare stt-batch-merge \
  stt-batch-transcribe-nemo \
  stt-batch-transcribe-faster-whisper \
  stt-batch-align-phoneme \
  stt-batch-diarize-pyannote-4.0 \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

**Using nemo-msdd instead of pyannote** (no HF_TOKEN required, open CC-BY-4.0 license):

```bash
# Replace stt-batch-diarize-pyannote-4.0 with:
  stt-batch-diarize-nemo-msdd
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
4. **Model swapping** — both runtimes share the GPU. The nemo runtime loads parakeet-tdt-1.1b for one job, could load parakeet-ctc-0.6b for the next. No container restart needed.
5. **Two diarization options**: pyannote 4.0 (battle-tested, needs HF_TOKEN) or nemo-msdd (open license, built-in overlap detection)
6. Realtime streaming for live English transcription
7. One machine, one `terraform apply`, done

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
- Could add nemo-onnx as a secondary CPU transcription runtime alongside GPU nemo
- PII detection (pii-presidio) + audio redaction without RAM pressure

```bash
# In terraform.tfvars
instance_type = "g5.2xlarge"
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
│  │ RT parakeet-rnnt-0.6b   │  │ nemo-msdd (diarize)        │  │
│  └─────────────────────────┘  └────────────────────────────┘  │
│                                                               │
│  GPU 2 (24 GB): Audio LLM (vllm-asr)                        │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ vllm-asr (Voxtral-Mini-3B or Qwen2-Audio-7B)           │ │
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
- **Multiple realtime sessions**: Dedicated GPU for streaming, no batch contention
- **Audio LLMs via vllm-asr**: Highest-accuracy transcription for supported languages. Three model options:

  | Model | VRAM | Languages | RTF (GPU) | Notes |
  |-------|------|-----------|-----------|-------|
  | Voxtral Mini 3B | ~8 GB | en, es, fr, pt, hi, de, nl, it | 0.12 | Best speed/accuracy trade-off |
  | Qwen2-Audio 7B | ~16 GB | en, zh, ja, ko, fr, de, es, pt, it, nl, ru, ar | 0.18 | Best CJK language support |
  | Voxtral Small 24B | ~55 GB | en, es, fr, pt, hi, de, nl, it | 0.20 | Needs 3x A10G (multi-GPU) |

  Audio LLMs produce text only — no word timestamps. Chain with phoneme-align stage for timing.

- **nemo-msdd diarization**: Neural end-to-end system with built-in overlap detection, open CC-BY-4.0 license (no HF_TOKEN). Or use pyannote-4.0 if you prefer.
- **PII detection + audio redaction on GPU**: GLiNER NER inference + FFmpeg redaction
- **No model swapping delays**: Each runtime gets its own GPU, models stay loaded

### GPU assignment

Use `CUDA_VISIBLE_DEVICES` in a compose override to pin runtimes to GPUs:

```yaml
# In docker-compose.aws-multigpu.yml (override)
services:
  stt-batch-transcribe-nemo:
    environment:
      CUDA_VISIBLE_DEVICES: "0"

  stt-batch-transcribe-faster-whisper:
    environment:
      CUDA_VISIBLE_DEVICES: "0"

  stt-rt-transcribe-parakeet-rnnt-0.6b:
    environment:
      CUDA_VISIBLE_DEVICES: "1"

  stt-batch-diarize-nemo-msdd:
    environment:
      CUDA_VISIBLE_DEVICES: "1"

  stt-batch-transcribe-vllm-asr:
    environment:
      CUDA_VISIBLE_DEVICES: "2"

  stt-batch-align-phoneme:
    environment:
      CUDA_VISIBLE_DEVICES: "3"

  stt-batch-pii-detect-presidio-gpu:
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

## Scenario 5: Split Architecture (ECS/Fargate + GPU instances)

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

## Recommendation: Start with Scenario 2

For Parakeet for English + faster-whisper for other languages:

**`g5.xlarge`** is the right answer. Here's why:

1. **All runtimes fit in 24 GB VRAM** — nemo + faster-whisper + diarization + realtime with 8 GB headroom
2. **Engine selector auto-routing** — submit a job and the orchestrator picks the best downloaded model. English → nemo (parakeet-tdt-1.1b), non-English → faster-whisper. Or pin a specific model per request.
3. **`dalston model pull`** downloads any compatible model from HuggingFace without rebuilding images
4. **~$100/month** at 8h/day usage (~$35 with spot)
5. **Upgrade path is clear**: bump to g5.2xlarge (same Terraform, one variable) or add vllm-asr for audio LLMs on g5.12xlarge

### Concrete config

```hcl
# terraform.tfvars
instance_type    = "g5.xlarge"
data_volume_size = 50   # Sufficient for typical setup (~10 GB models)
use_spot         = true # ~65% savings, auto-retry on interruption
```

Start these services:

```bash
# English-primary with multilingual fallback
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-batch-prepare \
  stt-batch-transcribe-nemo \
  stt-batch-transcribe-faster-whisper \
  stt-batch-align-phoneme \
  stt-batch-diarize-nemo-msdd \
  stt-batch-merge \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

Pre-pull models so the first transcription doesn't wait for download:

```bash
# Via the model registry API
curl -X POST http://localhost:8000/v1/models/nvidia/parakeet-tdt-1.1b/pull
curl -X POST http://localhost:8000/v1/models/Systran/faster-whisper-large-v3-turbo/pull

# Check download status
curl http://localhost:8000/v1/models | jq '.models[] | {id, status, size_bytes}'
```

The model registry tracks download state (`not_downloaded` → `downloading` → `ready`) and the engine selector only routes to models with status `ready`.

---

## Model Cache Sizing

The `/data/models` volume needs enough space for downloaded weights. Sizes from the model catalog:

**Transcription models (nemo runtime — full NeMo toolkit)**:

| Model | Size | Notes |
|-------|------|-------|
| nvidia/parakeet-tdt-1.1b | 4.2 GB | Best English accuracy |
| nvidia/parakeet-ctc-1.1b | 4.2 GB | Fast CTC decoder, no punctuation |
| nvidia/parakeet-tdt-0.6b-v3 | 1.8 GB | Punctuation + capitalization |
| nvidia/parakeet-ctc-0.6b | 1.8 GB | Lightest NeMo model |

**Transcription models (nemo-onnx runtime — lightweight ONNX)**:

| Model | Size | Notes |
|-------|------|-------|
| parakeet-onnx-tdt-0.6b-v3 | 0.6 GB | Best for CPU, has punctuation |
| parakeet-onnx-ctc-0.6b | 0.6 GB | Fastest CPU inference |
| parakeet-onnx-ctc-1.1b | 1.2 GB | Better accuracy, still fast |
| parakeet-onnx-rnnt-0.6b | 0.6 GB | RNNT decoder |

**Transcription models (faster-whisper runtime)**:

| Model | Size | Notes |
|-------|------|-------|
| Systran/faster-whisper-large-v3-turbo | 1.6 GB | Default multilingual |
| Systran/faster-whisper-large-v3 | 3.0 GB | Best Whisper accuracy |
| Systran/faster-whisper-medium | 1.5 GB | Good mid-range option |
| Systran/faster-whisper-small | 0.5 GB | Resource-constrained |
| Systran/faster-whisper-base | 0.3 GB | Testing / low resource |
| Systran/faster-whisper-tiny | 0.1 GB | Minimal |

**Audio LLM models (vllm-asr runtime)**:

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
| CPU-only (EN) | nemo-onnx tdt-0.6b-v3 + faster-whisper lg-v3-turbo | ~2.2 GB |
| Scenario 2 (EN + multi) | parakeet-tdt-1.1b + fw lg-v3-turbo + pyannote + align | ~9.3 GB |
| Scenario 4 (everything) | All above + Voxtral Mini 3B | ~15 GB |

50 GB data volume is sufficient for any single-GPU setup. 100 GB gives room for experimentation with multiple model variants and audio LLMs.

---

## Upgrade Path

```
Scenario 1 (CPU)          → Just works, slow
    ↓ change instance_type
Scenario 2 (g5.xlarge)    → Your target: Parakeet + faster-whisper + realtime
    ↓ change instance_type
Scenario 3 (g5.2xlarge)   → More CPU headroom, same GPU
    ↓ new terraform module
Scenario 4 (g5.12xlarge)  → Parallel pipeline, Voxtral, multi-RT
    ↓ architecture change
Scenario 5 (ECS split)    → Auto-scaling, managed services, production
```

Each step is additive. Scenarios 1→3 are literally a one-line Terraform variable change. Scenario 4 needs a compose override for GPU pinning. Scenario 5 is a new Terraform module.

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

### How to add spot to existing Terraform

#### Option A: Simple — Spot on the single EC2 (Scenarios 1-3)

Minimal change to the existing `ec2-dalston` module:

```hcl
# infra/terraform/modules/ec2-dalston/variables.tf
variable "use_spot" {
  description = "Use spot instance pricing"
  type        = bool
  default     = false
}

variable "spot_max_price" {
  description = "Maximum hourly price for spot (empty = on-demand price cap)"
  type        = string
  default     = ""
}
```

```hcl
# infra/terraform/modules/ec2-dalston/main.tf
resource "aws_instance" "dalston" {
  # ... existing config ...

  instance_market_options {
    market_type = var.use_spot ? "spot" : null

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

```hcl
# terraform.tfvars
instance_type = "g5.xlarge"
use_spot      = true
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

2. **Hybrid spot/on-demand** (Scenario 5 territory): Run gateway + realtime workers on a small on-demand instance, batch engines on spot. Realtime sessions survive interruptions; batch tasks auto-retry.

For your use case (primarily batch with occasional realtime), approach 1 is fine.

#### Model loading after restart

If using `instance_interruption_behavior = "stop"` (Option A), the EBS volume persists. Model cache is intact. Boot → Docker starts → engines load cached models → ready in ~60-90 seconds.

If using fleet replacement (Option B), the new instance needs the EBS volume. Options:
- **Shared EBS**: Not possible across AZs. Only works if fleet is pinned to one AZ.
- **EFS for model cache**: Mount an EFS volume at `/data/models`. Slightly slower than EBS but survives instance replacement. ~$0.30/GB/month for infrequent access tier.
- **S3 model storage**: Already implemented in `S3ModelStorage`. Engines download from S3 on first use. Adds 1-5 minutes cold start for model download, but subsequent tasks use the local cache.

For Option A (the recommended path), this isn't an issue — EBS stays attached.

### Recommendation

**Start with Option A**: Add `use_spot = true` to your `terraform.tfvars`. That's it.

- Same Terraform module, same Docker Compose, same `dalston-up`/`dalston-down` workflow
- Instance stops on interruption, restarts when capacity returns
- 65% cost savings on the GPU instance
- EBS and Tailscale IP preserved
- Worst case: you're interrupted during a batch job, it auto-retries in ~2 minutes

If you find spot interruptions too frequent (unlikely for g5), just flip `use_spot = false` and you're back to on-demand. Zero architectural changes needed.
