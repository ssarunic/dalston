# PII Detection Engine (Presidio + GLiNER)
#
# Detects PII entities in transcripts using Microsoft Presidio
# and optionally GLiNER for ML-based entity recognition.
#
# Build from repo root:
#   docker compose build engine-pii-presidio

FROM dalston/engine-base:latest

# Install additional system dependencies for spaCy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy engine files
COPY engines/detect/pii-presidio/requirements.txt .
COPY engines/detect/pii-presidio/engine.yaml .
COPY engines/detect/pii-presidio/engine.py .
COPY engines/detect/pii-presidio/recognizers/ ./recognizers/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model
RUN python -m spacy download en_core_web_sm

CMD ["python", "engine.py"]
