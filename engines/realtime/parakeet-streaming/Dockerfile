# NVIDIA Parakeet Streaming Real-time Engine
#
# Real-time transcription using cache-aware FastConformer streaming.
# Runs a WebSocket server for streaming audio transcription.
# Requires NVIDIA GPU with CUDA support.
#
# Build from repo root:
#   docker compose build realtime-parakeet
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

# Install the dalston realtime SDK
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/realtime/parakeet-streaming/requirements.txt .

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default model (0.6B) at build time
RUN python -c "\
import nemo.collections.asr as nemo_asr; \
model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-rnnt-0.6b'); \
print('Model nvidia/parakeet-rnnt-0.6b downloaded successfully')"

# Download Silero VAD model
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', force_reload=False)" \
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
ENV WORKER_ID=realtime-parakeet
ENV WORKER_PORT=9000
ENV MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
