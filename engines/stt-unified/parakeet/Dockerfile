# Unified Parakeet Engine (M63)
#
# One process, one NeMo model, both batch and realtime interfaces.
# Replaces the separate stt-batch-transcribe-nemo + stt-rt-nemo services.
#
# Build from repo root:
#   GPU: docker compose build stt-unified-nemo
#   CPU: docker compose build stt-unified-nemo-cpu
#
# Or directly:
#   GPU: docker build -t dalston/stt-unified-nemo:1.0.0 -f engines/stt-unified/parakeet/Dockerfile .
#   CPU: docker build --build-arg DEVICE=cpu -t dalston/stt-unified-nemo-cpu:1.0.0 -f engines/stt-unified/parakeet/Dockerfile .

ARG DEVICE=cuda

FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda
FROM python:3.11-slim AS base-cpu

FROM base-${DEVICE} AS base

ARG DEVICE=cuda

# System dependencies (superset of batch + RT)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Install dalston package with both SDKs
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk,realtime-sdk]"

# Install PyTorch for CPU builds only (GPU base already provides torch)
WORKDIR /engine
COPY engines/stt-unified/parakeet/requirements.txt .
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi
RUN pip install --no-cache-dir -r requirements.txt

# Model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
RUN mkdir -p /models/huggingface /models/torch /models/nemo

# Copy engine files preserving the directory structure the runner expects
WORKDIR /app
COPY engines/stt-unified/parakeet/ engines/stt-unified/parakeet/
COPY engines/stt-transcribe/parakeet/engine.py engines/stt-transcribe/parakeet/engine.py
COPY engines/stt-rt/parakeet/engine.py engines/stt-rt/parakeet/engine.py

# RT capabilities (read by realtime SDK at registration time)
COPY engines/stt-rt/parakeet/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all
ENV DALSTON_UNIFIED_ENGINE_ENABLED=true

EXPOSE 9000

CMD ["python", "engines/stt-unified/parakeet/runner.py"]
