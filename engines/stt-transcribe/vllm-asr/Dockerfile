# Unified vLLM-ASR Engine
#
# One process, one vLLM model, both batch and realtime interfaces.
# Supports Voxtral, Qwen2-Audio, and any model in the adapter registry.
#
# Build from repo root:
#   docker build -t dalston/stt-vllm-asr:latest -f engines/stt-transcribe/vllm-asr/Dockerfile .

# --- GPU only (vLLM requires CUDA) ---
FROM nvidia/cuda:13.2.0-runtime-ubuntu24.04
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
COPY engines/stt-transcribe/vllm-asr/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Model cache
ENV HF_HOME=/models/huggingface
RUN mkdir -p /models/huggingface

# Silero VAD model — baked in to eliminate runtime download
RUN mkdir -p /models/silero-vad \
    && python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx', '/models/silero-vad/silero_vad.onnx')"
ENV DALSTON_SILERO_VAD_ONNX=/models/silero-vad/silero_vad.onnx

# Engine code (all adapters colocated in stt-transcribe/vllm-asr/)
WORKDIR /app
COPY engines/stt-transcribe/vllm-asr/ engines/stt-transcribe/vllm-asr/
COPY engines/stt-transcribe/vllm-asr/engine.yaml /etc/dalston/engine.yaml

ENV NVIDIA_VISIBLE_DEVICES=all


EXPOSE 9000

CMD ["python", "engines/stt-transcribe/vllm-asr/runner.py"]
