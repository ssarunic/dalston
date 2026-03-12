# AWS Engine Deployment Tutorial (For Dummies)

Deploy Dalston transcription engines on a single AWS instance. Three paths:

| Path | Engines | GPU needed? | Image size | Best for |
|------|---------|-------------|------------|----------|
| **A** | ONNX Parakeet + Pyannote 4.0 | No (but faster with GPU) | ~1GB + ~2GB | Lightweight, cost-effective |
| **B** | NeMo Parakeet + NeMo MSDD | Yes (strongly recommended) | ~12GB + ~8GB | Full NVIDIA stack, no HF token |

Both paths give you transcription + speaker diarization. Pick one.

---

## Prerequisites

You need these on your **local machine** (your laptop):

1. **AWS CLI** configured with credentials:

   ```bash
   aws sts get-caller-identity
   # Should print your account ID. If not, run: aws configure
   ```

2. **The dalston-aws script** accessible:

   ```bash
   # From the dalston repo root:
   ./infra/scripts/dalston-aws help
   ```

3. **A HuggingFace token** (only for Path A with Pyannote):
   - Go to <https://huggingface.co/settings/tokens>
   - Create a token with read access
   - Accept the license at <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - Save the token — you'll need it later

---

## Step 1: Launch the AWS Instance

### For Path A (ONNX + Pyannote) — CPU is fine

```bash
# CPU instance (~$25/month on-demand, ~$10/month spot)
./infra/scripts/dalston-aws setup --cpu

# Or with spot pricing (recommended for testing):
./infra/scripts/dalston-aws setup --cpu --spot
```

> **Note:** ONNX Parakeet works well on CPU (RTF 0.15 = 1 hour of audio in ~9 minutes).
> Pyannote on CPU is slow (RTF 1.2 = 1 hour of audio in ~72 minutes).
> If you want fast diarization too, use a GPU instance instead:
>
> ```bash
> ./infra/scripts/dalston-aws setup --spot
> ```

### For Path B (NeMo + MSDD) — GPU required

```bash
# GPU instance with spot pricing (~$35/month spot)
./infra/scripts/dalston-aws setup --spot
```

The script will print something like:

```
[dalston-aws] Setup complete!
[dalston-aws] Instance: i-0abc123 (3.10.45.67)

Next steps:
  1. SSH to the instance
  2. Set up Tailscale
  3. Clone your repo and start
```

## Step 2: SSH into the Instance

```bash
./infra/scripts/dalston-aws ssh
```

You're now on the EC2 instance. Everything below runs **on the instance**.

## Step 3: Set Up Tailscale (Secure Access)

```bash
sudo tailscale up
```

It prints a URL — open it in your browser and authenticate. Then note your Tailscale IP:

```bash
tailscale ip -4
# Example: 100.100.1.5
```

This IP is how you'll access the API from your laptop (no public ports exposed).

## Step 4: Clone the Repo

```bash
cd /data/dalston
git clone https://github.com/YOUR_USERNAME/dalston.git .
```

## Step 5: Create the Environment File

```bash
cat > /data/dalston/.env.aws << 'EOF'
# Core settings (these are set automatically by dalston-aws, but verify)
REDIS_URL=redis://redis:6379
DATABASE_URL=postgresql://dalston:dalston@postgres:5432/dalston

# S3 — the dalston-aws script created a bucket for you
# Check: cat ~/.dalston/aws-state.env | grep S3_BUCKET
DALSTON_S3_BUCKET=dalston-artifacts-YOUR_ACCOUNT_ID
DALSTON_S3_REGION=eu-west-2
AWS_REGION=eu-west-2

# Model cache on the EBS volume (persistent across restarts)
HF_HOME=/data/models
NEMO_CACHE=/data/models

# HuggingFace token (ONLY needed for Path A with Pyannote)
HF_TOKEN=hf_your_token_here
EOF
```

> **Find your S3 bucket name:**
>
> ```bash
> cat ~/.dalston/aws-state.env | grep S3_BUCKET
> ```

---

## Path A: ONNX Parakeet 0.6B v3 + Pyannote 4.0

### What you're deploying

