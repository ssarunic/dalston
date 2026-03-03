# AWS Deployment Scenarios

Deployment options for Dalston on AWS, ordered from simplest/cheapest to most capable. Each scenario builds on the previous one.

Given the new runtime-based engine architecture (M36) with dynamic model loading from HuggingFace, the core question is: **how much GPU do you need, and how many runtimes do you want running?**

---

## Quick Reference

| Scenario | Instance | GPU | Monthly (8h/day) | w/ Spot | Models |
|----------|----------|-----|-------------------|---------|--------|
| 1. CPU-only | t3.xlarge | None | ~$35 | N/A | faster-whisper (all), parakeet-onnx (EN) |
| 2. Single GPU | g5.xlarge | 1x A10G 24GB | ~$100 | ~$35 | All batch + realtime |
| 3. Dual-purpose GPU | g5.2xlarge | 1x A10G 24GB | ~$150 | ~$50 | Higher throughput, concurrent batch+RT |
| 4. Multi-GPU | g5.12xlarge | 4x A10G 96GB | ~$500 | ~$170 | Full parallel pipeline |
| 5. Split infra | ECS + g5 | Varies | ~$200+ | Mixed | Auto-scaling engines |

Spot instances save ~65% on GPU instances. See [Spot Instances](#spot-instances) below.

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
│  Engines (CPU):                              │
│  ┌──────────────────────────────────┐        │
│  │ faster-whisper (large-v3-turbo)  │ 4 GB   │
│  └──────────────────────────────────┘        │
│  ┌──────────────────────────────────┐        │
│  │ nemo-onnx (parakeet-ctc-0.6b)   │ 2 GB   │
│  └──────────────────────────────────┘        │
│  ┌──────────┐  ┌────────┐  ┌───────┐        │
│  │ prepare  │  │ align  │  │ merge │        │
│  └──────────┘  └────────┘  └───────┘        │
│                                              │
│  + Tailscale                                 │
└──────────────────────────────────────────────┘
```

### What you get

- **English**: Parakeet ONNX (nemo-onnx) with native word timestamps, ~6.5x realtime on CPU
- **Multilingual**: faster-whisper with large-v3-turbo, ~2.5x realtime on CPU
- **No realtime streaming** (CPU too slow for real-time)
- Batch-only, sequential processing

### How to deploy

This is what you have today. Existing Terraform + `make aws-start` with the GPU profile disabled:

```bash
# In terraform.tfvars
instance_type = "t3.xlarge"

# On the instance — CPU-only, no --profile gpu
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra up -d \
  gateway orchestrator \
  stt-batch-prepare stt-batch-transcribe-faster-whisper \
  stt-batch-transcribe-nemo-onnx stt-batch-align-phoneme-cpu stt-batch-merge
```

### Memory budget (16 GB)

| Component | RAM |
|-----------|-----|
| OS + Docker | ~1.5 GB |
| Redis + Postgres | ~1 GB |
| Gateway + Orchestrator | ~0.5 GB |
| faster-whisper (large-v3-turbo, int8) | ~4 GB |
| nemo-onnx (parakeet-ctc-0.6b) | ~2 GB |
| prepare + align + merge | ~1 GB |
| **Headroom** | **~5.5 GB** |

### Cost

| State | Monthly |
|-------|---------|
| Running 24/7 | ~$135 |
| Running 8h/day weekdays | ~$35 |
| Stopped | ~$6 |

### Limitations

- Transcription is slow (~2.5-6.5x realtime, i.e., a 10min file takes 1.5-4 min)
- No realtime/streaming
- Running both runtimes simultaneously eats most of the RAM — pick one default, the other loads on demand via M36 model swapping
- No diarization (pyannote needs GPU or is very slow on CPU)

---

## Scenario 2: Single GPU — The Sweet Spot (`g5.xlarge`)

**For**: Your primary use case. Good Parakeet for English, faster-whisper for other languages, realtime streaming, diarization.

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
│  │ faster-whisper (large-v3-turbo)      │ ~3 GB VRAM │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ nemo (parakeet-tdt-1.1b)            │ ~6 GB VRAM │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ pyannote-4.0 (diarization)          │ ~3 GB VRAM │
│  └──────────────────────────────────────┘            │
│  ┌──────────────────────────────────────┐            │
│  │ RT: parakeet-rnnt-0.6b (streaming)  │ ~2 GB VRAM │
│  └──────────────────────────────────────┘            │
│                                                      │
│  + Tailscale                                         │
└──────────────────────────────────────────────────────┘
```

### What you get

- **English batch**: Parakeet TDT 1.1B via NeMo — best English accuracy, native word timestamps, ~150x realtime on GPU
- **Multilingual batch**: faster-whisper large-v3-turbo — 99 languages, ~30x realtime on GPU
- **English realtime**: Parakeet RNNT 0.6B streaming — sub-200ms latency
- **Diarization**: pyannote 4.0 for speaker identification
- **Full pipeline**: prepare → transcribe → align → diarize → merge

### VRAM budget (24 GB)

This is the key constraint. With M36 runtime model swapping, models load/unload on demand, but you need to plan for what's loaded simultaneously:

| Concurrent load | VRAM |
|-----------------|------|
| faster-whisper (large-v3-turbo) | ~3 GB |
| nemo (parakeet-tdt-1.1b) | ~6 GB |
| pyannote-4.0 | ~3 GB |
| RT parakeet-rnnt-0.6b | ~2 GB |
| **Total if all loaded** | **~14 GB** |
| CUDA overhead + buffers | ~2 GB |
| **Available headroom** | **~8 GB** |

All four fit simultaneously on 24 GB — this is the comfortable scenario. If you add alignment GPU, it still fits.

**If you only run English** (parakeet for batch + RT + pyannote): ~11 GB used, 13 GB free. Very comfortable.

### How to deploy

```bash
# In terraform.tfvars
instance_type = "g5.xlarge"

# On the instance — full GPU profile
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws --profile local-infra --profile gpu up -d
```

Or cherry-pick the engines you actually want:

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

### Cost

| State | Monthly |
|-------|---------|
| Running 24/7 | ~$300 |
| Running 8h/day weekdays | ~$100 |
| Stopped | ~$6 |

### Why this is the sweet spot for your needs

1. **Parakeet TDT 1.1B** is the best English model — native word timestamps, no alignment stage needed, WER competitive with Whisper large-v3
2. **faster-whisper** handles everything non-English at high speed
3. **M36 model swapping** means both runtimes share the GPU — the orchestrator routes `model=parakeet-tdt-1.1b` to the nemo runtime and `model=faster-whisper-large-v3-turbo` to the faster-whisper runtime
4. Realtime streaming works for live English transcription
5. Diarization available for meeting/interview recordings
6. One machine, one `terraform apply`, done

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

- Running more utility engines concurrently (prepare, align, merge run CPU-side)
- Higher batch throughput (CPU-bound stages don't bottleneck)
- Comfortable headroom for web console, monitoring stack (Prometheus/Grafana)
- Could add PII detection + audio redaction without RAM pressure

```bash
# In terraform.tfvars
instance_type = "g5.2xlarge"
```

Everything else is identical to Scenario 2. Only worth it if you're hitting CPU/RAM limits.

---

## Scenario 4: Multi-GPU Power (`g5.12xlarge`)

**For**: Production workloads, parallel pipeline stages, multiple concurrent realtime sessions, Voxtral/vLLM.

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
│  │ RT parakeet-rnnt-0.6b   │  │ pyannote-4.0 (diarize)    │  │
│  └─────────────────────────┘  └────────────────────────────┘  │
│                                                               │
│  GPU 2 (24 GB): Voxtral / vLLM                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ vllm-asr (Voxtral-Mini-3B)                              │ │
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
- **Voxtral**: State-of-the-art accuracy via audio LLM (no timestamps, chains with align stage)
- **PII detection on GPU**: Faster NER model inference
- **No model swapping delays**: Each runtime gets its own GPU, models stay loaded

### GPU assignment

Use `NVIDIA_VISIBLE_DEVICES` or `CUDA_VISIBLE_DEVICES` in docker-compose to pin engines to GPUs:

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

  stt-batch-diarize-pyannote-4.0:
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

This is the M16 "Future Phases" 3+4 combined. Significant infrastructure complexity increase. Only worth it when you need multi-user access or scale-to-zero economics.

---

## Recommendation: Start with Scenario 2

For your stated needs (Parakeet for English, faster-whisper for other languages):

**`g5.xlarge`** is the right answer. Here's why:

1. **Both runtimes fit in 24 GB VRAM** with room for diarization and realtime
2. **M36 model swapping** makes it seamless — submit a job with `model=parakeet-tdt-1.1b` or `model=faster-whisper-large-v3-turbo` and the right runtime handles it
3. **Dynamic HF download** means you can `dalston model pull` any compatible model without rebuilding images
4. **~$100/month** at 8h/day usage is very reasonable for GPU transcription
5. **Upgrade path is clear**: if you hit limits, bump to g5.2xlarge (same Terraform, change one variable)

### Concrete config for your needs

```hcl
# terraform.tfvars
instance_type   = "g5.xlarge"
data_volume_size = 100  # 50→100 GB for model cache (parakeet-tdt-1.1b is ~4.5 GB)
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
  stt-batch-diarize-pyannote-4.0 \
  stt-batch-merge \
  stt-rt-transcribe-parakeet-rnnt-0.6b
```

Then pre-pull your models so first transcription doesn't wait for download:

```bash
# On the instance, after services are up
# Models download to the shared /data/models volume
docker compose exec gateway python -c "
from dalston.gateway.services.model_registry import ModelRegistryService
# Or just hit the API:
"

# Or via API once M40 lands:
# dalston model pull parakeet-tdt-1.1b
# dalston model pull faster-whisper-large-v3-turbo
```

---

## Model Cache Sizing

The `/data/models` volume needs enough space for downloaded weights:

| Model | Size | Notes |
|-------|------|-------|
| parakeet-tdt-1.1b | ~4.5 GB | Best English accuracy |
| parakeet-ctc-0.6b | ~2.5 GB | Lighter English option |
| faster-whisper large-v3-turbo | ~3.1 GB | Default multilingual |
| faster-whisper large-v3 | ~6.2 GB | Highest Whisper accuracy |
| pyannote 4.0 | ~0.3 GB | Diarization |
| parakeet-rnnt-0.6b (RT) | ~2.5 GB | Realtime streaming |
| phoneme-align model | ~1.2 GB | Word alignment |
| **Total (typical setup)** | **~20 GB** | |

50 GB data volume is sufficient. 100 GB gives room for model experimentation.

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
