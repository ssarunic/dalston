# Engines Architecture Review

**Date**: 2026-02-26
**Scope**: Engine pluggability, deployment flexibility, scaling, cost, and ease of setup

---

## What You Built (and What's Good)

Before getting into problems, it's worth acknowledging the parts that are well-designed:

**The engine SDK abstraction is clean.** An engine author implements `process(TaskInput) -> TaskOutput`, declares capabilities in `engine.yaml`, and the SDK handles Redis stream polling, S3 I/O, heartbeating, metrics, and crash recovery. This is genuinely good. The contract is simple and the boilerplate is minimal.

**Capability-driven engine selection is smart.** The `engine_selector.py` dynamically skips pipeline stages based on what the selected engine can do natively (e.g. Parakeet produces word timestamps, so alignment is skipped). This means the system adapts to what's running without manual pipeline configuration.

**The dual registry pattern works.** Catalog answers "what could I start?" while the Redis registry answers "what's running right now?" This separation is correct and enables good error messages (e.g. "no engine running for diarize, but pyannote-3.1 is available — run `docker compose up ...`").

**The batch/realtime split is architecturally sound.** Queue-based for throughput, WebSocket for latency. Different SDKs for different needs. This is the right call.

---

## The Core Problem: Flexibility Without a Runtime

You designed an architecture where engines are pluggable, configurable, and independently deployable. But the system that actually *manages* that flexibility doesn't exist. You have:

- `engine.yaml` declaring GPU needs, VRAM requirements, CPU fallback support, RTF metrics
- A catalog that knows every engine that *could* run
- A selector that picks the best *running* engine

But nothing that:
- Starts engines based on demand
- Places engines on appropriate hardware
- Co-locates engines to share GPU memory
- Scales up/down based on queue depth
- Manages spot instance lifecycle

The metadata exists. The automation to act on it doesn't. This is the gap creating the "mess" feeling — you built the vocabulary for a sophisticated deployment system but the deployment itself is still `docker compose up`.

---

## Problem 1: The Single-Instance Trap

### Current State

Your AWS deployment is a single `g5.xlarge` (1 GPU, 16GB RAM, 24GB VRAM) running *everything* via Docker Compose. Every engine is a separate container, each loading its own model into GPU memory.

A typical transcription pipeline needs:
- Parakeet TDT 0.6B: ~2GB VRAM
- WhisperX align: ~2GB VRAM
- Pyannote 3.1: ~2GB VRAM
- Prepare + merge + PII: CPU only

That's ~6GB VRAM for batch alone. Add a realtime Parakeet worker (~2GB), and you're at ~8GB. Seems fine on 24GB.

But if you scale `--scale stt-batch-transcribe-parakeet=2`, that's another model load. Scale diarization too, and you're pushing VRAM limits fast. Docker Compose has no concept of "this container needs 4GB VRAM, and the GPU only has 24GB total."

### Why This Hurts

- **No GPU memory accounting**: Two engines can both request the GPU and OOM each other.
- **No affinity**: A CPU-only engine (prepare, merge) takes a container slot on a GPU instance for no reason.
- **Can't scale stages independently**: Queue depth for transcription might be 50, but diarization is idle. You can't add a transcribe-only instance without spinning up the whole compose stack.

---

## Problem 2: Co-location Is an Unsupported Middle Ground

Your question "should transcriber and diarizer share an instance?" reveals a real tension. The current architecture offers two extremes:

1. **Everything on one box** (docker compose on single EC2) — what you have now
2. **Each engine on its own instance** — possible in theory, but nothing manages it

The useful middle ground — **grouping engines onto instances based on hardware affinity** — has no mechanism. For example:

