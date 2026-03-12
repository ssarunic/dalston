# NVIDIA Parakeet ONNX Streaming Real-time Engine
#
# Real-time transcription using ONNX Runtime for Parakeet CTC inference.
# Runs a WebSocket server for streaming audio transcription.
# Lightweight: no NeMo/PyTorch required for CPU inference.
#
# Build from repo root:
#   docker compose build stt-rt-transcribe-parakeet-onnx-ctc-0.6b
#
# Or directly:
#   CPU: docker build -t dalston/stt-rt-transcribe-parakeet-onnx:cpu -f engines/stt-rt/onnx/Dockerfile .
#   GPU: docker build --build-arg DEVICE=cuda -t dalston/stt-rt-transcribe-parakeet-onnx:gpu -f engines/stt-rt/onnx/Dockerfile .

# Build argument to select device (cuda or cpu)
ARG DEVICE=cpu

# GPU: CUDA engine_id image
FROM nvidia/cuda:12.6.0-engine_id-ubuntu22.04 AS base-cuda
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf python3 /usr/bin/python

# CPU: Python slim image
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare ARG after FROM (it gets reset)
ARG DEVICE=cpu

# Install system dependencies
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
COPY engines/stt-rt/onnx/requirements.txt .

# Install PyTorch for CPU builds (needed for Silero VAD)
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi

# Install ONNX Runtime GPU variant for CUDA builds
RUN if [ "$DEVICE" = "cuda" ]; then \
        pip install --no-cache-dir onnxruntime-gpu; \
    fi

# Install engine dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine files
# M50: Use main engine.yaml for structured capabilities
COPY engines/stt-rt/onnx/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-rt/onnx/engine.py .

# M39: Standardized model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface /models/onnx

# Default environment variables
ENV DALSTON_INSTANCE=stt-rt-transcribe-parakeet-onnx
ENV DALSTON_WORKER_PORT=9000
ENV DALSTON_MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379

# Set CUDA visible devices (only relevant for GPU builds)
ENV NVIDIA_VISIBLE_DEVICES=all

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
