# Whisper + Align + Pyannote Composite Engine
#
# Lightweight CPU-only container that orchestrates faster-whisper,
# phoneme-align, and pyannote child engines via HTTP.  No GPU, no ML
# dependencies — just an HTTP client and the Dalston engine SDK.
#
# Build from repo root:
#   docker build -t dalston/stt-whisper-align-pyannote:latest \
#     -f engines/stt-combo/whisper-align-pyannote/Dockerfile .

FROM python:3.14-slim AS base
ENV DEBIAN_FRONTEND=noninteractive

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Composite engine dependencies (httpx for calling children)
WORKDIR /engine
COPY engines/stt-combo/whisper-align-pyannote/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Engine config
COPY engines/stt-combo/whisper-align-pyannote/engine.yaml /etc/dalston/engine.yaml

EXPOSE 9100

CMD ["python", "-m", "dalston.engine_sdk.composite_runner"]