- GPU engines (transcribe + diarize + align) sharing a `g5.xlarge`
- CPU engines (prepare + merge + PII) on a cheap `c6i.xlarge`
- Realtime workers on dedicated GPU instances (latency-sensitive, can't share with batch)

This grouping can't be expressed in the current system. Docker Compose profiles get you *on/off* per engine but not *which-host*.

---

## Problem 3: The Scaling Gap

`engine.yaml` declares `max_concurrency`, `rtf_gpu`, `rtf_cpu`, hardware requirements. The catalog knows every deployable engine. But there's no autoscaler that reads queue depth from Redis and decides "I need 2 more transcribe instances."

What you have:
```bash
docker compose --scale stt-batch-transcribe-faster-whisper-base=2  # manual
```

What you'd need:
```
if queue_depth("dalston:stream:parakeet") > 10 for 60s:
    start_instance(engine="parakeet", instance_type="g5.xlarge", spot=True)
```

This is a non-trivial system to build. Kubernetes has it (HPA + Karpenter). ECS has it (service auto-scaling). Docker Compose on EC2 doesn't.

---

## Problem 4: Spot Instance Aspirations Without Infrastructure

Your engine SDK has spot-instance resilience built in (instance IDs vs logical IDs, crash recovery, stale task claiming). This is forward-thinking. But the *infrastructure* to actually use spot instances doesn't exist:

- Terraform creates a single on-demand `g5.xlarge`
- No launch templates for spot
- No capacity provider
- No termination handler
- No mixed instance fleet

The application layer is ready for spot. The infrastructure layer isn't.

---

## Problem 5: 15+ Engine Variants Create Maintenance Burden

You have:
- 3 transcription families (Faster-Whisper, Parakeet, Voxtral) × multiple sizes
- 3 diarization engines (Pyannote 3.1, 4.0, NeMo MSDD)
- CPU/GPU variants for most
- 4 realtime engine variants

Each needs: Dockerfile, engine.yaml, engine.py, docker-compose service entry, model downloads, testing. You're maintaining ~20 service definitions in a 746-line docker-compose.yml.

In practice, most users will run *one* transcriber and *one* diarizer. The variety is good for benchmarking and choice, but every engine you add is ongoing maintenance.

---

## Problem 6: Setup Complexity for New Users

To get a working system, a new user needs to:

1. Install Docker with GPU support (if using GPU engines)
2. Get HuggingFace tokens (for pyannote, some models need gated access)
3. Run `make dev` and wait for ~10 Docker images to build
4. Wait for model downloads (first run can take 20+ minutes)
5. Understand which profile to use (local-infra, gpu, observability)
6. Know that `make dev-minimal` exists for quick iteration

For AWS:
1. Install Terraform, AWS CLI, Tailscale
2. Create IAM user, SSH keys
3. Run Terraform
4. SSH in, set up Tailscale
5. Manually start the service
6. Create API keys via console

Compare this to something like a SaaS transcription API: get key, call endpoint.

---

## Recommendations

### 1. Accept Two Deployment Tiers (Don't Try to Be Universal)

Stop trying to make one architecture serve all scenarios. Define two explicit tiers:

**Tier 1: Single-Box ("Appliance Mode")**

Target: Self-hosters, small teams, dev/test. One machine, one `docker compose up`.

- Pick sensible defaults: one transcriber, one diarizer, one realtime worker
- Pre-built `docker-compose.yml` with no profiles to understand
- Single env file with only the essentials (HF_TOKEN, maybe ANTHROPIC_API_KEY)
- Works on a single GPU machine or CPU-only (slower)
- No scaling pretensions — it processes jobs sequentially, and that's fine

**Tier 2: Scaled Deployment ("Cloud Mode")**

Target: Production workloads with auto-scaling needs.

This is where you invest in a real orchestration layer. But instead of building one from scratch, adopt one:

| Option | Effort | GPU Support | Spot | Auto-scale |
|--------|--------|-------------|------|------------|
| ECS + Fargate/EC2 | Medium | Yes (p3/g5) | Yes | Yes |
| Kubernetes + Karpenter | High | Yes | Yes | Yes |
| Modal / Beam / RunPod | Low | Yes | Varies | Built-in |

For your architecture, **ECS on EC2** (not Fargate — no GPU on Fargate) is probably the best fit:
- Each engine becomes an ECS service with a task definition
- Task definitions declare GPU/CPU needs (the data from `engine.yaml`)
- Auto-scaling based on Redis queue depth (custom CloudWatch metric)
- Spot capacity providers for batch, on-demand for realtime
- No Kubernetes complexity

### 2. Introduce "Deployment Profiles" as a First-Class Concept

Rather than relying on users to understand docker-compose profiles, create named deployment profiles:

```yaml
# dalston-profiles.yaml
profiles:
  minimal:
    description: "Fastest startup, English-only, CPU-friendly"
    engines:
      transcribe: faster-whisper-base
      diarize: null                    # no diarization
      realtime: null                   # no realtime
    hardware: cpu-ok

  standard:
    description: "Good quality, diarization, GPU recommended"
    engines:
      transcribe: parakeet-tdt-0.6b-v3
      align: whisperx-align
      diarize: pyannote-3.1
      realtime: parakeet-rnnt-0.6b
    hardware: gpu-8gb

  quality:
    description: "Best quality, all features"
    engines:
      transcribe: parakeet-tdt-1.1b
      align: whisperx-align
      diarize: pyannote-4.0
      pii_detect: pii-presidio
      realtime: parakeet-rnnt-1.1b
    hardware: gpu-24gb
```

Then: `make dev PROFILE=minimal` or `make dev PROFILE=standard`.

A script reads the profile, generates the appropriate `docker compose` command with only the needed services. Users pick a profile name, not a combination of `--profile` flags.

### 3. Solve Co-location With GPU Groups, Not General Scheduling

For the single-box tier, co-location is implicit (everything shares one GPU). The question "should transcriber and diarizer share an instance" only matters for Tier 2.

For Tier 2 (ECS), solve it with **placement constraints**:

```
GPU group: transcribe + align + diarize  (share a g5.xlarge)
CPU group: prepare + merge + pii_detect  (share a c6i.large)
RT group:  realtime workers              (dedicated g5.xlarge, latency-sensitive)
```

These groups are static per deployment profile, not dynamically decided at runtime. Trying to solve placement at runtime is a container orchestrator problem — use ECS/K8s for that rather than building your own.

### 4. Reduce Engine Variants, Add Them Lazily

You don't need 8 transcriber variants from day one. Ship with:

- **Faster-Whisper base** (CPU-friendly, good enough, fast)
- **Parakeet TDT 0.6B** (GPU, better quality, good speed)
- **Pyannote 3.1** (diarization, proven)

Mark others as "community/experimental" in docs. Users who need Voxtral for multilingual or NeMo MSDD for specific use cases can enable them, but they're not in the default path.

This shrinks your docker-compose from 746 lines to ~200 and makes `make dev` start 3 engines instead of 10.

### 5. Make the "Getting Started" Path Trivial

The single most impactful change for adoption:

```bash
git clone https://github.com/your-org/dalston
cd dalston
cp .env.example .env          # edit HF_TOKEN
make start                     # pulls pre-built images, starts minimal stack
# → Gateway running at http://localhost:8000
# → Ready to transcribe in ~60 seconds (model download on first run)
```

This means:
- **Pre-built Docker images** on a registry (no local `docker build`)
- **A single `make start`** that picks the minimal profile
- **No Terraform, no AWS, no profiles** for someone just trying it out
- Model downloads happen lazily on first job (with progress indication)

### 6. Separate the Auto-Scaling Question Entirely

Auto-scaling is a deployment concern, not an application concern. Your engine SDK already handles:
- Registration and deregistration
- Crash recovery and stale task detection
- Instance-based tracking for spot resilience

What's missing is the *infrastructure* that starts/stops instances. This should be a separate component (an "autoscaler") that:
1. Watches Redis queue depths
2. Reads the engine catalog for hardware requirements
3. Calls AWS APIs to start/stop instances (or ECS to adjust desired count)

This can be a simple Python script that runs as a sidecar, not part of the engine architecture itself. Build it when you actually need scaling, not before.

### 7. Consider a Configuration-as-Code Approach for Cloud

Instead of docker-compose overrides layered on docker-compose, consider generating deployment configs from a single source of truth:

```yaml
# dalston-deploy.yaml
environment: production
region: eu-west-2
storage:
  type: s3                              # or "minio" for local
  bucket: dalston-artifacts
database:
  type: rds                             # or "postgres-container" for local
  instance_class: db.t3.medium
engines:
  transcribe:
    engine: parakeet-tdt-0.6b-v3
    instances: { min: 1, max: 4 }
    spot: true
  diarize:
    engine: pyannote-3.1
    instances: { min: 1, max: 2 }
    spot: true
  realtime:
    engine: parakeet-rnnt-0.6b
    instances: { min: 1, max: 2 }
    spot: false                         # latency-sensitive
```

A generator script produces either:
- Docker Compose files (for single-box)
- ECS task definitions + service configs (for AWS)
- Kubernetes manifests (if someone contributes it)

The `engine.yaml` metadata feeds into this — GPU requirements, memory, VRAM — so the generator can pick appropriate instance types.

---

## Suggested Priority Order

| Priority | Action | Impact | Effort |
|----------|--------|--------|--------|
| 1 | Define "minimal" and "standard" profiles, make `make start` work with pre-built images | Huge for adoption | Low |
| 2 | Trim default engines to 3-4 core ones, move rest to opt-in | Reduces maintenance and confusion | Low |
| 3 | Formalize the two-tier model in docs (single-box vs scaled) | Reduces architectural confusion | Low |
| 4 | Build ECS task definitions from engine.yaml metadata | Enables real cloud scaling | Medium |
| 5 | Add queue-depth autoscaler sidecar | Enables demand-based scaling | Medium |
| 6 | Add spot instance support in Terraform | Cost savings for batch | Medium |
| 7 | Configuration-as-code generator for multi-env | Replaces compose override layering | High |

---

## The Meta-Lesson

The architecture isn't wrong — it's incomplete. You built the *capability model* (engine.yaml, catalog, selector) but not the *deployment model* that acts on it. The fix isn't to tear things down, it's to:

1. **Simplify the default path** (profiles, fewer engines, pre-built images)
2. **Defer scaling to proven tools** (ECS/K8s, not custom orchestration)
3. **Keep the pluggability** but make it opt-in, not the default experience

The engine SDK, capability-driven selection, and dual registry are genuinely good patterns. The problem is that they're exposed as complexity rather than hidden as infrastructure. A user should be able to run `make start` and get a working transcription server without understanding any of this. The pluggability should be there when they need it, invisible when they don't.
