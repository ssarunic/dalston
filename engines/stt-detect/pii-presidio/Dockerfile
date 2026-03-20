# PII Detection Engine (Presidio + GLiNER)
#
# Detects PII entities in transcripts using Microsoft Presidio
# and optionally GLiNER for ML-based entity recognition.
#
# Requires base image: dalston/base-pytorch:latest
#   docker build -f docker/Dockerfile.base-pytorch -t dalston/base-pytorch:latest .
#
# Build from repo root:
#   docker compose build stt-pii-detect-presidio

FROM dalston/base-pytorch:latest

ARG SKIP_MODEL_DOWNLOAD=false

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Engine code and dependencies
WORKDIR /engine
COPY engines/stt-detect/pii-presidio/requirements.txt .
COPY engines/stt-detect/pii-presidio/engine.yaml .
COPY engines/stt-detect/pii-presidio/engine.py .
COPY engines/stt-detect/pii-presidio/recognizers/ ./recognizers/

RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Pre-download spaCy + GLiNER models for faster startup
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
        python -m spacy download en_core_web_sm && \
        python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi-v2.1')" && \
        echo "GLiNER model downloaded successfully"; \
    else \
        echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; \
    fi

CMD ["python", "engine.py"]