```
┌─────────────────────────────────────────────┐
│  Your EC2 Instance                          │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Gateway  │  │  Redis   │  │ Postgres │  │
│  │ :8000    │  │  :6379   │  │  :5432   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────────┐                           │
│  │ Orchestrator │                           │
│  └──────────────┘                           │
│  ┌──────────────────────────────────────┐   │
│  │ stt-unified-onnx-cpu                │   │
│  │   Model: parakeet-onnx-tdt-0.6b-v3  │   │
│  │   Batch + Realtime transcription     │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │ stt-batch-diarize-pyannote-4.0-cpu  │   │
│  │   Speaker diarization               │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │ stt-batch-prepare                   │   │
│  │   Audio prep (normalize, resample)   │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### Step A1: Configure the ONNX Engine to Use TDT 0.6B v3

The default model in `docker-compose.yml` is `parakeet-onnx-ctc-0.6b`. You want `parakeet-onnx-tdt-0.6b-v3` instead (it adds punctuation + capitalization).

Add to your `.env.aws`:

```bash
cat >> /data/dalston/.env.aws << 'EOF'

# Override the default ONNX model to TDT v3 (punctuation + capitalization)
DALSTON_DEFAULT_MODEL_ID=parakeet-onnx-tdt-0.6b-v3
DALSTON_MODEL_PRELOAD=parakeet-onnx-tdt-0.6b-v3
EOF
```

### Step A2: Build and Start

```bash
cd /data/dalston

# Build the ONNX engine image (lightweight — takes ~2 minutes)
docker compose build stt-unified-onnx-cpu

# Build the Pyannote engine image (~5 minutes)
docker compose build stt-batch-diarize-pyannote-4.0-cpu

# Start everything: infra + orchestrator + your engines
docker compose \
  -f docker-compose.yml \
  -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws \
  --profile local-infra \
  up -d \
  gateway orchestrator \
  stt-batch-prepare \
  stt-unified-onnx-cpu \
  stt-batch-diarize-pyannote-4.0-cpu
```

### Step A3: Verify It's Running

```bash
# Check all containers are up
docker compose ps

# Check gateway health
curl http://localhost:8000/health

# Check engine logs (first run downloads the model — may take a few minutes)
docker compose logs -f stt-unified-onnx-cpu
docker compose logs -f stt-batch-diarize-pyannote-4.0-cpu
```

Wait until you see log lines like:

- ONNX: `"Model parakeet-onnx-tdt-0.6b-v3 loaded"` or `"Engine ready"`
- Pyannote: `"Pipeline loaded"` or `"Engine ready"`

### Step A4: Test It

From your **laptop** (using the Tailscale IP):

```bash
# Simple transcription (no diarization)
curl -X POST http://100.100.1.5:8000/v1/audio/transcriptions \
  -F file=@test.wav

# With diarization (speaker identification)
curl -X POST http://100.100.1.5:8000/v1/audio/transcriptions \
  -F file=@test.wav \
  -F diarize=true
```

### Optional: Upgrade Pyannote to GPU Later

If CPU diarization is too slow, switch to a GPU instance:

```bash
# From your laptop:
./infra/scripts/dalston-aws teardown
./infra/scripts/dalston-aws setup --spot  # GPU instance
```

Then repeat steps 2–4, but use the GPU service names instead:

```bash
# Start with GPU profile — note the service names without "-cpu" suffix:
docker compose \
  -f docker-compose.yml \
  -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws \
  --profile local-infra --profile gpu \
  up -d \
  gateway orchestrator \
  stt-batch-prepare \
  stt-unified-onnx \
  stt-batch-diarize-pyannote-4.0
```

---

## Path B: NeMo Parakeet + NeMo MSDD (Full NVIDIA Stack)

### What you're deploying

```
┌─────────────────────────────────────────────┐
│  g5.xlarge (GPU instance)                   │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Gateway  │  │  Redis   │  │ Postgres │  │
│  │ :8000    │  │  :6379   │  │  :5432   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────────┐                           │
│  │ Orchestrator │                           │
│  └──────────────┘                           │
│  ┌──────────────────────────────────────┐   │
│  │ stt-unified-nemo            [GPU]   │   │
│  │   Model: nvidia/parakeet-tdt-0.6b-v3│   │
│  │   Batch + Realtime transcription     │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │ stt-batch-diarize-nemo-msdd [GPU]   │   │
│  │   VAD + TitaNet + MSDD              │   │
│  │   No HF token needed!               │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │ stt-batch-prepare                   │   │
│  │   Audio prep (normalize, resample)   │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### Advantages over Path A

- **No HuggingFace token needed** — all NeMo models are CC-BY-4.0
- **Larger NeMo model zoo** — 0.6B and 1.1B variants, CTC/TDT/RNNT decoders
- **MSDD** — neural overlap detection (better for meetings with crosstalk)

