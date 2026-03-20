# NeMo MSDD Speaker Diarization Engine
#
# Uses NVIDIA NeMo Multi-scale Diarization Decoder for speaker diarization.
# Supports GPU (recommended) or CPU (slow, for testing).
#
# Requires base image: dalston/base-nemo:latest
#   docker build -f docker/Dockerfile.base-nemo -t dalston/base-nemo:latest .
#
# Build from repo root:
#   docker compose build stt-diarize-nemo-msdd

FROM dalston/base-nemo:latest

ARG SKIP_MODEL_DOWNLOAD=false

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Engine code
WORKDIR /engine
COPY engines/stt-diarize/nemo-msdd/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-diarize/nemo-msdd/engine.py .

# Pre-download models at build time for faster container startup
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
from nemo.collections.asr.models import EncDecFrameClassificationModel, EncDecSpeakerLabelModel; \
from nemo.collections.asr.models.msdd_models import NeuralDiarizer; \
print('Downloading VAD model...'); \
EncDecFrameClassificationModel.from_pretrained('nvidia/frame_vad_multilingual_marblenet_v2.0'); \
print('Downloading TitaNet speaker embedding model...'); \
EncDecSpeakerLabelModel.from_pretrained('nvidia/speakerverification_en_titanet_large'); \
print('Models downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Smoke test: verify NeMo config schema and imports work with this version
RUN python -c "\
from engine import DIAR_CONFIG, MODEL_COMPONENTS; \
from omegaconf import OmegaConf; \
from nemo.collections.asr.models.msdd_models import NeuralDiarizer; \
cfg = OmegaConf.create(DIAR_CONFIG); \
assert cfg.diarizer.vad.model_path is not None; \
assert cfg.diarizer.speaker_embeddings.model_path is not None; \
print('NeMo MSDD config schema OK')"

CMD ["python", "engine.py"]
