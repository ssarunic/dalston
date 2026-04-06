# HF ASR + Phoneme Align + Pyannote 4.0 Combo Engine
#
# Integrated transcription + word alignment + speaker diarization in a
# single process. Uses base-pyannote which includes PyTorch, torchaudio,
# and pyannote-audio 4.0.
#
# Requires base image: dalston/base-pyannote:latest
#   docker build --build-arg DEVICE=cuda -f docker/Dockerfile.base-pyannote -t dalston/base-pyannote:latest .
#
# Build from repo root:
#   CPU:  docker build -t dalston/stt-hf-asr-align-pyannote:latest -f engines/stt-transcribe/hf-asr-align-pyannote/Dockerfile .
#   GPU:  docker build --build-arg DEVICE=cuda -t dalston/stt-hf-asr-align-pyannote:latest -f engines/stt-transcribe/hf-asr-align-pyannote/Dockerfile .

ARG DEVICE=cpu
FROM dalston/base-pyannote:latest AS base

# Dalston SDK (rebuilds only when dalston code changes)
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Phoneme alignment modules (shared with phoneme-align engine)
COPY engines/stt-align/phoneme-align/align.py /engine/
COPY engines/stt-align/phoneme-align/ctc_forced_align.py /engine/
COPY engines/stt-align/phoneme-align/model_loader.py /engine/

# Engine code
WORKDIR /engine
COPY engines/stt-transcribe/hf-asr-align-pyannote/engine.yaml .
COPY engines/stt-transcribe/hf-asr-align-pyannote/engine.py .

CMD ["python", "engine.py"]
