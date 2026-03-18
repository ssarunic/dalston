# Unified HF-ASR Engine
#
# One process, one model manager, both batch and realtime interfaces.
# Supports any HuggingFace model with pipeline_tag=automatic-speech-recognition.
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-hf-asr:latest -f engines/stt-transcribe/hf-asr/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-hf-asr:latest -f engines/stt-transcribe/hf-asr/Dockerfile .

ARG DEVICE=cpu

# --- CPU base ---
FROM python:3.14-slim AS base-cpu

# --- GPU base ---
FROM nvidia/cuda:12.6.0-runtime-ubuntu24.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip \
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

# HF-ASR engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/hf-asr/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Model cache
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface

# Silero VAD model — baked in to eliminate runtime download
RUN mkdir -p /models/silero-vad \
    && python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx', '/models/silero-vad/silero_vad.onnx')"
ENV DALSTON_SILERO_VAD_ONNX=/models/silero-vad/silero_vad.onnx

# Engine code (all adapters colocated in stt-transcribe/hf-asr/)
WORKDIR /app
COPY engines/stt-transcribe/hf-asr/ engines/stt-transcribe/hf-asr/
COPY engines/stt-transcribe/hf-asr/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-transcribe/hf-asr/runner.py"]
