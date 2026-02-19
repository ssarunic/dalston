# NeMo MSDD Speaker Diarization Engine
#
# Uses NVIDIA NeMo Multi-scale Diarization Decoder for speaker diarization.
# Supports GPU (recommended) or CPU (slow, for testing).
#
# Build from repo root:
#   docker compose build stt-batch-diarize-nemo-msdd

# Use plain Python to avoid NGC container version conflicts
FROM python:3.11-slim

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
COPY engines/stt-diarize/nemo-msdd/requirements.txt .

# Install PyTorch CPU first (for CPU testing), then NeMo
# For GPU, the CUDA version will be used at runtime
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install NeMo and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download models at build time for faster container startup
# NeMo downloads: VAD model, TitaNet embeddings, MSDD model
ARG SKIP_MODEL_DOWNLOAD=false
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
from nemo.collections.asr.models import EncDecClassificationModel, EncDecSpeakerLabelModel; \
from nemo.collections.asr.models.msdd_models import NeuralDiarizer; \
print('Downloading VAD model...'); \
EncDecClassificationModel.from_pretrained('vad_multilingual_marblenet'); \
print('Downloading TitaNet speaker embedding model...'); \
EncDecSpeakerLabelModel.from_pretrained('titanet_large'); \
print('Models downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Copy engine files
COPY engines/stt-diarize/nemo-msdd/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-diarize/nemo-msdd/engine.py .

# Create model cache directory
ENV NEMO_CACHE=/models
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
