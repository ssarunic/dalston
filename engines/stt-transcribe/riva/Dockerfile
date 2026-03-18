# Unified Riva Engine
#
# One process, one gRPC channel to NIM, both batch and realtime interfaces.
# CPU-only -- all GPU inference runs in the Riva NIM sidecar container.
#
# Build from repo root:
#   docker build -t dalston/stt-riva:latest -f engines/stt-transcribe/riva/Dockerfile .

FROM python:3.12-slim
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

# Riva engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/riva/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Silero VAD model — baked in to eliminate runtime download
RUN mkdir -p /models/silero-vad \
    && python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx', '/models/silero-vad/silero_vad.onnx')"
ENV DALSTON_SILERO_VAD_ONNX=/models/silero-vad/silero_vad.onnx

# Engine code
WORKDIR /app
COPY engines/stt-transcribe/riva/ engines/stt-transcribe/riva/
COPY engines/stt-transcribe/riva/engine.yaml /etc/dalston/engine.yaml

# Default environment
ENV DALSTON_RIVA_URI=riva-nim:50051
ENV DALSTON_RIVA_CHUNK_MS=100
ENV DALSTON_ENGINE_ID=riva
ENV DALSTON_UNIFIED_ENGINE_ENABLED=true

EXPOSE 9000

CMD ["python", "engines/stt-transcribe/riva/runner.py"]
