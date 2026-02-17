# NVIDIA Parakeet Transcription Engine
#
# FastConformer transcription with CTC or TDT decoders using NeMo toolkit.
# Parameterized by MODEL_VARIANT to build variant-specific containers.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-parakeet-ctc-0.6b
#   docker compose build stt-batch-transcribe-parakeet-tdt-1.1b
#
# Or directly:
#   docker build --build-arg MODEL_VARIANT=ctc-0.6b -t dalston/stt-batch-transcribe-parakeet-ctc-0.6b:1.0.0 .
#   docker build --build-arg MODEL_VARIANT=tdt-1.1b -t dalston/stt-batch-transcribe-parakeet-tdt-1.1b:1.0.0 .

# Build argument to select model variant (decoder-size)
ARG MODEL_VARIANT=ctc-0.6b

# Use CUDA base image for GPU support
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Re-declare ARG after FROM (it gets reset)
ARG MODEL_VARIANT=ctc-0.6b

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    gcc \
    g++ \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    && ln -s /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN python -m pip install --no-cache-dir --upgrade pip

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN python -m pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/transcribe/parakeet/requirements.txt .

# Install NeMo and dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Pre-download the model at build time for faster container startup
# Skip on ARM64/Apple Silicon due to NeMo segfault issues - model will download at runtime
ARG MODEL_VARIANT=ctc-0.6b
ARG SKIP_MODEL_DOWNLOAD=false
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
import nemo.collections.asr as nemo_asr; \
model_map = { \
    'ctc-0.6b': 'nvidia/parakeet-ctc-0.6b', \
    'ctc-1.1b': 'nvidia/parakeet-ctc-1.1b', \
    'tdt-0.6b': 'nvidia/parakeet-tdt-0.6b-v3', \
    'tdt-1.1b': 'nvidia/parakeet-tdt-1.1b', \
}; \
model_id = model_map['${MODEL_VARIANT}']; \
model = nemo_asr.models.ASRModel.from_pretrained(model_id); \
print(f'Model {model_id} downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Copy engine files
COPY engines/transcribe/parakeet/variants/${MODEL_VARIANT}.yaml /etc/dalston/engine.yaml
COPY engines/transcribe/parakeet/engine.py .

# Create model cache directory
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
RUN mkdir -p /models

# Set MODEL_VARIANT environment variable for the engine
ENV MODEL_VARIANT=${MODEL_VARIANT}

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
