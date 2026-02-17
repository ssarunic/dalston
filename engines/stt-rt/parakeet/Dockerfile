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

# Install the dalston realtime SDK
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/realtime/parakeet-streaming/requirements.txt .

# Install PyTorch with appropriate backend
# CPU: Use CPU-only wheels (smaller, faster install)
# CUDA: Use default wheels with CUDA support
# torchaudio is installed from regular PyPI (works for both)
ARG DEVICE=cuda
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    fi && \
    pip install --no-cache-dir torchaudio

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default model (0.6B) at build time
RUN python -c "\
import nemo.collections.asr as nemo_asr; \
model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-rnnt-0.6b'); \
print('Model nvidia/parakeet-rnnt-0.6b downloaded successfully')"

# Download Silero VAD model
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)" \
    && echo "Silero VAD model downloaded successfully"

# Copy engine files
COPY engines/realtime/parakeet-streaming/engine.yaml .
COPY engines/realtime/parakeet-streaming/engine.py .

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
