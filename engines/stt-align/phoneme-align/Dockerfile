# Phoneme Forced Alignment Engine
#
# CTC forced alignment using wav2vec2 models for word-level timestamps.
# Supports both GPU (CUDA) and CPU modes with automatic detection.
#
# Requires base image: dalston/base-pytorch:latest
#   docker build -f docker/Dockerfile.base-pytorch -t dalston/base-pytorch:latest .
#
# Build from repo root:
#   docker compose build stt-align-phoneme-cpu
#
# For GPU support, use docker compose --profile gpu

FROM dalston/base-pytorch:latest

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Engine dependencies
WORKDIR /engine
COPY engines/stt-align/phoneme-align/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Engine code
COPY engines/stt-align/phoneme-align/engine.yaml .
COPY engines/stt-align/phoneme-align/engine.py .
COPY engines/stt-align/phoneme-align/align.py .
COPY engines/stt-align/phoneme-align/ctc_forced_align.py .
COPY engines/stt-align/phoneme-align/model_loader.py .

CMD ["python", "engine.py"]
