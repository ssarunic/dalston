# Riva NIM Real-time Transcription Engine
#
# Thin CPU adapter that streams audio to a Riva NIM sidecar via gRPC.
# No GPU required — all inference runs in the NIM container.
#
# Build from repo root:
#   docker compose --profile riva build stt-rt-riva

FROM python:3.11-slim

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston realtime SDK
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-rt/riva/requirements.txt .

# Install Riva client and gRPC dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-rt/riva/engine.py .

# Copy engine.yaml for capabilities registration
COPY engines/stt-rt/riva/engine.yaml /etc/dalston/engine.yaml

# Default environment variables
ENV DALSTON_RIVA_URI=riva-nim:50051
ENV DALSTON_ENGINE_ID=riva
ENV DALSTON_INSTANCE=stt-rt-riva
ENV DALSTON_WORKER_PORT=9000
ENV DALSTON_MAX_SESSIONS=8
ENV REDIS_URL=redis://redis:6379

# Expose WebSocket port
EXPOSE 9000

CMD ["python", "engine.py"]
