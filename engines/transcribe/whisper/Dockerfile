# Whisper Transcription Engine (Parameterized)
#
# Whisper transcription using CTranslate2 (faster-whisper).
# Supports multiple model variants via MODEL_SIZE build arg.
#
# Build specific variant:
#   docker compose build stt-batch-transcribe-whisper-base
#   docker compose build stt-batch-transcribe-whisper-large-v3
#   docker compose build stt-batch-transcribe-whisper-large-v3-turbo
#
# Or manually:
#   docker build --build-arg MODEL_SIZE=base -t dalston/whisper-base .
#   docker build --build-arg MODEL_SIZE=large-v3 -t dalston/whisper-large-v3 .

ARG MODEL_SIZE=large-v3

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
COPY engines/transcribe/whisper/requirements.txt .

# Install faster-whisper and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/transcribe/whisper/engine.py .

# Copy variant-specific config to standard location
# Re-declare ARG after FROM to use it
ARG MODEL_SIZE
COPY engines/transcribe/whisper/variants/${MODEL_SIZE}.yaml /etc/dalston/engine.yaml

# Pre-download the model at build time for faster startup
# This makes container startup faster at the cost of larger image size
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${MODEL_SIZE}', device='cpu', compute_type='int8')" \
    && echo "Model ${MODEL_SIZE} downloaded successfully"

# Set model variant via environment
ENV MODEL_SIZE=${MODEL_SIZE}
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
