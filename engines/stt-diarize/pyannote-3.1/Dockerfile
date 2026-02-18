# Pyannote 3.1 Speaker Diarization Engine
#
# Identifies who speaks when in audio files using pyannote-audio 3.1.
# Requires HuggingFace token (HF_TOKEN) for accessing gated models.
#
# Build from repo root:
#   docker compose build stt-batch-diarize-pyannote-v31-cpu
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
COPY engines/stt-diarize/pyannote-3.1/requirements.txt .

# Install pyannote and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine files
COPY engines/stt-diarize/pyannote-3.1/engine.yaml .
COPY engines/stt-diarize/pyannote-3.1/engine.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
