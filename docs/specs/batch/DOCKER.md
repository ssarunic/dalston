# Dalston Docker Composition

## Overview

Dalston runs as a set of Docker containers orchestrated via Docker Compose.

### Storage Architecture

| Component | Storage | Purpose |
|-----------|---------|---------|
| PostgreSQL | Container volume | Persistent business data |
| Redis | Container volume | Ephemeral queues, sessions |
| S3 | External service | Audio files, transcripts, models |
| Local temp | Container `/tmp` | In-flight processing only |

---

## Quick Start

```bash
# Clone repository
git clone https://github.com/your-org/dalston.git
cd dalston

# Copy environment template
cp .env.example .env

# Edit .env with your settings
# - DATABASE_URL (PostgreSQL connection)
# - DALSTON_S3_BUCKET, DALSTON_S3_REGION, AWS credentials
# - HF_TOKEN (HuggingFace token for pyannote)
# - ANTHROPIC_API_KEY (for LLM cleanup)

# Start all services
docker compose up -d

# Run database migrations
docker compose exec gateway python -m dalston.db.migrate

# Check status
docker compose ps

# View logs
docker compose logs -f gateway
```

---

## docker-compose.yml

```yaml
version: "3.8"

services:

  # ============================================================
  # CORE SERVICES
  # ============================================================

  gateway:
    build:
      context: .
      dockerfile: docker/Dockerfile.gateway
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://dalston:${POSTGRES_PASSWORD}@postgres:5432/dalston
      - REDIS_URL=redis://redis:6379
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    depends_on:
      - postgres
      - redis
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  orchestrator:
    build:
      context: .
      dockerfile: docker/Dockerfile.orchestrator
    environment:
      - DATABASE_URL=postgresql://dalston:${POSTGRES_PASSWORD}@postgres:5432/dalston
      - REDIS_URL=redis://redis:6379
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=dalston
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=dalston
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dalston"]
      interval: 10s
      timeout: 5s
      retries: 3

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly no
    ports:
      - "6379:6379"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  # ============================================================
  # PREPARE ENGINES
  # ============================================================

  stt-batch-prepare:
    build:
      context: ./engines/prepare/audio-prepare
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=audio-prepare
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs:
      - /tmp/dalston:size=10G
    depends_on:
      - redis
    restart: unless-stopped

  # ============================================================
  # TRANSCRIPTION ENGINES
  # ============================================================

  stt-batch-transcribe-whisper-cpu:
    build:
      context: ./engines/transcribe/faster-whisper
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=faster-whisper
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs:
      - /tmp/dalston:size=10G
    volumes:
      - model-cache:/models
    depends_on:
      - redis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  stt-batch-transcribe-parakeet:
    build:
      context: ./engines/transcribe/parakeet
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=parakeet
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs:
      - /tmp/dalston:size=10G
    volumes:
      - model-cache:/models
    depends_on:
      - redis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  # ============================================================
  # ALIGNMENT ENGINES
  # ============================================================

  stt-batch-align-phoneme-cpu:
    build:
      context: ./engines/stt-align/phoneme-align
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=phoneme-align
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs:
      - /tmp/dalston:size=10G
    volumes:
      - model-cache:/models
    depends_on:
      - redis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  # ============================================================
  # DIARIZATION ENGINES
  # ============================================================

  stt-batch-diarize-pyannote-v40-cpu:
    build:
      context: ./engines/diarize/pyannote-4.0
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=pyannote-4.0
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - HF_TOKEN=${HF_TOKEN}
    tmpfs:
      - /tmp/dalston:size=10G
    volumes:
      - model-cache:/models
    depends_on:
      - redis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  # ============================================================
  # MULTI-STAGE ENGINES
  # ============================================================

  stt-batch-transcribe-whisperx-full:
    build:
      context: ./engines/multi/whisperx-full
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=whisperx-full
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - HF_TOKEN=${HF_TOKEN}
    tmpfs:
      - /tmp/dalston:size=10G
    volumes:
      - model-cache:/models
    depends_on:
      - redis
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  # ============================================================
  # MERGE ENGINES
  # ============================================================

  stt-batch-merge:
    build:
      context: ./engines/merge/final-merger
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=final-merger
      - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
      - DALSTON_S3_REGION=${DALSTON_S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs:
      - /tmp/dalston:size=1G
    depends_on:
      - redis
    restart: unless-stopped

volumes:
  postgres-data:
    driver: local
  model-cache:
    driver: local
```