### Tradeoffs

- **Much larger Docker images** (~12GB for NeMo vs ~1GB for ONNX)
- **GPU strongly recommended** — NeMo on CPU is painfully slow
- **Longer first build** (~15-20 minutes)

### Step B1: Configure the NeMo Model

Add to your `.env.aws` to use the 0.6B v3 model (smaller VRAM, has punctuation + capitalization):

```bash
cat >> /data/dalston/.env.aws << 'EOF'

# Override the default NeMo model to TDT 0.6B v3
DALSTON_DEFAULT_MODEL_ID=nvidia/parakeet-tdt-0.6b-v3
DALSTON_MODEL_PRELOAD=nvidia/parakeet-tdt-0.6b-v3
EOF
```

### Step B2: Build Images

```bash
cd /data/dalston

# Build NeMo unified engine (WARNING: ~15 minutes, ~12GB image)
docker compose build stt-unified-nemo

# Build NeMo MSDD diarization (~10 minutes, downloads VAD + TitaNet + MSDD models)
docker compose build stt-batch-diarize-nemo-msdd
```

> **Tip:** The g5.xlarge has 24GB GPU VRAM. The NeMo 0.6B model uses ~2GB and MSDD
> uses ~4GB, so both fit comfortably on the same GPU. They time-share it — one
> runs at a time per job.

### Step B3: Start Everything

```bash
docker compose \
  -f docker-compose.yml \
  -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws \
  --profile local-infra --profile gpu \
  up -d \
  gateway orchestrator \
  stt-batch-prepare \
  stt-unified-nemo \
  stt-batch-diarize-nemo-msdd
```

### Step B4: Verify

```bash
# Check containers
docker compose ps

# Watch NeMo engine startup (first run downloads model — can take 5+ minutes)
docker compose logs -f stt-unified-nemo

# Watch MSDD engine
docker compose logs -f stt-batch-diarize-nemo-msdd

# Verify GPU is visible to containers
docker compose exec stt-unified-nemo nvidia-smi
```

### Step B5: Test It

From your laptop:

```bash
curl -X POST http://100.100.1.5:8000/v1/audio/transcriptions \
  -F file=@test.wav \
  -F diarize=true
```

---

## Day-to-Day Operations

### View logs

```bash
# On the instance:
docker compose logs -f gateway          # API logs
docker compose logs -f stt-unified-onnx-cpu  # Engine logs (Path A)
docker compose logs -f stt-unified-nemo      # Engine logs (Path B)
```

### Stop to save money

```bash
# From your laptop:
./infra/scripts/dalston-aws down
# Instance stops. EBS data preserved. Cost: ~$4/month (just storage)
```

### Start back up

```bash
./infra/scripts/dalston-aws up
# Instance boots, systemd auto-starts Docker Compose
```

### Check status

```bash
./infra/scripts/dalston-aws status
```

### Delete everything

```bash
./infra/scripts/dalston-aws teardown
# Removes instance, EBS, security group, IAM role
# S3 bucket is NOT deleted (your transcription data is there)
```

---

## Hybrid Mode: Remote Engine, Local Everything Else

The most useful pattern for development: run Gateway, Orchestrator, Redis, and
Postgres **locally** on your Mac, and offload only the heavy engine(s) to a GPU
instance on AWS. Tailscale makes the remote instance behave like a local machine.

```
┌─── Your Mac ──────────────────────┐      ┌─── AWS g4dn.xlarge ────────────┐
│                                   │      │                                │
│  Gateway :8000                    │      │  Engine container(s)           │
│  Orchestrator                     │      │    connects to Redis ──────────┼──┐
│  Redis :6379  ◄───────────────────┼──────┼────────────────────────────────┘  │
│  Postgres :5432                   │      │    connects to MinIO ─────────────┘
│  MinIO :9000  ◄───────────────────┼──────┼───────────────────────────────┘
│                                   │      │                                │
│  Tailscale IP: 100.x.y.z         │      │  Tailscale IP: 100.a.b.c      │
└───────────────────────────────────┘      └────────────────────────────────┘
```

### Prerequisites

1. **Local stack running** on your Mac:

   ```bash
   make dev-minimal
   # or: docker compose --profile local-infra up -d
   ```

2. **Tailscale** installed on both machines and connected to the same tailnet.

3. **Know your Mac's Tailscale IP:**

   ```bash
   tailscale ip -4
   # Example: 100.64.1.10
   ```

