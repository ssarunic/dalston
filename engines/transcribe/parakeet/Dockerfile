# NVIDIA Parakeet Transcription Engine
#
# FastConformer RNNT transcription using NeMo toolkit.
# Requires NVIDIA GPU with CUDA support.
#
# Build from repo root:
#   docker compose build engine-parakeet
#
# Note: This image is GPU-only. No CPU fallback is provided.

# Use NVIDIA CUDA base image for GPU support
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Install Python and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/python3.11 /usr/bin/python

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

# Set CUDA visible devices (can be overridden at runtime)
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
