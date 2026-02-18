# NVIDIA Parakeet Streaming Real-time Engine
#
# Real-time transcription using cache-aware FastConformer streaming.
# Runs a WebSocket server for streaming audio transcription.
# Supports both GPU (CUDA) and CPU inference via build argument.
#
# Build from repo root:
#   GPU (default): docker compose build stt-rt-transcribe-parakeet
#   CPU:           docker compose --profile parakeet-cpu build stt-rt-transcribe-parakeet-cpu
#
# Or directly:
#   GPU: docker build -t dalston/stt-rt-transcribe-parakeet:gpu .
#   CPU: docker build --build-arg DEVICE=cpu -t dalston/stt-rt-transcribe-parakeet:cpu .

# Build argument to select device (cuda or cpu)
ARG DEVICE=cuda

# Base images for each device type
# GPU: Official NVIDIA PyTorch container (requires NGC login: docker login nvcr.io)
#      PyTorch 2.5.0, CUDA 12.6, cuDNN 9 - all versions aligned by NVIDIA
# CPU: Python slim image for lightweight CPU-only inference
FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare ARG after FROM (it gets reset)
ARG DEVICE=cuda

# Install system dependencies
# For NGC PyTorch base: Python/CUDA already installed, just need audio libs
# For CPU base: Python already installed, just need audio libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston realtime SDK
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-rt/parakeet/requirements.txt .

# Install PyTorch for CPU builds only (GPU base already has PyTorch)
ARG DEVICE=cuda
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download models at build time (skip on Mac cross-compile to avoid QEMU crashes)
ARG MODEL_VARIANT=0.6b
ARG SKIP_MODEL_DOWNLOAD=false
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
        python -c "\
import nemo.collections.asr as nemo_asr; \
model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-rnnt-${MODEL_VARIANT}'); \
print('Model nvidia/parakeet-rnnt-${MODEL_VARIANT} downloaded successfully')" && \
        python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)" && \
        echo "Silero VAD model downloaded successfully"; \
    else \
        echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; \
    fi

# Copy engine files
ARG MODEL_VARIANT=0.6b
COPY engines/stt-rt/parakeet/variants/rnnt-${MODEL_VARIANT}.yaml ./engine.yaml
COPY engines/stt-rt/parakeet/engine.py .

# Create model cache directory
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Default environment variables
ENV WORKER_ID=stt-rt-transcribe-parakeet
ENV WORKER_PORT=9000
ENV MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379

# Set CUDA visible devices (only relevant for GPU builds)
ENV NVIDIA_VISIBLE_DEVICES=all

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
