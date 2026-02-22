# PII Detection Engine (Presidio + GLiNER)
#
# Detects PII entities in transcripts using Microsoft Presidio
# and optionally GLiNER for ML-based entity recognition.
#
# Build from repo root:
#   docker compose build stt-batch-pii-detect-presidio

# Build argument to select device (cuda or cpu)
ARG DEVICE=cpu

# Base images for each device type
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base-cuda
FROM python:3.11-slim AS base-cpu

# Select the appropriate base image
FROM base-${DEVICE} AS base

# Re-declare args after FROM (they get reset)
ARG DEVICE=cpu
ARG SKIP_MODEL_DOWNLOAD=false

# Install system dependencies
RUN if [ "$DEVICE" = "cuda" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            build-essential \
            python3.11 \
            python3.11-venv \
            python3.11-dev \
            python3-pip \
            git; \
        ln -s /usr/bin/python3.11 /usr/bin/python; \
    else \
        apt-get update && apt-get install -y --no-install-recommends \
            build-essential \
            git; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN python -m pip install --no-cache-dir --upgrade pip

# Install Dalston engine SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN python -m pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine code
WORKDIR /engine

# Copy engine files
COPY engines/stt-detect/pii-presidio/requirements.txt .
COPY engines/stt-detect/pii-presidio/engine.yaml .
COPY engines/stt-detect/pii-presidio/engine.py .
COPY engines/stt-detect/pii-presidio/recognizers/ ./recognizers/

# Install torch variant matching target device
RUN if [ "$DEVICE" = "cuda" ]; then \
        python -m pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cu121; \
    else \
        python -m pip install --no-cache-dir \
            torch --index-url https://download.pytorch.org/whl/cpu; \
    fi

# Install dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Create model cache directory before predownload so runtime reuses build cache
ENV HF_HOME=/models
ENV TORCH_HOME=/models
RUN mkdir -p /models

# Pre-download spaCy + GLiNER models for faster startup
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
        python -m spacy download en_core_web_sm && \
        python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi-v2.1')" && \
        echo "GLiNER model downloaded successfully"; \
    else \
        echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; \
    fi

CMD ["python", "engine.py"]
