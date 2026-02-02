# Pyannote 4.0 Speaker Diarization Engine
#
# Identifies who speaks when in audio files using pyannote-audio 4.0.
# Features the new Community-1 pipeline with VBx clustering.
# Requires HuggingFace token (HF_TOKEN) for accessing gated models.
#
# Build from repo root:
#   docker compose build engine-pyannote-4.0
#
# For GPU support, use docker compose --profile gpu
#
# Note: torchcodec (required by pyannote 4.0) only has x86_64 Linux wheels,
# so this image must be built for linux/amd64 platform on ARM Macs.
# macOS ARM64 wheels exist but Docker runs Linux, not macOS.

# Python 3.10+ required for pyannote.audio 4.0
FROM --platform=linux/amd64 python:3.11-slim

# Install system dependencies (ffmpeg for audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
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
COPY engines/diarize/pyannote-4.0/requirements.txt .

# Install pyannote 4.0 and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine files
COPY engines/diarize/pyannote-4.0/engine.yaml .
COPY engines/diarize/pyannote-4.0/engine.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