---

## Environment Variables

### .env.example

```bash
# PostgreSQL
POSTGRES_PASSWORD=your-secure-password

# S3 Storage (required)
DALSTON_S3_BUCKET=dalston-artifacts
DALSTON_S3_REGION=eu-west-2
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# HuggingFace token (required for pyannote)
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# LLM providers (optional, for llm-cleanup engine)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx
```

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `DALSTON_S3_BUCKET` | Yes | S3 bucket for artifacts |
| `DALSTON_S3_REGION` | Yes | S3 region (e.g., `eu-west-2`) |
| `AWS_ACCESS_KEY_ID` | Yes | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS secret key |
| `HF_TOKEN` | For diarization | HuggingFace token for pyannote |
| `ANTHROPIC_API_KEY` | For LLM cleanup | Anthropic API key |
| `OPENAI_API_KEY` | For LLM cleanup | OpenAI API key (alternative) |

---

## Dockerfiles

### docker/Dockerfile.gateway

```dockerfile
# Build web assets
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Final image
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application
COPY dalston/ ./dalston/

# Copy web assets
COPY --from=web-builder /web/dist ./web/dist

EXPOSE 8000
CMD ["uvicorn", "dalston.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker/Dockerfile.orchestrator

```dockerfile
FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application
COPY dalston/ ./dalston/

CMD ["python", "-m", "dalston.orchestrator.main"]
```

### docker/Dockerfile.base (Engine Base Image)

```dockerfile
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

# Install Python and system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# Install engine SDK
COPY dalston/engine_sdk /app/dalston/engine_sdk
RUN pip install --no-cache-dir /app/dalston/engine_sdk

WORKDIR /app
```

### engines/transcribe/faster-whisper/Dockerfile

```dockerfile
FROM dalston/engine-base:latest

# Install faster-whisper and dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine
COPY engine.yaml /app/
COPY engine.py /app/

# Pre-download default model
ENV HF_HOME=/models
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu')"

CMD ["python", "/app/engine.py"]
```

---

## Operations

### Starting Services

```bash
# Start all
docker compose up -d

# Start core only (minimal)
docker compose up -d postgres redis gateway orchestrator stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge

# Start with specific engines
docker compose up -d postgres redis gateway orchestrator \
  stt-batch-prepare \
  stt-batch-transcribe-whisper-cpu \
  stt-batch-align-whisperx-cpu \
  stt-batch-diarize-pyannote-v40-cpu \
  stt-batch-merge
```

### Scaling Engines

```bash
# Scale transcription engine (if backlogged)
docker compose up -d --scale stt-batch-transcribe-whisper-cpu=2

# Scale multiple engines
docker compose up -d \
  --scale stt-batch-transcribe-whisper-cpu=2 \
  --scale stt-batch-diarize-pyannote-v40-cpu=2
```

### Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f gateway
docker compose logs -f stt-batch-transcribe-whisper-cpu

# Last 100 lines
docker compose logs --tail=100 orchestrator
```

### Stopping Services

```bash
# Stop all
docker compose down

# Stop but keep volumes
docker compose down

# Stop and remove volumes (DELETES DATA)
docker compose down -v
```

### Rebuilding

```bash
# Rebuild all
docker compose build

# Rebuild specific service
docker compose build stt-batch-transcribe-whisper-cpu

# Rebuild and restart
docker compose up -d --build stt-batch-transcribe-whisper-cpu
```

---

## Resource Management

### GPU Assignment

By default, all GPU engines share the same GPU. To assign specific GPUs:

```yaml
stt-batch-transcribe-whisper-cpu:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['0']      # First GPU
            capabilities: [gpu]

stt-batch-diarize-pyannote-v40-cpu:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['1']      # Second GPU
            capabilities: [gpu]
```

