# Final Merger Engine
#
# Combines pipeline outputs into standard transcript format.
# No GPU required - pure data transformation.
#
# For per-channel mode with audio redaction, uses FFmpeg to
# assemble redacted mono WAVs into stereo output.
#
# Build from repo root:
#   docker compose build engine-final-merger

FROM dalston/engine-base:latest

# Install FFmpeg for stereo audio assembly
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy engine files
COPY engines/merge/final-merger/requirements.txt .
COPY engines/merge/final-merger/engine.yaml .
COPY engines/merge/final-merger/engine.py .

# Install any additional Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "engine.py"]
