# NVIDIA Parakeet ONNX Transcription Engine
#
# Lightweight engine using ONNX Runtime for Parakeet CTC inference.
# M41: ~1GB image vs ~12GB for the NeMo-based engine.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-nemo-onnx
#
# Or directly:
#   CPU:  docker build -t dalston/stt-batch-transcribe-nemo-onnx:1.0.0 -f engines/stt-transcribe/parakeet-onnx/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-batch-transcribe-nemo-onnx:1.0.0 -f engines/stt-transcribe/parakeet-onnx/Dockerfile .
#
# Models are downloaded at runtime to /models/onnx volume by onnx-asr (via HuggingFace Hub).
# Supported models: nvidia/parakeet-ctc-0.6b, nvidia/parakeet-ctc-1.1b

# Build argument to select device (cuda or cpu)
ARG DEVICE=cpu

# GPU: CUDA runtime image (no PyTorch needed - just ONNX Runtime GPU)
FROM nvidia/cuda:12.6.0-runtime-ubuntu22.04 AS base-cuda
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

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-transcribe/parakeet-onnx/requirements.txt .

# Install ONNX Runtime (GPU variant for CUDA builds)
RUN if [ "$DEVICE" = "cuda" ]; then \
        pip install --no-cache-dir onnxruntime-gpu; \
    fi

# Install engine dependencies
RUN pip install --no-cache-dir -r requirements.txt

# M39: Standardized model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface /models/onnx

# Copy engine files
COPY engines/stt-transcribe/parakeet-onnx/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-transcribe/parakeet-onnx/engine.py .

# Set CUDA visible devices (only relevant for GPU builds)
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
