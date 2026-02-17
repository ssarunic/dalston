# Mistral Voxtral Streaming Real-time Engine
#
# Real-time transcription using Voxtral-Mini-4B-Realtime.
# Runs a WebSocket server for streaming audio transcription.
# GPU required for realtime latency requirements.
#
# Build from repo root:
#   docker compose build stt-rt-transcribe-voxtral-mini-4b
#
# Or directly:
#   docker build -t dalston/stt-rt-transcribe-voxtral-mini-4b:1.0.0 .

# Build argument to select model size
ARG MODEL_SIZE=mini-4b

# Use CUDA base image for GPU support (required for realtime)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Re-declare ARG after FROM
ARG MODEL_SIZE=mini-4b

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
COPY engines/realtime/voxtral-streaming/requirements.txt .

# Install dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Pre-download the model at build time for faster container startup
ARG MODEL_SIZE=mini-4b
RUN python -c "\
import os; \
os.environ['HF_HOME'] = '/models'; \
from transformers import AutoProcessor, VoxtralRealtimeForConditionalGeneration; \
model_map = {'mini-4b': 'mistralai/Voxtral-Mini-4B-Realtime-2602'}; \
model_id = model_map['${MODEL_SIZE}']; \
print(f'Downloading model {model_id}...'); \
AutoProcessor.from_pretrained(model_id); \
VoxtralRealtimeForConditionalGeneration.from_pretrained(model_id); \
print(f'Model {model_id} downloaded successfully')"

# Download Silero VAD model
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)" \
    && echo "Silero VAD model downloaded successfully"

# Copy engine files
COPY engines/realtime/voxtral-streaming/variants/${MODEL_SIZE}.yaml /etc/dalston/engine.yaml
COPY engines/realtime/voxtral-streaming/engine.py .

# Create model cache directory
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Default environment variables
ENV WORKER_ID=stt-rt-transcribe-voxtral
ENV WORKER_PORT=9000
ENV MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379
ENV MODEL_SIZE=${MODEL_SIZE}
ENV TRANSCRIPTION_DELAY_MS=480

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
