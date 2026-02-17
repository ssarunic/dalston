# NVIDIA Parakeet Transcription Engine
#
# FastConformer RNNT transcription using NeMo toolkit.
# Parameterized by MODEL_SIZE to build variant-specific containers.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-parakeet-0.6b
#   docker compose build stt-batch-transcribe-parakeet-1.1b
#
# Or directly:
#   docker build --build-arg MODEL_SIZE=0.6b -t dalston/stt-batch-transcribe-parakeet-0.6b:1.0.0 .
#   docker build --build-arg MODEL_SIZE=1.1b -t dalston/stt-batch-transcribe-parakeet-1.1b:1.0.0 .

# Build argument to select model size
ARG MODEL_SIZE=0.6b

# Use CUDA base image for GPU support
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Re-declare ARG after FROM (it gets reset)
ARG MODEL_SIZE=0.6b

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
    && ln -s /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
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

# Install NeMo and dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Pre-download the model at build time for faster container startup
ARG MODEL_SIZE=0.6b
RUN python -c "\
import nemo.collections.asr as nemo_asr; \
model_map = {'0.6b': 'nvidia/parakeet-rnnt-0.6b', '1.1b': 'nvidia/parakeet-rnnt-1.1b'}; \
model_id = model_map['${MODEL_SIZE}']; \
model = nemo_asr.models.ASRModel.from_pretrained(model_id); \
print(f'Model {model_id} downloaded successfully')"

# Copy engine files
COPY engines/transcribe/parakeet/variants/${MODEL_SIZE}.yaml /etc/dalston/engine.yaml
COPY engines/transcribe/parakeet/engine.py .

# Create model cache directory
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
RUN mkdir -p /models

# Set MODEL_SIZE environment variable for the engine
ENV MODEL_SIZE=${MODEL_SIZE}

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
