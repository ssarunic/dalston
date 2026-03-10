# Riva NIM Batch Transcription Engine
#
# Thin CPU adapter that streams audio to a Riva NIM sidecar via gRPC.
# No GPU required — all inference runs in the NIM container.
#
# Build from repo root:
#   docker compose --profile riva build stt-batch-transcribe-riva

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

# Install Riva client and gRPC dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-transcribe/riva/engine.py .

# Copy engine.yaml for capabilities registration
COPY engines/stt-transcribe/riva/engine.yaml /etc/dalston/engine.yaml

# Default environment variables
ENV DALSTON_RIVA_URI=riva-nim:50051
ENV DALSTON_RIVA_CHUNK_MS=100
ENV DALSTON_RUNTIME=riva

CMD ["python", "engine.py"]
