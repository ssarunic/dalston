# Unified Faster Whisper Engine
#
# One process, one CTranslate2 model, both batch and realtime interfaces.
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-faster-whisper:latest -f engines/stt-transcribe/faster-whisper/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-faster-whisper:latest -f engines/stt-transcribe/faster-whisper/Dockerfile .

ARG DEVICE=cpu

# --- CPU base ---
FROM python:3.13-slim AS base-cpu

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

# Faster Whisper engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/faster-whisper/requirements.txt .
RUN if [ "$DEVICE" = "cpu" ]; then \
        pip install --no-cache-dir --break-system-packages \
            torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir --break-system-packages torchaudio; \
    fi
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Model cache
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
RUN mkdir -p /models/huggingface /models/torch /models/ctranslate2/faster-whisper

# Silero VAD model — baked in to eliminate runtime download
RUN mkdir -p /models/silero-vad \
    && python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx', '/models/silero-vad/silero_vad.onnx')"
ENV DALSTON_SILERO_VAD_ONNX=/models/silero-vad/silero_vad.onnx

# Engine code (all adapters colocated in stt-transcribe/faster-whisper/)
WORKDIR /app
COPY engines/stt-transcribe/faster-whisper/ engines/stt-transcribe/faster-whisper/
COPY engines/stt-transcribe/faster-whisper/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-transcribe/faster-whisper/runner.py"]