4. **AWS instance with GPU + Docker + NVIDIA runtime.** Use `dalston-aws`:

   ```bash
   ./infra/scripts/dalston-aws setup --spot
   ./infra/scripts/dalston-aws ssh
   # On instance:
   sudo tailscale up   # authenticate
   ```

5. **Clone the repo on the instance** (needed to build images):

   ```bash
   cd /data && git clone https://github.com/YOUR_USER/dalston.git
   cd /data/dalston
   ```

### The Connection Variables

Every engine needs to reach your local Redis and MinIO. Set these once:

```bash
# On the AWS instance — replace 100.64.1.10 with YOUR Mac's Tailscale IP
export LOCAL_TS_IP=100.64.1.10
```

---

### Run Pyannote 4.0 Only (Diarization)

Pyannote is a **batch engine** — it polls Redis for diarize tasks, processes them,
writes results to S3. No ports need to be exposed.

**Build (first time only):**

```bash
cd /data/dalston
docker compose build stt-batch-diarize-pyannote-4.0
```

**Run:**

```bash
docker run -d --name pyannote \
  --gpus all \
  --restart unless-stopped \
  -v /data/models:/models \
  -e REDIS_URL="redis://${LOCAL_TS_IP}:6379" \
  -e DALSTON_S3_ENDPOINT_URL="http://${LOCAL_TS_IP}:9000" \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_REGION=eu-west-2 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e DALSTON_ENGINE_ID=pyannote-4.0 \
  -e DALSTON_WORKER_ID=pyannote-aws-gpu1 \
  -e HF_TOKEN=hf_your_token_here \
  -e DALSTON_LOG_LEVEL=INFO \
  -e DALSTON_LOG_FORMAT=json \
  dalston/stt-batch-diarize-pyannote-4.0:latest
```

**Verify:**

```bash
docker logs -f pyannote
# Should see: "Engine ready" or "Polling for tasks"
```

**Stop:**

```bash
docker stop pyannote && docker rm pyannote
```

---

### Run ONNX Parakeet Only (Transcription)

The unified ONNX engine handles **both batch and realtime**. It runs a WebSocket
server on port 9000, so for realtime to work, the gateway on your Mac must be
able to reach it. Set `DALSTON_WORKER_ENDPOINT` to the instance's Tailscale IP.

**Build (first time only):**

```bash
cd /data/dalston
docker compose build stt-unified-onnx   # GPU variant
```

**Run:**

```bash
# Get THIS instance's Tailscale IP for the worker endpoint
INSTANCE_TS_IP=$(tailscale ip -4)

docker run -d --name onnx-parakeet \
  --gpus all \
  --restart unless-stopped \
  -p 9000:9000 \
  -v /data/models:/models \
  -e REDIS_URL="redis://${LOCAL_TS_IP}:6379" \
  -e DALSTON_S3_ENDPOINT_URL="http://${LOCAL_TS_IP}:9000" \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_REGION=eu-west-2 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e DALSTON_UNIFIED_ENGINE_ENABLED=true \
  -e DALSTON_ENGINE_ID=onnx \
  -e DALSTON_DEFAULT_MODEL_ID=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_MODEL_PRELOAD=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_INSTANCE=stt-unified-onnx-aws \
  -e DALSTON_WORKER_PORT=9000 \
  -e DALSTON_WORKER_ENDPOINT="ws://${INSTANCE_TS_IP}:9000" \
  -e DALSTON_MAX_SESSIONS=4 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_RT_RESERVATION=2 \
  -e DALSTON_BATCH_MAX_INFLIGHT=4 \
  -e DALSTON_TOTAL_CAPACITY=6 \
  -e DALSTON_LOG_LEVEL=INFO \
  -e DALSTON_LOG_FORMAT=json \
  dalston/stt-unified-onnx:1.0.0
```

> **Why `-p 9000:9000`?** The gateway proxies realtime WebSocket sessions to the
> engine's port 9000. The engine registers `ws://<tailscale-ip>:9000` with Redis,
> and the gateway connects to it. For batch-only use, you can skip the port mapping.

**Verify:**

```bash
docker logs -f onnx-parakeet
# Should see: "Model parakeet-onnx-tdt-0.6b-v3 loaded" then "Engine ready"
```

**Stop:**

```bash
docker stop onnx-parakeet && docker rm onnx-parakeet
```

---

### Run Both on the Same Instance (Combo)

