# Riva NIM Transcription Engine (gRPC Client)
#
# Thin engine that delegates to an NVIDIA Riva NIM container via gRPC.
# No model files — inference happens in the NIM sidecar.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-riva
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-riva:1.0.0 \
#     -f engines/stt-transcribe/riva/Dockerfile .

FROM python:3.11-slim

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-transcribe/riva/requirements.txt .

# Install riva client and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-transcribe/riva/engine.py .

# Copy engine.yaml
COPY engines/stt-transcribe/riva/engine.yaml /etc/dalston/engine.yaml

CMD ["python", "engine.py"]
