# Unified vLLM-ASR Engine
#
# One process, one vLLM model, both batch and realtime interfaces.
# Supports Voxtral, Qwen2-Audio, and any model in the adapter registry.
#
# Build from repo root:
#   docker build -t dalston/stt-vllm-asr:latest -f engines/stt-unified/vllm-asr/Dockerfile .

# --- GPU only (vLLM requires CUDA) ---
FROM nvidia/cuda:12.6.0-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# vLLM engine dependencies
WORKDIR /engine
COPY engines/stt-unified/vllm-asr/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Model cache
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface

# Engine code (all adapters colocated in stt-unified/vllm-asr/)
WORKDIR /app
COPY engines/stt-unified/vllm-asr/ engines/stt-unified/vllm-asr/
COPY engines/stt-unified/vllm-asr/rt_engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-unified/vllm-asr/runner.py"]
