# NeMo Sortformer Speaker Diarization Engine
#
# Uses NVIDIA NeMo SortformerEncLabelModel for end-to-end speaker diarization.
# Supports up to 4 speakers with native overlap handling.
# Supports GPU (recommended) or CPU (slow, for testing).
#
# Requires base image: dalston/base-nemo:latest
#   docker build -f docker/Dockerfile.base-nemo -t dalston/base-nemo:latest .
#
# Build from repo root:
#   docker compose build stt-diarize-nemo-sortformer

FROM dalston/base-nemo:latest

ARG SKIP_MODEL_DOWNLOAD=false

# Dalston SDK
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir --break-system-packages -e ".[engine-sdk]"

# Engine code
WORKDIR /engine
COPY engines/stt-diarize/nemo-sortformer/engine.yaml /etc/dalston/engine.yaml
COPY engines/stt-diarize/nemo-sortformer/engine.py .

# Pre-download model at build time for faster container startup
RUN if [ "$SKIP_MODEL_DOWNLOAD" = "false" ]; then \
    python -c "\
from nemo.collections.asr.models import SortformerEncLabelModel; \
print('Downloading Sortformer v2.1 model...'); \
SortformerEncLabelModel.from_pretrained('nvidia/diar_streaming_sortformer_4spk-v2.1'); \
print('Model downloaded successfully')"; \
    else echo "Skipping model download (will download at runtime)"; fi

# Smoke test: verify NeMo imports and model registry work with this version
RUN python -c "\
from engine import MODEL_REGISTRY; \
from nemo.collections.asr.models import SortformerEncLabelModel; \
assert len(MODEL_REGISTRY) > 0; \
print('NeMo Sortformer imports OK')"

CMD ["python", "engine.py"]
