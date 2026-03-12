# Unified Parakeet ONNX Engine (M63)
#
# One process, one ONNX model, both batch and realtime interfaces.
# Replaces the separate stt-batch-transcribe-onnx + stt-rt-onnx services.
#
# Build from repo root:
#   CPU: docker compose build stt-unified-onnx-cpu
#   GPU: docker compose build stt-unified-onnx
#
# Or directly:
#   CPU: docker build -t dalston/stt-unified-onnx-cpu:1.0.0 -f engines/stt-unified/parakeet-onnx/Dockerfile .
#   GPU: docker build --build-arg DEVICE=cuda -t dalston/stt-unified-onnx:1.0.0 -f engines/stt-unified/parakeet-onnx/Dockerfile .

ARG DEVICE=cpu

FROM python:3.11-slim AS base-cpu
FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda

FROM base-${DEVICE} AS base

ARG DEVICE=cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install dalston package with both SDKs
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk,realtime-sdk]"

# Install ONNX ASR and audio dependencies
WORKDIR /engine
COPY engines/stt-unified/parakeet-onnx/requirements.txt .
RUN if [ "$DEVICE" = "cuda" ]; then \
        pip install --no-cache-dir "onnx-asr[cuda,hub]>=0.5.0"; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# Model cache directories
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV ONNX_MODEL_CACHE=/models/onnx
RUN mkdir -p /models/huggingface /models/onnx

# Copy engine files preserving the directory structure the runner expects
WORKDIR /app
COPY engines/stt-unified/parakeet-onnx/ engines/stt-unified/parakeet-onnx/
COPY engines/stt-transcribe/parakeet-onnx/engine.py engines/stt-transcribe/parakeet-onnx/engine.py
COPY engines/stt-rt/parakeet-onnx/engine.py engines/stt-rt/parakeet-onnx/engine.py

# RT capabilities (read by realtime SDK at registration time)
COPY engines/stt-rt/parakeet-onnx/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all
ENV DALSTON_UNIFIED_ENGINE_ENABLED=true

EXPOSE 9000

CMD ["python", "engines/stt-unified/parakeet-onnx/runner.py"]
