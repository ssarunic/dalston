# Unified Faster Whisper Engine (M63)
#
# One process, one CTranslate2 model, both batch and realtime interfaces.
# Replaces the separate stt-batch-transcribe-faster-whisper + stt-rt-faster-whisper services.
#
# Build from repo root:
#   GPU: docker compose build stt-unified-faster-whisper
#   CPU: docker compose build stt-unified-faster-whisper-cpu
#
# Or directly:
#   GPU: docker build -t dalston/stt-unified-faster-whisper:1.0.0 -f engines/stt-unified/faster-whisper/Dockerfile .
#   CPU: docker build --build-arg DEVICE=cpu -t dalston/stt-unified-faster-whisper-cpu:1.0.0 -f engines/stt-unified/faster-whisper/Dockerfile .

ARG DEVICE=cpu

FROM python:3.11-slim AS base-cpu
FROM nvcr.io/nvidia/pytorch:24.12-py3 AS base-cuda

FROM base-${DEVICE} AS base

ARG DEVICE=cpu

# System dependencies (superset of batch + RT)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install dalston package with both SDKs
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk,realtime-sdk]"

# Install PyTorch for CPU builds only (GPU base already provides torch)
WORKDIR /engine
COPY engines/stt-unified/faster-whisper/requirements.txt .
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
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
RUN mkdir -p /models/huggingface /models/torch /models/ctranslate2/faster-whisper

# Copy engine files preserving the directory structure the runner expects
WORKDIR /app
COPY engines/stt-unified/faster-whisper/ engines/stt-unified/faster-whisper/
COPY engines/stt-transcribe/faster-whisper/engine.py engines/stt-transcribe/faster-whisper/engine.py
COPY engines/stt-rt/faster-whisper/engine.py engines/stt-rt/faster-whisper/engine.py

# RT capabilities (read by realtime SDK at registration time)
COPY engines/stt-rt/faster-whisper/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all
ENV DALSTON_UNIFIED_ENGINE_ENABLED=true

EXPOSE 9000

CMD ["python", "engines/stt-unified/faster-whisper/runner.py"]
