# Faster Whisper Transcription Engine (Runtime)
#
# Whisper transcription using CTranslate2 (faster-whisper).
# M36: Single runtime image that loads any Whisper model variant on demand.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-faster-whisper
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-faster-whisper:1.0.0 -f engines/stt-transcribe/faster-whisper/Dockerfile .
#
# Models are downloaded at runtime to /models/faster-whisper volume.
# Supported models: tiny, base, small, medium, large-v2, large-v3, large-v3-turbo

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
COPY engines/stt-transcribe/faster-whisper/requirements.txt .

# Install faster-whisper and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-transcribe/faster-whisper/engine.py .

# Copy runtime-level engine.yaml (not variant-specific)
COPY engines/stt-transcribe/faster-whisper/engine.yaml /etc/dalston/engine.yaml

# Model cache directory - runtime-specific subdirectory
# Models are downloaded at runtime on first use
ENV HF_HOME=/models/faster-whisper
ENV TORCH_HOME=/models/faster-whisper
RUN mkdir -p /models/faster-whisper

CMD ["python", "engine.py"]
