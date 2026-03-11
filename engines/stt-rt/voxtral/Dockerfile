# Voxtral Realtime Engine (vLLM)
#
# Real-time transcription using Voxtral-Mini-4B-Realtime on vLLM.
# Runs a WebSocket server for streaming audio transcription.
# GPU required for vLLM.
#
# Legacy split RT image kept for targeted testing.
# Note: `stt-rt-transcribe-voxtral-mini-4b` was removed from default
# `docker-compose.yml` on 2026-03-11.
# Build directly from repo root:
#   docker build -t dalston/stt-rt-transcribe-voxtral-mini-4b:1.0.0 .

# Build argument to select model variant
ARG MODEL_VARIANT=mini-4b

# Use CUDA base image for GPU support (required for realtime)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Re-declare ARG after FROM
ARG MODEL_VARIANT=mini-4b

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    gcc \
    g++ \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    && ln -s /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN python -m pip install --no-cache-dir --upgrade pip

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston realtime SDK
RUN python -m pip install --no-cache-dir -e ".[realtime-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-rt/voxtral/requirements.txt .

# Install dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Download Silero VAD model
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)" \
    && echo "Silero VAD model downloaded successfully"

# Copy engine files
# M50: Use main engine.yaml for structured capabilities
COPY engines/stt-rt/voxtral/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-rt/voxtral/engine.py .

# M39: Standardized model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
RUN mkdir -p /models/huggingface /models/torch

# Default environment variables
ENV DALSTON_INSTANCE=stt-rt-transcribe-voxtral
ENV DALSTON_WORKER_PORT=9000
ENV DALSTON_MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379
ENV DALSTON_MODEL_VARIANT=${MODEL_VARIANT}

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
