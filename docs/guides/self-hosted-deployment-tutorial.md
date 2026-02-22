# Self-Hosted Deployment Tutorial

Step-by-step guide to deploy Dalston on your own server using Docker Compose.

## Prerequisites

- Linux server (Ubuntu 22.04+ recommended)
- Docker and Docker Compose installed
- At least 8GB RAM (16GB+ recommended for full pipeline)
- (Optional) NVIDIA GPU with CUDA for accelerated transcription
- (Optional) HuggingFace account for speaker diarization

## 1. Server Setup

### Install Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Log out and back in for group changes to take effect
```

### Install Docker Compose

```bash
# Docker Compose is included with Docker Desktop
# For Linux servers, install the plugin:
sudo apt-get update
sudo apt-get install docker-compose-plugin

# Verify installation
docker compose version
```

### (Optional) Install NVIDIA Container Toolkit

Required only if using GPU acceleration:

```bash
# Add NVIDIA repository
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install toolkit
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU access
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

## 2. Clone Repository

```bash
git clone https://github.com/ssarunic/dalston.git
cd dalston
```

## 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Required: Database password
POSTGRES_PASSWORD=your-secure-password-here

# Required for diarization: HuggingFace token
# Get one at https://huggingface.co/settings/tokens
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# MinIO credentials (for local S3-compatible storage)
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change-this-password

# S3 configuration (uses MinIO)
S3_BUCKET=dalston-artifacts
S3_REGION=eu-west-2
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=change-this-password
S3_ENDPOINT_URL=http://minio:9000

# Optional: LLM API keys for cleanup engine
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx
```

## 4. Choose Deployment Configuration

### Option A: Minimal Setup (Transcription Only)

Best for: Testing, development, or when you only need basic transcription without word timestamps.

```bash
docker compose --profile local-infra --profile local-object-storage up -d \
  gateway orchestrator \
  stt-batch-prepare stt-batch-transcribe-faster-whisper-base stt-batch-merge
```

Submit jobs with `timestamps_granularity=segment` to skip alignment.

### Option B: Standard Setup (With Word Timestamps)

Best for: Production use requiring word-level timestamps.

```bash
docker compose --profile local-infra --profile local-object-storage up -d \
  gateway orchestrator \
  stt-batch-prepare stt-batch-transcribe-faster-whisper-base stt-batch-align-whisperx-cpu stt-batch-merge
```

### Option C: Full Pipeline (With Speaker Diarization)

Best for: Meeting transcription, interviews, multi-speaker content.

```bash
docker compose --profile local-infra --profile local-object-storage up -d
```

This starts the default CPU-safe stack, including `stt-batch-diarize-pyannote-3.1-cpu` for speaker identification.

### Option D: GPU-Accelerated

Best for: High-throughput production deployments.

```bash
docker compose --profile local-infra --profile local-object-storage --profile gpu up -d
```

This uses GPU variants of transcription, alignment, and diarization engines.

### Option E: Real-Time Streaming Only

Best for: Live transcription without batch processing.

```bash
docker compose --profile local-infra --profile local-object-storage up -d \
  gateway orchestrator \
  stt-rt-transcribe-parakeet-rnnt-0.6b-cpu
```

## 5. Verify Deployment

### Check service health

```bash
# All services running
docker compose ps

# Gateway health
curl http://localhost:8000/health
# Expected: {"status":"healthy"}

# System status
curl http://localhost:8000/v1/system/status
```

### Check individual components

```bash
# Redis connectivity
docker compose exec redis redis-cli ping
# Expected: PONG

# PostgreSQL connectivity
docker compose exec postgres pg_isready -U dalston
# Expected: accepting connections

# View logs
docker compose logs -f gateway
docker compose logs -f orchestrator
```

## 6. Create Admin API Key

```bash
docker compose exec -T gateway python -c "
import asyncio
from dalston.common.redis import get_redis
from dalston.gateway.services.auth import AuthService, Scope
from dalston.db.session import DEFAULT_TENANT_ID

async def create_key():
    redis = await get_redis()
    auth = AuthService(redis)
    key, _ = await auth.create_api_key('Admin', DEFAULT_TENANT_ID, [Scope.ADMIN])
    print('API Key:', key)

asyncio.run(create_key())
"
```

**Save this key** (starts with `dk_`) - it cannot be retrieved later.

## 7. Access Services

| Service | URL | Description |
| ------- | --- | ----------- |
| API | <http://localhost:8000> | REST API |
| API Docs | <http://localhost:8000/docs> | OpenAPI documentation |
| Web Console | <http://localhost:8000> | Management UI (login with API key) |
| MinIO Console | <http://localhost:9001> | Object storage admin |

## 8. Test Transcription

### Submit a test job

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_YOUR_API_KEY" \
  -F "file=@/path/to/audio.mp3" \
  -F "language=en"
```

### Check job status

```bash
curl http://localhost:8000/v1/audio/transcriptions/JOB_ID \
  -H "Authorization: Bearer dk_YOUR_API_KEY"
```

## 9. Scaling for Production

### Scale compute-intensive engines

```bash
# Scale transcription engines
docker compose up -d --scale stt-batch-transcribe-whisper-cpu=2

# Scale multiple engines
docker compose up -d \
  --scale stt-batch-transcribe-whisper-cpu=2 \
  --scale stt-batch-align-whisperx-cpu=2 \
  --scale stt-batch-diarize-pyannote-v31-cpu=2
```

### Resource limits

Add to `docker-compose.override.yml`:

```yaml
services:
  stt-batch-transcribe-whisper-cpu:
    deploy:
      resources:
        limits:
          memory: 16G
        reservations:
          memory: 8G
```

### Multi-GPU assignment

