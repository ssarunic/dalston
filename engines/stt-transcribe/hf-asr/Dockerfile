# Unified HF-ASR Engine
#
# One process, one model manager, both batch and realtime interfaces.
# Supports any HuggingFace model with pipeline_tag=automatic-speech-recognition.
#
# Requires base image: dalston/base-pytorch:latest
#   docker build -f docker/Dockerfile.base-pytorch -t dalston/base-pytorch:latest .
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-hf-asr:latest -f engines/stt-transcribe/hf-asr/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-hf-asr:latest -f engines/stt-transcribe/hf-asr/Dockerfile .

ARG DEVICE=cpu
FROM dalston/base-pytorch:latest AS base

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# HF-ASR engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/hf-asr/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Engine code
WORKDIR /app
COPY engines/stt-transcribe/hf-asr/ engines/stt-transcribe/hf-asr/
COPY engines/stt-transcribe/hf-asr/engine.yaml /etc/dalston/engine.yaml

EXPOSE 9000

CMD ["python", "engines/stt-transcribe/hf-asr/runner.py"]
