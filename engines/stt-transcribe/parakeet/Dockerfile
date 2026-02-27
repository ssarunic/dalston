# NVIDIA Parakeet Transcription Engine (Runtime)
#
# FastConformer transcription with CTC or TDT decoders using NeMo toolkit.
# M36: Single runtime image that loads any Parakeet model variant on demand.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-nemo
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-nemo:1.0.0 -f engines/stt-transcribe/parakeet/Dockerfile .
#
# Models are downloaded at runtime to /models/nemo volume.
# Supported models: nvidia/parakeet-ctc-0.6b, nvidia/parakeet-ctc-1.1b,
#                   nvidia/parakeet-tdt-0.6b-v3, nvidia/parakeet-tdt-1.1b

# Build argument to select device (cuda or cpu)
ARG DEVICE=cuda

# Base images for each device type
# GPU: Official NVIDIA PyTorch container (requires NGC login: docker login nvcr.io)
# CPU: Python slim image for lightweight CPU-only builds
FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare ARG after FROM (they get reset)
ARG DEVICE=cuda

# Install system dependencies
# Note: build-essential and cmake are needed on ARM64 to compile texterrors (NeMo dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    cmake \
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
COPY engines/stt-transcribe/parakeet/requirements.txt .

# Install PyTorch for CPU builds only (GPU base already provides torch)
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Model cache directory - runtime-specific subdirectory
# Models are downloaded at runtime on first use
ENV NEMO_CACHE=/models/nemo
ENV HF_HOME=/models/nemo
ENV TORCH_HOME=/models/nemo
RUN mkdir -p /models/nemo

# Copy runtime-level engine.yaml (not variant-specific)
COPY engines/stt-transcribe/parakeet/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-transcribe/parakeet/engine.py .

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
