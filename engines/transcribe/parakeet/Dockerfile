# NVIDIA Parakeet Transcription Engine
#
# FastConformer RNNT transcription using NeMo toolkit.
# Supports both GPU (CUDA) and CPU inference via build argument.
#
# Build from repo root:
#   GPU (default): docker compose build engine-parakeet
#   CPU:           docker compose --profile parakeet-cpu build engine-parakeet-cpu
#
# Or directly:
#   GPU: docker build -t dalston/engine-parakeet:gpu .
#   CPU: docker build --build-arg DEVICE=cpu -t dalston/engine-parakeet:cpu .

# Build argument to select device (cuda or cpu)
ARG DEVICE=cuda

# Base images for each device type
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base-cuda
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare ARG after FROM (it gets reset)
ARG DEVICE=cuda

# Install system dependencies
# For CUDA base: need to install Python
# For CPU base: Python already installed, just need audio libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && if [ "$DEVICE" = "cuda" ]; then \
        apt-get install -y --no-install-recommends \
            python3.11 \
            python3.11-venv \
            python3-pip \
        && ln -s /usr/bin/python3.11 /usr/bin/python; \
    fi \
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
COPY engines/transcribe/parakeet/requirements.txt .

# Install PyTorch with appropriate backend
# CPU: Use CPU-only wheels (smaller, faster install)
# CUDA: Use default wheels with CUDA support
ARG DEVICE=cuda
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    fi

# Install NeMo and dependencies
# This is a large installation (~2GB+)
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default model (0.6B) at build time
# This makes container startup faster at the cost of larger image size
RUN python -c "\
import nemo.collections.asr as nemo_asr; \
model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-rnnt-0.6b'); \
print('Model nvidia/parakeet-rnnt-0.6b downloaded successfully')"

# Copy engine files
COPY engines/transcribe/parakeet/engine.yaml .
COPY engines/transcribe/parakeet/engine.py .

# Create model cache directory
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
RUN mkdir -p /models

# Set CUDA visible devices (only relevant for GPU builds)
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