### Memory Limits

```yaml
stt-batch-transcribe-whisper-cpu:
  deploy:
    resources:
      limits:
        memory: 16G
      reservations:
        memory: 8G
        devices:
          - capabilities: [gpu]
```

---

## Volumes

| Volume | Purpose | Can Delete? |
|--------|---------|-------------|
| `postgres-data` | PostgreSQL database | No (contains job/task state) |
| `model-cache` | Cached model weights | Yes (will re-download from S3) |

**Note**: Redis runs without persistence (`--appendonly no`) since it only stores ephemeral data (queues, session state). All durable data is in PostgreSQL and S3.

### Backup

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U dalston dalston > dalston-$(date +%Y%m%d).sql

# Restore PostgreSQL
docker compose exec -T postgres psql -U dalston dalston < dalston-backup.sql

# S3 artifacts are backed up via S3 versioning or cross-region replication
```

### S3 Bucket Setup

Create the S3 bucket with recommended settings:

```bash
# Create bucket
aws s3 mb s3://dalston-artifacts --region eu-west-2

# Enable versioning (recommended)
aws s3api put-bucket-versioning \
  --bucket dalston-artifacts \
  --versioning-configuration Status=Enabled

# Set lifecycle rule for old sessions
aws s3api put-bucket-lifecycle-configuration \
  --bucket dalston-artifacts \
  --lifecycle-configuration '{
    "Rules": [
      {
        "ID": "cleanup-old-sessions",
        "Prefix": "sessions/",
        "Status": "Enabled",
        "Expiration": { "Days": 7 }
      }
    ]
  }'
```

---

## Monitoring

### Health Checks

```bash
# Gateway health
curl http://localhost:8000/health

# System status
curl http://localhost:8000/v1/system/status

# PostgreSQL
docker compose exec postgres pg_isready -U dalston

# Redis
docker compose exec redis redis-cli ping

# S3 connectivity
aws s3 ls s3://${DALSTON_S3_BUCKET}/ --region ${DALSTON_S3_REGION}
```

### Resource Usage

```bash
# Container stats
docker stats

# Specific containers
docker stats gateway orchestrator stt-batch-transcribe-whisper-cpu
```

---

## Troubleshooting

### GPU Not Available

```bash
# Check NVIDIA runtime
docker run --rm --gpus all nvidia/cuda:12.1-base nvidia-smi

# If not working, install nvidia-container-toolkit
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

### Engine Not Processing

```bash
# Check stream backlog (lag field in consumer-group info)
docker compose exec redis redis-cli XINFO GROUPS dalston:stream:faster-whisper

# Check engine logs
docker compose logs -f stt-batch-transcribe-whisper-cpu

# Check engine is running
docker compose ps stt-batch-transcribe-whisper-cpu
```

### Out of Memory

```bash
# Check memory usage
docker stats --no-stream

# Reduce batch size in engine config
# Or reduce number of concurrent engines
docker compose up -d --scale stt-batch-transcribe-whisper-cpu=1
```

### Redis Connection Errors

```bash
# Check Redis is running
docker compose ps redis

# Check Redis logs
docker compose logs redis

# Test connection
docker compose exec redis redis-cli ping
```

### PostgreSQL Connection Errors

```bash
# Check PostgreSQL is running
docker compose ps postgres

# Check PostgreSQL logs
docker compose logs postgres

# Test connection
docker compose exec postgres psql -U dalston -c "SELECT 1"

# Check database exists
docker compose exec postgres psql -U dalston -l
```

### S3 Connection Errors

```bash
# Check credentials are set
echo "Bucket: $DALSTON_S3_BUCKET, Region: $DALSTON_S3_REGION"

# Test S3 access
aws s3 ls s3://${DALSTON_S3_BUCKET}/ --region ${DALSTON_S3_REGION}

# Check bucket policy allows access
aws s3api get-bucket-policy --bucket ${DALSTON_S3_BUCKET}

# Test write access
echo "test" | aws s3 cp - s3://${DALSTON_S3_BUCKET}/test.txt
aws s3 rm s3://${DALSTON_S3_BUCKET}/test.txt
```
