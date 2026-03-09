# Riva NIM Realtime Transcription Engine (gRPC Streaming Client)
#
# Build from repo root:
#   docker compose build stt-rt-riva

FROM python:3.11-slim

WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

WORKDIR /engine
COPY engines/stt-rt/riva/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engines/stt-rt/riva/engine.py .
COPY engines/stt-rt/riva/engine.yaml /etc/dalston/engine.yaml

CMD ["python", "engine.py"]
