# Audio Prepare Engine
#
# Converts audio files to standardized 16kHz, 16-bit, mono WAV format.
# Uses ffmpeg for conversion and ffprobe for metadata extraction.
#
# Build from repo root:
#   docker compose build stt-batch-prepare

FROM dalston/engine-base:latest

# Install ffmpeg (includes ffprobe)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy engine files
COPY engines/prepare/audio-prepare/requirements.txt .
COPY engines/prepare/audio-prepare/engine.yaml .
COPY engines/prepare/audio-prepare/engine.py .

# Install any additional Python dependencies
RUN pip install --no-cache-dir -r requirements.txt || true

CMD ["python", "engine.py"]
