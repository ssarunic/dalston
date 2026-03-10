# Parakeet Inference Server (M72 sidecar pattern)
#
# Standalone GPU container wrapping ParakeetCore via gRPC.
# Batch and RT engines connect as thin CPU-only clients.
#
# Build from repo root:
#   docker build -t dalston/parakeet-server:1.0.0 \
#     -f engines/stt-server/parakeet/Dockerfile .

ARG DEVICE=cuda

FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda
FROM python:3.11-slim AS base-cpu

FROM base-${DEVICE} AS base

ARG DEVICE=cuda

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Install dalston package
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Install engine-specific requirements
WORKDIR /engine
COPY engines/stt-server/parakeet/requirements.txt .
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi
RUN pip install --no-cache-dir -r requirements.txt

# Model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
RUN mkdir -p /models/huggingface /models/torch /models/nemo

# Copy server code
WORKDIR /app
COPY engines/stt-server/parakeet/server.py .

ENV NVIDIA_VISIBLE_DEVICES=all
ENV DALSTON_SERVER_PORT=50053
ENV DALSTON_MAX_CONCURRENT=4
ENV DALSTON_DEFAULT_MODEL_ID=nvidia/parakeet-tdt-1.1b
ENV DALSTON_MODEL_TTL_SECONDS=3600
ENV DALSTON_MAX_LOADED_MODELS=2

EXPOSE 50053

CMD ["python", "server.py"]
