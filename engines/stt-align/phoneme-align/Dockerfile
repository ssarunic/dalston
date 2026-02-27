# Phoneme Forced Alignment Engine
#
# CTC forced alignment using wav2vec2 models for word-level timestamps.
# Supports both GPU (CUDA) and CPU modes with automatic detection.
#
# Build from repo root:
#   docker compose build stt-batch-align-phoneme-cpu
#
# For GPU support, use docker compose --profile gpu

FROM python:3.11-slim

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
COPY engines/stt-align/phoneme-align/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine files
COPY engines/stt-align/phoneme-align/engine.yaml .
COPY engines/stt-align/phoneme-align/engine.py .
COPY engines/stt-align/phoneme-align/align.py .
COPY engines/stt-align/phoneme-align/ctc_forced_align.py .
COPY engines/stt-align/phoneme-align/model_loader.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
