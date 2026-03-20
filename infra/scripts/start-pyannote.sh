#!/usr/bin/env bash
# start-pyannote.sh — Launch or terminate a spot GPU instance running the pyannote diarization container
#
# Usage:
#   start-pyannote.sh [start] [TAG]   # Launch instance (default command)
#   start-pyannote.sh stop             # Terminate running instance

INSTANCE_TAG="dalston-stt-diarize-pyannote-4-0"
CONTAINERS_TO_WAIT=("stt-diarize-pyannote-4-0")

prereq_check() {
  [[ -z "${HF_TOKEN:-}" ]] && { echo "ERROR: HF_TOKEN environment variable is required for pyannote models"; exit 1; }
}

build_container_run_block() {
  cat <<'BLOCK'
docker pull DALSTON_ECR/dalston/stt-diarize-pyannote:DALSTON_TAG
docker run -d --name stt-diarize-pyannote-4-0 --gpus all --restart unless-stopped \
  -v /data/models:/models \
  -e HF_HOME=/models/huggingface \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_ENGINE_ID=pyannote-4.0 \
  -e DALSTON_WORKER_ID=pyannote-gpu-1 \
  -e HF_TOKEN=DALSTON_HF_TOKEN \
  -e REDIS_URL=redis://DALSTON_MAC_TS_IP:6379 \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_ENDPOINT_URL=http://DALSTON_MAC_TS_IP:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  DALSTON_ECR/dalston/stt-diarize-pyannote:DALSTON_TAG
BLOCK
}

apply_extra_substitutions() {
  USER_DATA="${USER_DATA//DALSTON_TAG/$TAG}"
  USER_DATA="${USER_DATA//DALSTON_HF_TOKEN/$HF_TOKEN}"
}

# shellcheck source=_gpu-instance-lib.sh
source "$(dirname "$0")/_gpu-instance-lib.sh"
main "$@"
