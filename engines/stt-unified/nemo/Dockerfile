# Unified NeMo Engine
#
# One process, one NeMo model, both batch and realtime interfaces.
#
# Build from repo root:
#   CPU:  docker build --build-arg DEVICE=cpu -t dalston/stt-nemo:latest -f engines/stt-unified/nemo/Dockerfile .
#   GPU:  docker build -t dalston/stt-nemo:latest -f engines/stt-unified/nemo/Dockerfile .

ARG DEVICE=cuda

# --- CPU base ---
FROM python:3.12-slim AS base-cpu

# --- GPU base ---
FROM nvidia/cuda:12.6.0-runtime-ubuntu24.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- Common ---
FROM base-${DEVICE} AS base
ARG DEVICE=cuda
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# NeMo engine dependencies
WORKDIR /engine
COPY engines/stt-unified/nemo/requirements.txt .
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir --break-system-packages \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir --break-system-packages torchaudio; \
    fi
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Model cache
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
RUN mkdir -p /models/huggingface /models/torch /models/nemo

# Engine code (all adapters colocated in stt-unified/nemo/)
WORKDIR /app
COPY engines/stt-unified/nemo/ engines/stt-unified/nemo/
COPY engines/stt-unified/nemo/rt_engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-unified/nemo/runner.py"]
