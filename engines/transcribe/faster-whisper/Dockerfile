# Faster-Whisper Transcription Engine
#
# Whisper transcription using CTranslate2.
# Supports both GPU (CUDA) and CPU modes with automatic detection.
#
# Build from repo root:
#   docker compose build engine-faster-whisper
#
# For GPU support, use docker compose --profile gpu

FROM python:3.11-slim

# Install system dependencies (ffmpeg for audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
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
COPY engines/transcribe/faster-whisper/requirements.txt .

# Install faster-whisper and dependencies (CPU-only versions)
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default model (large-v3) at build time
# This makes container startup faster at the cost of larger image size
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')" \
    && echo "Model large-v3 downloaded successfully"

# Copy engine files
COPY engines/transcribe/faster-whisper/engine.yaml .
COPY engines/transcribe/faster-whisper/engine.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
