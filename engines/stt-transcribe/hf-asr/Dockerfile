# HuggingFace Transformers ASR Engine
#
# Generic ASR engine supporting any HuggingFace model with
# pipeline_tag=automatic-speech-recognition (Whisper, Wav2Vec2, HuBERT, MMS, etc.)
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-hf-asr
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-hf-asr:1.0.0 -f engines/stt-transcribe/hf-asr/Dockerfile .
#
# Models are downloaded at runtime to /models/huggingface volume.

FROM python:3.11-slim

# Install system dependencies (ffmpeg for audio processing, libsndfile for soundfile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 \
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
COPY engines/stt-transcribe/hf-asr/requirements.txt .

# Install HuggingFace Transformers and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-transcribe/hf-asr/engine.py .

# Copy engine.yaml for capability declaration
COPY engines/stt-transcribe/hf-asr/engine.yaml /etc/dalston/engine.yaml

# M39: Standardized model cache directories
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
RUN mkdir -p /models/huggingface /models/torch

CMD ["python", "engine.py"]
