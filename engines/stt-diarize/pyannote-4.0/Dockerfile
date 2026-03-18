# Pyannote 4.0 Speaker Diarization Engine
#
# Identifies who speaks when in audio files using pyannote-audio 4.0.
# Features the new Community-1 pipeline with VBx clustering.
# Requires HuggingFace token (HF_TOKEN) for accessing gated models.
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-diarize-pyannote:latest -f engines/stt-diarize/pyannote-4.0/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-diarize-pyannote:latest -f engines/stt-diarize/pyannote-4.0/Dockerfile .
#
# Note: torchcodec (required by pyannote 4.0) only has x86_64 Linux wheels,
# so this image must be built for linux/amd64 platform on ARM Macs.
# macOS ARM64 wheels exist but Docker runs Linux, not macOS.

ARG DEVICE=cpu

# --- CPU base ---
FROM --platform=linux/amd64 python:3.14-slim AS base-cpu

# --- GPU base ---
FROM --platform=linux/amd64 nvidia/cuda:12.6.0-runtime-ubuntu24.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# --- Common ---
FROM base-${DEVICE} AS base
ARG DEVICE=cpu
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (ffmpeg for audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-diarize/pyannote-4.0/requirements.txt .

# Install pyannote 4.0 and dependencies
# GPU: use --extra-index-url so pip resolves CUDA-enabled torch wheels
#       that satisfy pyannote's torch>=2.8.0 requirement in one pass.
#       cu126 matches the nvidia/cuda:12.6 base image.
RUN if [ "$DEVICE" = "cuda" ]; then \
        pip install --no-cache-dir --break-system-packages \
            --extra-index-url https://download.pytorch.org/whl/cu126 \
            -r requirements.txt; \
    else \
        pip install --no-cache-dir --break-system-packages -r requirements.txt; \
    fi

# Copy engine files
COPY engines/stt-diarize/pyannote-4.0/engine.yaml .
COPY engines/stt-diarize/pyannote-4.0/engine.py .

# M39: Standardized model cache directories
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
RUN mkdir -p /models/huggingface /models/torch

ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
