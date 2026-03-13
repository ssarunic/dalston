# Unified ONNX Engine
#
# One process, one ONNX model, both batch and realtime interfaces.
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-onnx:latest -f engines/stt-unified/onnx/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-onnx:latest -f engines/stt-unified/onnx/Dockerfile .

ARG DEVICE=cpu

# --- CPU base ---
FROM python:3.12-slim AS base-cpu

# --- GPU base ---
FROM nvidia/cuda:12.6.0-runtime-ubuntu24.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# --- Common ---
FROM base-${DEVICE} AS base
ARG DEVICE=cpu
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# ONNX engine dependencies
WORKDIR /engine
COPY engines/stt-unified/onnx/requirements.txt .
RUN if [ "$DEVICE" = "cuda" ]; then \
        pip install --no-cache-dir --break-system-packages \
            "onnx-asr[hub]>=0.5.0" \
            "onnxruntime-gpu==1.19.2"; \
    else \
        pip install --no-cache-dir --break-system-packages -r requirements.txt; \
    fi

# Model cache
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface

# Engine code (all ONNX engine files in a single directory)
WORKDIR /app
COPY engines/stt-unified/onnx/ engines/stt-unified/onnx/
COPY engines/stt-unified/onnx/rt_engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-unified/onnx/runner.py"]
