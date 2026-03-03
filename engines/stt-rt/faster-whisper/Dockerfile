# Faster Whisper Streaming Real-time Engine
#
# Real-time transcription using faster-whisper with dynamic model loading (M43).
# Runs a WebSocket server for streaming audio transcription.
# Models are loaded on-demand via ModelManager, not baked into the image.
#
# Build from repo root:
#   docker compose build stt-rt-faster-whisper
#
# For GPU support, use docker compose --profile gpu

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
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
COPY engines/stt-rt/faster-whisper/requirements.txt .

# Install engine dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download Silero VAD model (always needed)
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', force_reload=False)" \
    && echo "Silero VAD model downloaded successfully"

# M43: Optional model preloading via build arg
# If PRELOAD_MODEL is set, download that model at build time
ARG PRELOAD_MODEL=""
RUN if [ -n "$PRELOAD_MODEL" ]; then \
    echo "Pre-downloading model: $PRELOAD_MODEL"; \
    python -c "from faster_whisper import WhisperModel; WhisperModel('$PRELOAD_MODEL', device='cpu', compute_type='int8')" && \
    echo "Model $PRELOAD_MODEL downloaded successfully"; \
    fi

# Copy engine files
# M50: Use main engine.yaml for structured capabilities
COPY engines/stt-rt/faster-whisper/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-rt/faster-whisper/engine.py .

# M39: Standardized model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
RUN mkdir -p /models/huggingface /models/torch /models/ctranslate2/faster-whisper

# M43: Dynamic model loading configuration
# Models are loaded on-demand, with optional preloading and TTL-based eviction
ENV DALSTON_MODEL_TTL_SECONDS=3600
ENV DALSTON_MAX_LOADED_MODELS=2
# Set DALSTON_MODEL_PRELOAD to preload a model on startup (e.g., "large-v3-turbo")
# Set DALSTON_S3_BUCKET to enable S3-based model storage

# Default environment variables
ENV DALSTON_WORKER_ID=stt-rt-faster-whisper
ENV DALSTON_WORKER_PORT=9000
ENV DALSTON_MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
