# Audio Redactor Engine
#
# Redacts PII audio segments using FFmpeg.
# Supports silence and beep redaction modes.
#
# Build from repo root:
#   docker compose build engine-audio-redactor

FROM dalston/engine-base:latest

# Install FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy engine files
COPY engines/redact/audio-redactor/requirements.txt .
COPY engines/redact/audio-redactor/engine.yaml .
COPY engines/redact/audio-redactor/engine.py .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "engine.py"]
