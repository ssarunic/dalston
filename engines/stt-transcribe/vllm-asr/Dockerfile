# vLLM Audio ASR Engine
#
# Audio-capable LLMs via vLLM for high-accuracy transcription.
# Supports Voxtral and Qwen2-Audio model families.
# GPU required for vLLM inference.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-vllm-asr
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-vllm-asr:1.0.0 \
#     -f engines/stt-transcribe/vllm-asr/Dockerfile .

# Build argument to skip model download during build
ARG SKIP_MODEL_DOWNLOAD=false

# Use vLLM official base image (includes CUDA, PyTorch, vLLM)
FROM vllm/vllm-openai:v0.8.4 AS base

# Re-declare ARGs after FROM
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
COPY engines/stt-transcribe/vllm-asr/requirements.txt .

# Install additional dependencies (vLLM already in base image)
RUN pip install --no-cache-dir soundfile librosa numpy

# M39: Standardized model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
RUN mkdir -p /models/huggingface /models/torch

# Pre-download the default model at build time
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
import os; \
os.environ['HF_HOME'] = '/models/huggingface'; \
from huggingface_hub import snapshot_download; \
model_id = 'mistralai/Voxtral-Mini-3B-2507'; \
print(f'Downloading model {model_id}...'); \
snapshot_download(model_id); \
print(f'Model {model_id} downloaded successfully')"; \
    else echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; fi

# Copy engine files
COPY engines/stt-transcribe/vllm-asr/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-transcribe/vllm-asr/engine.py .
COPY engines/stt-transcribe/vllm-asr/adapters/ adapters/

# Default environment variables
ENV DALSTON_ENGINE_ID=vllm-asr
ENV DALSTON_DEFAULT_MODEL_ID=mistralai/Voxtral-Mini-3B-2507
ENV DALSTON_VLLM_GPU_MEMORY_UTILIZATION=0.9
ENV DALSTON_VLLM_MAX_MODEL_LEN=4096

# Set CUDA visible devices
ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["python", "engine.py"]