Just run both commands above. They share the GPU (24 GB VRAM on g4dn.xlarge —
ONNX uses ~2 GB, Pyannote uses ~2 GB, plenty of room).

```bash
# Set the shared variables once
export LOCAL_TS_IP=100.64.1.10       # Your Mac
INSTANCE_TS_IP=$(tailscale ip -4)    # This AWS instance

# 1. Start Pyannote (diarization)
docker run -d --name pyannote \
  --gpus all \
  --restart unless-stopped \
  -v /data/models:/models \
  -e REDIS_URL="redis://${LOCAL_TS_IP}:6379" \
  -e DALSTON_S3_ENDPOINT_URL="http://${LOCAL_TS_IP}:9000" \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_REGION=eu-west-2 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e DALSTON_ENGINE_ID=pyannote-4.0 \
  -e DALSTON_WORKER_ID=pyannote-aws-gpu1 \
  -e HF_TOKEN=hf_your_token_here \
  -e DALSTON_LOG_LEVEL=INFO \
  -e DALSTON_LOG_FORMAT=json \
  dalston/stt-batch-diarize-pyannote-4.0:latest

# 2. Start ONNX Parakeet (transcription + realtime)
docker run -d --name onnx-parakeet \
  --gpus all \
  --restart unless-stopped \
  -p 9000:9000 \
  -v /data/models:/models \
  -e REDIS_URL="redis://${LOCAL_TS_IP}:6379" \
  -e DALSTON_S3_ENDPOINT_URL="http://${LOCAL_TS_IP}:9000" \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_REGION=eu-west-2 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e DALSTON_UNIFIED_ENGINE_ENABLED=true \
  -e DALSTON_ENGINE_ID=onnx \
  -e DALSTON_DEFAULT_MODEL_ID=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_MODEL_PRELOAD=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_INSTANCE=stt-unified-onnx-aws \
  -e DALSTON_WORKER_PORT=9000 \
  -e DALSTON_WORKER_ENDPOINT="ws://${INSTANCE_TS_IP}:9000" \
  -e DALSTON_MAX_SESSIONS=4 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_RT_RESERVATION=2 \
  -e DALSTON_BATCH_MAX_INFLIGHT=4 \
  -e DALSTON_TOTAL_CAPACITY=6 \
  -e DALSTON_LOG_LEVEL=INFO \
  -e DALSTON_LOG_FORMAT=json \
  dalston/stt-unified-onnx:1.0.0
```

**Verify both are running:**

```bash
docker ps
# Should show: pyannote (Up), onnx-parakeet (Up)

docker logs pyannote --tail 5
docker logs onnx-parakeet --tail 5
```

**Test from your Mac:**

```bash
# Batch transcription + diarization (uses both remote engines)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F file=@test.wav \
  -F diarize=true

# Realtime transcription (WebSocket to remote ONNX engine)
# Use wscat or the web console at http://localhost:8000
```

**Stop everything:**

```bash
docker stop pyannote onnx-parakeet && docker rm pyannote onnx-parakeet
```

---

### Convenience: Save as a Script

Create `/data/dalston/start-engines.sh` on the AWS instance:

