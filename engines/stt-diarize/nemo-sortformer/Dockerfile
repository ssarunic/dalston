# NeMo Sortformer Speaker Diarization Engine
#
# Uses NVIDIA NeMo SortformerEncLabelModel for end-to-end speaker diarization.
# Supports up to 4 speakers with native overlap handling.
# Supports GPU (recommended) or CPU (slow, for testing).
#
# Build from repo root:
#   docker compose build stt-diarize-nemo-sortformer

# Use plain Python to avoid NGC container version conflicts
FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-diarize/nemo-sortformer/requirements.txt .

# Install PyTorch CPU first (for CPU testing), then NeMo
# For GPU, the CUDA version will be used at runtime
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download model at build time for faster container startup
ARG SKIP_MODEL_DOWNLOAD=false
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
from nemo.collections.asr.models import SortformerEncLabelModel; \
print('Downloading Sortformer v2.1 model...'); \
SortformerEncLabelModel.from_pretrained('nvidia/diar_streaming_sortformer_4spk-v2.1'); \
print('Model downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Copy engine files
COPY engines/stt-diarize/nemo-sortformer/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-diarize/nemo-sortformer/engine.py .

# Smoke test: verify NeMo imports and model registry work with this version
RUN python -c "\
from engine import MODEL_REGISTRY; \
from nemo.collections.asr.models import SortformerEncLabelModel; \
assert len(MODEL_REGISTRY) > 0; \
print('NeMo Sortformer imports OK')"

# M39: Standardized model cache directories
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
RUN mkdir -p /models/huggingface /models/torch /models/nemo

CMD ["python", "engine.py"]
