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
# Build argument to select device (cuda or cpu)
ARG DEVICE=cuda

# Base images for each device type
# GPU: Official NVIDIA PyTorch container (requires NGC login: docker login nvcr.io)
# CPU: Python slim image for lightweight CPU-only builds
FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare ARGs after FROM (they get reset)
ARG MODEL_VARIANT=ctc-0.6b
ARG DEVICE=cuda
ARG SKIP_MODEL_DOWNLOAD=false

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
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
COPY engines/stt-transcribe/parakeet/requirements.txt .

# Install PyTorch for CPU builds only (GPU base already provides torch)
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create model cache directory before predownload so runtime reuses build cache
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Pre-download the model at build time for faster container startup
# Skip on ARM64/Apple Silicon due to QEMU emulation issues - model will download at runtime
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
import nemo.collections.asr as nemo_asr; \
model_map = { \
    'ctc-0.6b': 'nvidia/parakeet-ctc-0.6b', \
    'ctc-1.1b': 'nvidia/parakeet-ctc-1.1b', \
    'tdt-0.6b-v3': 'nvidia/parakeet-tdt-0.6b-v3', \
    'tdt-1.1b': 'nvidia/parakeet-tdt-1.1b', \
}; \
model_id = model_map['${MODEL_VARIANT}']; \
model = nemo_asr.models.ASRModel.from_pretrained(model_id); \
print(f'Model {model_id} downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Copy engine files
COPY engines/stt-transcribe/parakeet/variants/${MODEL_VARIANT}.yaml /etc/dalston/engine.yaml
COPY engines/stt-transcribe/parakeet/engine.py .

# Set MODEL_VARIANT environment variable for the engine
ENV MODEL_VARIANT=${MODEL_VARIANT}

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
