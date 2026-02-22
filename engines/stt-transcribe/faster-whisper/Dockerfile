# Faster Whisper Transcription Engine (Parameterized)
#
# Whisper transcription using CTranslate2 (faster-whisper).
# Supports multiple model variants via MODEL_VARIANT build arg.
#
# Build specific variant:
#   docker compose build stt-batch-transcribe-faster-whisper-base
#   docker compose build stt-batch-transcribe-faster-whisper-large-v3
#   docker compose build stt-batch-transcribe-faster-whisper-large-v3-turbo
#
# Or manually:
#   docker build --build-arg MODEL_VARIANT=base -t dalston/faster-whisper-base .
#   docker build --build-arg MODEL_VARIANT=large-v3 -t dalston/faster-whisper-large-v3 .

ARG MODEL_VARIANT=large-v3

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

# Copy variant-specific config to standard location
# Re-declare ARG after FROM to use it
ARG MODEL_VARIANT
COPY engines/stt-transcribe/faster-whisper/variants/${MODEL_VARIANT}.yaml /etc/dalston/engine.yaml

# Create model cache directory before predownload so runtime reuses build cache
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Pre-download the model at build time for faster startup
# This makes container startup faster at the cost of larger image size
ARG SKIP_MODEL_DOWNLOAD=false
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
        python -c "from faster_whisper import WhisperModel; WhisperModel('${MODEL_VARIANT}', device='cpu', compute_type='int8')" \
        && echo "Model ${MODEL_VARIANT} downloaded successfully"; \
    else \
        echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; \
    fi

# Set model variant via environment
ENV MODEL_VARIANT=${MODEL_VARIANT}

CMD ["python", "engine.py"]
