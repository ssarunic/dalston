# Pyannote 4.0 Speaker Diarization Engine
#
# Identifies who speaks when in audio files using pyannote-audio 4.0.
# Features the new Community-1 pipeline with VBx clustering.
# Requires HuggingFace token (HF_TOKEN) for accessing gated models.
#
# Requires base image: dalston/base-pyannote:latest
#   docker build --build-arg DEVICE=cuda -f docker/Dockerfile.base-pyannote -t dalston/base-pyannote:latest .
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-diarize-pyannote:latest -f engines/stt-diarize/pyannote-4.0/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-diarize-pyannote:latest -f engines/stt-diarize/pyannote-4.0/Dockerfile .
#
# Note: torchcodec (required by pyannote 4.0) only has x86_64 Linux wheels,
# so base image must be built for linux/amd64 platform on ARM Macs.

ARG DEVICE=cpu
FROM dalston/base-pyannote:latest AS base

# Dalston SDK (rebuilds only when dalston code changes)
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Engine code
WORKDIR /engine
COPY engines/stt-diarize/pyannote-4.0/engine.yaml .
COPY engines/stt-diarize/pyannote-4.0/engine.py .

CMD ["python", "engine.py"]
