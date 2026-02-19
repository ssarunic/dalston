# Faster Whisper Streaming Real-time Engine
#
# Real-time transcription using faster-whisper with Silero VAD.
# Runs a WebSocket server for streaming audio transcription.
#
# Build from repo root:
#   docker compose build stt-rt-transcribe-faster-whisper-streaming-base
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

# Pre-download models at build time for faster startup
# Download distil-whisper (fast model) - use Systran CTranslate2 converted model
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('Systran/faster-distil-whisper-large-v3', device='cpu', compute_type='int8')" \
    && echo "Fast model (distil-large-v3) downloaded successfully"

# Download large-v3 (accurate model) - use Systran CTranslate2 converted model
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('Systran/faster-whisper-large-v3', device='cpu', compute_type='int8')" \
    && echo "Accurate model (large-v3) downloaded successfully"

# Download Silero VAD model
RUN python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', force_reload=False)" \
    && echo "Silero VAD model downloaded successfully"

# Copy engine files
COPY engines/stt-rt/faster-whisper/variants/base.yaml /etc/dalston/engine.yaml
COPY engines/stt-rt/faster-whisper/engine.py .

# Create model cache directory
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Default environment variables
ENV WORKER_ID=stt-rt-transcribe-faster-whisper
ENV WORKER_PORT=9000
ENV MAX_SESSIONS=4
ENV REDIS_URL=redis://redis:6379

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
