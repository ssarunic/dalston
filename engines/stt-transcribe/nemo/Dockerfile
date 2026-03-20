# Unified NeMo Engine
#
# One process, one NeMo model, both batch and realtime interfaces.
#
# Requires base image: dalston/base-nemo:latest
#   docker build -f docker/Dockerfile.base-nemo -t dalston/base-nemo:latest .
#
# Build from repo root:
#   CPU:  docker build --build-arg DEVICE=cpu -t dalston/stt-nemo:latest -f engines/stt-transcribe/nemo/Dockerfile .
#   GPU:  docker build -t dalston/stt-nemo:latest -f engines/stt-transcribe/nemo/Dockerfile .

ARG DEVICE=cuda
FROM dalston/base-nemo:latest AS base

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# NeMo engine-specific dependencies (lhotse, transformers pinned for compatibility)
WORKDIR /engine
COPY engines/stt-transcribe/nemo/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Engine code
WORKDIR /app
COPY engines/stt-transcribe/nemo/ engines/stt-transcribe/nemo/
COPY engines/stt-transcribe/nemo/engine.yaml /etc/dalston/engine.yaml

EXPOSE 9000

CMD ["python", "engines/stt-transcribe/nemo/runner.py"]
