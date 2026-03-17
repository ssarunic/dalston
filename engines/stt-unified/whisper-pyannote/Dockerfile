# Whisper-Pyannote Composite Engine
#
# Lightweight CPU-only container that orchestrates faster-whisper and
# pyannote child engines via HTTP.  No GPU, no ML dependencies — just
# an HTTP client and the Dalston engine SDK.
#
# Build from repo root:
#   docker build -t dalston/stt-whisper-pyannote:latest \
#     -f engines/stt-unified/whisper-pyannote/Dockerfile .

FROM python:3.12-slim AS base
ENV DEBIAN_FRONTEND=noninteractive

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Composite engine dependencies (httpx for calling children)
WORKDIR /engine
COPY engines/stt-unified/whisper-pyannote/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Engine code and config
WORKDIR /app
COPY engines/stt-unified/whisper-pyannote/ engines/stt-unified/whisper-pyannote/
COPY engines/stt-unified/whisper-pyannote/engine.yaml /etc/dalston/engine.yaml

EXPOSE 9100

CMD ["python", "engines/stt-unified/whisper-pyannote/engine.py"]
