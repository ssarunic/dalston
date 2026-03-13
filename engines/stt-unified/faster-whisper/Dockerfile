# Unified Faster Whisper Engine
#
# One process, one CTranslate2 model, both batch and realtime interfaces.
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-faster-whisper:latest -f engines/stt-unified/faster-whisper/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-faster-whisper:latest -f engines/stt-unified/faster-whisper/Dockerfile .

ARG DEVICE=cpu

# --- CPU base ---
FROM python:3.11-slim AS base-cpu

# --- GPU base ---
FROM nvidia/cuda:12.6.0-runtime-ubuntu22.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
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
RUN pip install --no-cache-dir -e ".[engine-sdk,realtime-sdk]"

# Faster Whisper engine dependencies
WORKDIR /engine
COPY engines/stt-unified/faster-whisper/requirements.txt .
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir torchaudio; \
    fi
RUN pip install --no-cache-dir -r requirements.txt

# Model cache
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
RUN mkdir -p /models/huggingface /models/torch /models/ctranslate2/faster-whisper

# Engine code (runner + adapters it imports via _register_engine_modules)
WORKDIR /app
COPY engines/stt-unified/faster-whisper/ engines/stt-unified/faster-whisper/
COPY engines/stt-transcribe/faster-whisper/engine.py engines/stt-transcribe/faster-whisper/engine.py
COPY engines/stt-rt/faster-whisper/engine.py engines/stt-rt/faster-whisper/engine.py
COPY engines/stt-rt/faster-whisper/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all
ENV DALSTON_UNIFIED_ENGINE_ENABLED=true

EXPOSE 9000

CMD ["python", "engines/stt-unified/faster-whisper/runner.py"]