```bash
#!/bin/bash
set -euo pipefail

LOCAL_TS_IP="${1:?Usage: $0 <mac-tailscale-ip>}"
INSTANCE_TS_IP=$(tailscale ip -4)
HF_TOKEN="${HF_TOKEN:?Set HF_TOKEN env var}"

COMMON_ENV=(
  -e REDIS_URL="redis://${LOCAL_TS_IP}:6379"
  -e DALSTON_S3_ENDPOINT_URL="http://${LOCAL_TS_IP}:9000"
  -e DALSTON_S3_BUCKET=dalston-artifacts
  -e DALSTON_S3_REGION=eu-west-2
  -e AWS_ACCESS_KEY_ID=minioadmin
  -e AWS_SECRET_ACCESS_KEY=minioadmin
  -e DALSTON_LOG_LEVEL=INFO
  -e DALSTON_LOG_FORMAT=json
)

echo "Starting Pyannote 4.0..."
docker run -d --name pyannote --gpus all --restart unless-stopped \
  -v /data/models:/models \
  "${COMMON_ENV[@]}" \
  -e DALSTON_ENGINE_ID=pyannote-4.0 \
  -e DALSTON_WORKER_ID=pyannote-aws-gpu1 \
  -e HF_TOKEN="${HF_TOKEN}" \
  dalston/stt-batch-diarize-pyannote-4.0:latest

echo "Starting ONNX Parakeet TDT 0.6B v3..."
docker run -d --name onnx-parakeet --gpus all --restart unless-stopped \
  -p 9000:9000 -v /data/models:/models \
  "${COMMON_ENV[@]}" \
  -e DALSTON_UNIFIED_ENGINE_ENABLED=true \
  -e DALSTON_ENGINE_ID=onnx \
  -e DALSTON_DEFAULT_MODEL_ID=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_MODEL_PRELOAD=parakeet-onnx-tdt-0.6b-v3 \
  -e DALSTON_INSTANCE=stt-unified-onnx-aws \
  -e DALSTON_WORKER_PORT=9000 \
  -e DALSTON_WORKER_ENDPOINT="ws://${INSTANCE_TS_IP}:9000" \
  -e DALSTON_MAX_SESSIONS=4 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_RT_RESERVATION=2 \
  -e DALSTON_BATCH_MAX_INFLIGHT=4 \
  -e DALSTON_TOTAL_CAPACITY=6 \
  dalston/stt-unified-onnx:1.0.0

echo "Done. Both engines running."
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Then:

```bash
chmod +x /data/dalston/start-engines.sh
export HF_TOKEN=hf_your_token
./start-engines.sh 100.64.1.10
```

---

### Important: Local Redis Must Accept Remote Connections

By default, `make dev-minimal` starts Redis inside Docker bound to the Docker
network only. For the remote engine to reach it via Tailscale, Redis must listen
on your Mac's Tailscale interface.

**Option A — Expose Redis port (simplest):**

Your `docker-compose.yml` already has `ports: ["6379:6379"]` for Redis and
`ports: ["9000:9000", "9001:9001"]` for MinIO. Verify with:

```bash
# On your Mac
curl -s telnet://$(tailscale ip -4):6379 </dev/null && echo "Redis reachable" || echo "Not reachable"
```

If not reachable, your firewall may be blocking it. On macOS:

```bash
# Check if Redis is listening on all interfaces
docker port redis
# Should show: 6379/tcp -> 0.0.0.0:6379
```

**Option B — Use Tailscale Funnel (more secure):**

Only expose specific ports via Tailscale, no public access. See Tailscale docs
for `tailscale serve` configuration.

---

## Troubleshooting

### "Model download failed" or "Connection timeout"

The first startup downloads models from HuggingFace/NGC. If it fails:

```bash
# Restart the engine to retry
docker compose restart stt-unified-onnx-cpu  # or stt-unified-nemo

# Check if disk is full (models are stored on EBS)
df -h /data
```

### "CUDA out of memory" (Path B only)

The g5.xlarge has 24GB VRAM. If both engines try to use GPU simultaneously:

```bash
# Check GPU usage
nvidia-smi

# Reduce NeMo concurrent sessions
# In .env.aws, add:
DALSTON_MAX_SESSIONS=2
```

### Pyannote says "token required" (Path A only)

Your `HF_TOKEN` is missing or invalid:

```bash
# Verify it's in the env file
grep HF_TOKEN /data/dalston/.env.aws

# Test the token (from your laptop)
curl -H "Authorization: Bearer hf_your_token" \
  https://huggingface.co/api/models/pyannote/speaker-diarization-3.1
```

### Container won't start / "image not found"

You need to build the images on the instance first:

```bash
docker compose build stt-unified-onnx-cpu    # Path A
docker compose build stt-batch-diarize-pyannote-4.0-cpu  # Path A
docker compose build stt-unified-nemo         # Path B
docker compose build stt-batch-diarize-nemo-msdd  # Path B
```

### Logs say "Engine ready" but API returns 503

The orchestrator might not have discovered the engine yet. Wait 10 seconds (engines heartbeat every 5s), then retry. If it persists:

```bash
# Check Redis for engine registrations
docker compose exec redis redis-cli KEYS "dalston:engines:*"
```

---

## Cost Summary

| Setup | Instance | On-demand | Spot | Stopped |
|-------|----------|-----------|------|---------|
| Path A (CPU) | t3.xlarge | ~$120/mo | ~$40/mo | ~$4/mo |
| Path A (GPU) | g5.xlarge | ~$725/mo | ~$250/mo | ~$4/mo |
| Path B (GPU) | g5.xlarge | ~$725/mo | ~$250/mo | ~$4/mo |

> **Tip:** Use `dalston-aws down` when not in use. If you only run it 8 hours/day
> on weekdays, that's ~23% of the month = ~$58/mo for a spot GPU instance.
