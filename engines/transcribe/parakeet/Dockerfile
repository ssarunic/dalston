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
# Build tools needed for C extensions (ctc_segmentation, texterrors)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    gcc \
    g++ \
    && if [ "$DEVICE" = "cuda" ]; then \
        apt-get install -y --no-install-recommends \
            python3.11 \
            python3.11-venv \
            python3.11-dev \
            python3-pip \
        && ln -s /usr/bin/python3.11 /usr/bin/python; \
    else \
        apt-get install -y --no-install-recommends \
            python3-dev; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to ensure it finds pre-built wheels correctly
# Use python -m pip to ensure we use Python 3.11's pip (not system Python 3.10)
RUN python -m pip install --no-cache-dir --upgrade pip

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN python -m pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/transcribe/parakeet/requirements.txt .

# Install PyTorch with appropriate backend
# CPU: Use CPU-only wheels (smaller, faster install)
# CUDA: Use default wheels with CUDA support
ARG DEVICE=cuda
RUN if [ "$DEVICE" = "cpu" ]; then \
        python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    fi

# Install NeMo and dependencies
# This is a large installation (~2GB+)
RUN python -m pip install --no-cache-dir -r requirements.txt

# Pre-download the default model (110M TDT_CTC) at build time
# This makes container startup faster at the cost of larger image size
# Using 110M model for lower memory requirements on CPU
RUN python -c "\
import nemo.collections.asr as nemo_asr; \
model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-tdt_ctc-110m'); \
print('Model nvidia/parakeet-tdt_ctc-110m downloaded successfully')"

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
