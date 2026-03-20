# Unified ONNX Engine
#
# One process, one ONNX model, both batch and realtime interfaces.
#
# Requires base image: dalston/base-onnx:latest
#   docker build --build-arg DEVICE=cuda -f docker/Dockerfile.base-onnx -t dalston/base-onnx:latest .
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-onnx:latest -f engines/stt-transcribe/onnx/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-onnx:latest -f engines/stt-transcribe/onnx/Dockerfile .

ARG DEVICE=cpu
FROM dalston/base-onnx:latest AS base

# Dalston SDK (rebuilds only when dalston code changes)
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk,realtime-sdk]"

# Engine code
WORKDIR /app
COPY engines/stt-transcribe/onnx/ engines/stt-transcribe/onnx/
COPY engines/stt-transcribe/onnx/engine.yaml /etc/dalston/engine.yaml
COPY scripts/diagnose-gpu.py scripts/diagnose-gpu.py

EXPOSE 9000

CMD ["python", "engines/stt-transcribe/onnx/runner.py"]
