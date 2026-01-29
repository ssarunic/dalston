# Dalston Docker Composition

## Overview

Dalston runs as a set of Docker containers orchestrated via Docker Compose.

---

## Quick Start

```bash
# Clone repository
git clone https://github.com/your-org/dalston.git
cd dalston

# Copy environment template
cp .env.example .env

# Edit .env with your settings
# - HF_TOKEN (HuggingFace token for pyannote)
# - ANTHROPIC_API_KEY (for LLM cleanup)

# Start all services
docker compose up -d

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
      - REDIS_URL=redis://redis:6379
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
    depends_on:
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
      - REDIS_URL=redis://redis:6379
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
    depends_on:
      - redis
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  # ============================================================
  # PREPARE ENGINES
  # ============================================================
  
  engine-audio-prepare:
    build:
      context: ./engines/prepare/audio-prepare
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=audio-prepare
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
    depends_on:
      - redis
    restart: unless-stopped

  # ============================================================
  # TRANSCRIPTION ENGINES
  # ============================================================

  engine-faster-whisper:
    build:
      context: ./engines/transcribe/faster-whisper
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=faster-whisper
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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

  engine-parakeet:
    build:
      context: ./engines/transcribe/parakeet
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=parakeet
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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

  engine-whisperx-align:
    build:
      context: ./engines/align/whisperx-align
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=whisperx-align
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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

  engine-pyannote:
    build:
      context: ./engines/diarize/pyannote-3.1
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=pyannote-3.1
      - DATA_DIR=/data
      - HF_TOKEN=${HF_TOKEN}
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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

  engine-whisperx-full:
    build:
      context: ./engines/multi/whisperx-full
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=whisperx-full
      - DATA_DIR=/data
      - HF_TOKEN=${HF_TOKEN}
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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
  # ENRICHMENT ENGINES
  # ============================================================

  engine-emotion:
    build:
      context: ./engines/detect/emotion2vec
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=emotion2vec
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
      - dalston-models:/models
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

  engine-events:
    build:
      context: ./engines/detect/panns-events
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=panns-events
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
      - dalston-models:/models
    depends_on:
      - redis
    restart: unless-stopped

  # ============================================================
  # REFINEMENT ENGINES
  # ============================================================

  engine-llm-cleanup:
    build:
      context: ./engines/refine/llm-cleanup
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=llm-cleanup
      - DATA_DIR=/data
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - dalston-data:/data
    depends_on:
      - redis
    restart: unless-stopped

  # ============================================================
  # MERGE ENGINES
  # ============================================================

  engine-merger:
    build:
      context: ./engines/merge/final-merger
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=final-merger
      - DATA_DIR=/data
    volumes:
      - dalston-data:/data
    depends_on:
      - redis
    restart: unless-stopped

volumes:
  dalston-data:
    driver: local
  dalston-models:
    driver: local
  redis-data:
    driver: local
```

---

## Environment Variables

### .env.example

```bash
# HuggingFace token (required for pyannote)
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# LLM providers (optional, for llm-cleanup engine)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx

# Optional: custom model cache location
# MODEL_CACHE_DIR=/path/to/models
```

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
docker compose up -d gateway orchestrator redis engine-audio-prepare engine-faster-whisper engine-merger

# Start with specific engines
docker compose up -d gateway orchestrator redis \
  engine-audio-prepare \
  engine-faster-whisper \
  engine-whisperx-align \
  engine-pyannote \
  engine-merger
```

### Scaling Engines

```bash
# Scale transcription engine (if backlogged)
docker compose up -d --scale engine-faster-whisper=2

# Scale multiple engines
docker compose up -d \
  --scale engine-faster-whisper=2 \
  --scale engine-pyannote=2
```

### Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f gateway
docker compose logs -f engine-faster-whisper

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
docker compose build engine-faster-whisper

# Rebuild and restart
docker compose up -d --build engine-faster-whisper
```

---

## Resource Management

### GPU Assignment

By default, all GPU engines share the same GPU. To assign specific GPUs:

```yaml
engine-faster-whisper:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['0']      # First GPU
            capabilities: [gpu]

engine-pyannote:
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
engine-faster-whisper:
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
| `dalston-data` | Job data, transcripts | No (contains results) |
| `dalston-models` | Cached model weights | Yes (will re-download) |
| `redis-data` | Redis persistence | No (contains job state) |

### Backup

```bash
# Backup job data
docker run --rm -v dalston-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/dalston-data-$(date +%Y%m%d).tar.gz /data

# Backup Redis
docker compose exec redis redis-cli BGSAVE
docker run --rm -v redis-data:/data -v $(pwd):/backup alpine \
  cp /data/dump.rdb /backup/redis-$(date +%Y%m%d).rdb
```

---

## Monitoring

### Health Checks

```bash
# Gateway health
curl http://localhost:8000/health

# System status
curl http://localhost:8000/v1/system/status

# Redis
docker compose exec redis redis-cli ping
```

### Resource Usage

```bash
# Container stats
docker stats

# Specific containers
docker stats gateway orchestrator engine-faster-whisper
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
# Check queue depth
docker compose exec redis redis-cli LLEN dalston:queue:faster-whisper

# Check engine logs
docker compose logs -f engine-faster-whisper

# Check engine is running
docker compose ps engine-faster-whisper
```

### Out of Memory

```bash
# Check memory usage
docker stats --no-stream

# Reduce batch size in engine config
# Or reduce number of concurrent engines
docker compose up -d --scale engine-faster-whisper=1
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
