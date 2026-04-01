# Unified Faster Whisper Engine
#
# One process, one CTranslate2 model, both batch and realtime interfaces.
#
# Requires base image: dalston/base-pytorch:latest
#   docker build -f docker/Dockerfile.base-pytorch -t dalston/base-pytorch:latest .
#
# Build from repo root (GPU/CPU depends on the base-pytorch image):
#   docker build -t dalston/stt-faster-whisper:latest -f engines/stt-transcribe/faster-whisper/Dockerfile .

FROM dalston/base-pytorch:latest AS base

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# Faster Whisper engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/faster-whisper/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
# Re-run ldconfig to pick up nvidia-cublas-cu12 installed above
RUN ldconfig

# Additional model cache for CTranslate2
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
RUN mkdir -p /models/ctranslate2/faster-whisper

# Engine code
WORKDIR /app
COPY engines/stt-transcribe/faster-whisper/ engines/stt-transcribe/faster-whisper/
COPY engines/stt-transcribe/faster-whisper/engine.yaml /etc/dalston/engine.yaml

EXPOSE 9000

CMD ["python", "engines/stt-transcribe/faster-whisper/runner.py"]