```yaml
services:
  stt-batch-transcribe-whisper-cpu:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['0']
              capabilities: [gpu]

  stt-batch-diarize-pyannote-v31-cpu:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['1']
              capabilities: [gpu]
```

## 10. Setup Reverse Proxy (Production)

For production, use a reverse proxy with SSL. Example with Caddy:

### Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

### Configure Caddy

Create `/etc/caddy/Caddyfile`:

```
transcribe.yourdomain.com {
    reverse_proxy localhost:8000
}
```

### Start Caddy

```bash
sudo systemctl enable caddy
sudo systemctl start caddy
```

Caddy automatically provisions SSL certificates via Let's Encrypt.

## 11. Setup Systemd Service

Create `/etc/systemd/system/dalston.service`:

```ini
[Unit]
Description=Dalston Transcription Server
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/path/to/dalston
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dalston
sudo systemctl start dalston
```

## Daily Operations

### Start services

```bash
docker compose up -d
# Or with systemd:
sudo systemctl start dalston
```

### Stop services

```bash
docker compose down
# Or with systemd:
sudo systemctl stop dalston
```

### View logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f gateway
docker compose logs -f stt-batch-transcribe-whisper-cpu
```

### Update deployment

```bash
git pull
docker compose build
docker compose up -d
```

### Check stream backlogs

```bash
docker compose exec redis redis-cli XINFO GROUPS dalston:stream:faster-whisper
docker compose exec redis redis-cli XINFO GROUPS dalston:stream:whisperx-align
```

## Backup Strategy

### PostgreSQL backup

```bash
# Backup
docker compose exec postgres pg_dump -U dalston dalston > backup.sql

# Restore
cat backup.sql | docker compose exec -T postgres psql -U dalston dalston
```

### MinIO backup

```bash
# Using MinIO client
docker run --rm -v minio-data:/data alpine tar czf - /data > minio-backup.tar.gz
```

### Volume locations

| Volume | Contents |
| ------ | -------- |
| `postgres-data` | Jobs, tasks, API keys, tenants |
| `minio-data` | Audio files, transcripts, models |
| `faster-whisper-cache` | Whisper model cache |
| `whisperx-cache` | WhisperX model cache |
| `pyannote-cache` | PyAnnote model cache |

## Troubleshooting

### Services won't start

```bash
# Check for port conflicts
sudo lsof -i :8000
sudo lsof -i :5432
sudo lsof -i :6379

# Check Docker logs
docker compose logs
```

### GPU not detected

```bash
# Verify NVIDIA driver
nvidia-smi

# Verify Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi

# Check Docker runtime configuration
docker info | grep -i runtime
```

### Out of memory errors

```bash
# Check memory usage
docker stats

# Reduce concurrent processing by scaling down
docker compose up -d --scale stt-batch-transcribe-whisper-cpu=1
```

### Jobs stuck in pending

```bash
# Check orchestrator logs
docker compose logs orchestrator

# Check stream keys/backlogs
docker compose exec redis redis-cli KEYS "dalston:stream:*"
docker compose exec redis redis-cli XINFO GROUPS dalston:stream:faster-whisper

# Verify engines are running
docker compose ps | grep engine
```

### Connection refused errors

```bash
# Ensure services are healthy
docker compose ps

# Check if gateway can reach Redis
docker compose exec gateway python -c "import redis; r=redis.from_url('redis://redis:6379'); print(r.ping())"

# Check if gateway can reach PostgreSQL
docker compose exec gateway python -c "import psycopg2; c=psycopg2.connect('postgresql://dalston:password@postgres/dalston'); print('Connected')"
```

### Model download failures

Models are downloaded on first use. If downloads fail:

```bash
# Check internet connectivity from container
docker compose exec stt-batch-transcribe-whisper-cpu curl -I https://huggingface.co

# Manually download models
docker compose exec stt-batch-transcribe-whisper-cpu python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3')"
```

### HuggingFace token issues

For diarization engines:

```bash
# Verify token is set
docker compose exec stt-batch-diarize-pyannote-v31-cpu printenv HF_TOKEN

# Test token validity
docker compose exec stt-batch-diarize-pyannote-v31-cpu python -c "
from huggingface_hub import HfApi
api = HfApi()
print(api.whoami())
"
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Your Server                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────────────┐   │
│  │  Caddy   │───▶│   Gateway   │───▶│     Orchestrator     │   │
│  │ (proxy)  │    │  (FastAPI)  │    │   (Job Scheduler)    │   │
│  └──────────┘    └─────────────┘    └──────────────────────┘   │
│                         │                      │                 │
│                         ▼                      ▼                 │
│                  ┌──────────┐          ┌──────────────┐         │
│                  │  Redis   │◀────────▶│   Engines    │         │
│                  │ (queues) │          │ (containers) │         │
│                  └──────────┘          └──────────────┘         │
│                         │                      │                 │
│           ┌─────────────┴─────────────┐       │                 │
│           ▼                           ▼       ▼                 │
│    ┌────────────┐              ┌──────────────────┐             │
│    │ PostgreSQL │              │      MinIO       │             │
│    │  (state)   │              │ (file storage)   │             │
│    └────────────┘              └──────────────────┘             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Comparison: Self-Hosted vs AWS

| Aspect | Self-Hosted | AWS |
| ------ | ----------- | --- |
| Storage | MinIO (local) | S3 |
| Cost | Hardware + electricity | Pay-per-use |
| Scaling | Manual | Easier with ASG |
| Maintenance | You manage updates | Managed services available |
| Network | Direct or VPN | Tailscale VPN |
| GPU | Your hardware | EC2 GPU instances |
| Backup | Manual setup | S3 versioning + snapshots |
