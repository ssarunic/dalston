# Mistral Voxtral Transcription Engine
#
# Multilingual transcription using Voxtral models.
# Parameterized by MODEL_VARIANT to build variant-specific containers.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-voxtral-mini-3b
#   docker compose build stt-batch-transcribe-voxtral-small-24b
#
# Or directly:
#   docker build --build-arg MODEL_VARIANT=mini-3b -t dalston/stt-batch-transcribe-voxtral-mini-3b:1.0.0 .
#   docker build --build-arg MODEL_VARIANT=small-24b -t dalston/stt-batch-transcribe-voxtral-small-24b:1.0.0 .

# Build argument to select model variant
ARG MODEL_VARIANT=mini-3b

# Use CUDA base image for GPU support
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

# Re-declare ARG after FROM (it gets reset)
ARG MODEL_VARIANT=mini-3b

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
    git \
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
COPY engines/stt-transcribe/voxtral/requirements.txt .

# Install dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Pre-download the model at build time for faster container startup
ARG MODEL_VARIANT=mini-3b
RUN python -c "\
import os; \
os.environ['HF_HOME'] = '/models'; \
from transformers import VoxtralForConditionalGeneration, AutoProcessor; \
model_map = {'mini-3b': 'mistralai/Voxtral-Mini-3B-2507', 'small-24b': 'mistralai/Voxtral-Small-24B-2507'}; \
model_id = model_map['${MODEL_VARIANT}']; \
print(f'Downloading model {model_id}...'); \
AutoProcessor.from_pretrained(model_id); \
VoxtralForConditionalGeneration.from_pretrained(model_id); \
print(f'Model {model_id} downloaded successfully')"

# Copy engine files
COPY engines/stt-transcribe/voxtral/variants/${MODEL_VARIANT}.yaml /etc/dalston/engine.yaml
COPY engines/stt-transcribe/voxtral/engine.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

# Set MODEL_VARIANT environment variable for the engine
ENV MODEL_VARIANT=${MODEL_VARIANT}

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
