#!/usr/bin/env bash
# start-gpu-combo.sh — Launch or terminate a single spot GPU instance running both
#                      stt-onnx (transcription) and pyannote (diarization) containers.
#
# Usage:
#   start-gpu-combo.sh [start] [TAG]   # Launch instance; TAG applies to both images (default: latest)
#   start-gpu-combo.sh stop             # Terminate running instance
#
# To use different image tags per engine, override via environment variables:
#   ONNX_TAG=v1.2 PYANNOTE_TAG=v1.5 start-gpu-combo.sh start
#
# GPU note: both containers share the T4's 16GB VRAM via --gpus all.
# stt-onnx (~2-4GB) + pyannote (~2-4GB) leaves ample headroom on a 16GB T4,
# even when both are running inference concurrently across different jobs.

INSTANCE_TAG="dalston-gpu-combo"
CONTAINERS_TO_WAIT=("stt-unified-onnx" "stt-batch-diarize-pyannote-4-0")

prereq_check() {
  [[ -z "${HF_TOKEN:-}" ]] && { echo "ERROR: HF_TOKEN environment variable is required for pyannote models"; exit 1; }
}

build_container_run_block() {
  cat <<'BLOCK'
docker pull DALSTON_ECR/dalston/stt-onnx:DALSTON_ONNX_TAG
docker pull DALSTON_ECR/dalston/stt-diarize-pyannote:DALSTON_PYANNOTE_TAG

docker run -d --name stt-unified-onnx --gpus all --restart unless-stopped \
  -p 9000:9000 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_ENGINE_ID=onnx \
  -e REDIS_URL=redis://DALSTON_MAC_TS_IP:6379 \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_ENDPOINT_URL=http://DALSTON_MAC_TS_IP:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  DALSTON_ECR/dalston/stt-onnx:DALSTON_ONNX_TAG

docker run -d --name stt-batch-diarize-pyannote-4-0 --gpus all --restart unless-stopped \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_ENGINE_ID=pyannote-4.0 \
  -e DALSTON_WORKER_ID=pyannote-gpu-1 \
  -e HF_TOKEN=DALSTON_HF_TOKEN \
  -e REDIS_URL=redis://DALSTON_MAC_TS_IP:6379 \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_ENDPOINT_URL=http://DALSTON_MAC_TS_IP:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  DALSTON_ECR/dalston/stt-diarize-pyannote:DALSTON_PYANNOTE_TAG
BLOCK
}

apply_extra_substitutions() {
  local onnx_tag="${ONNX_TAG:-$TAG}"
  local pyannote_tag="${PYANNOTE_TAG:-$TAG}"
  USER_DATA="${USER_DATA//DALSTON_ONNX_TAG/$onnx_tag}"
  USER_DATA="${USER_DATA//DALSTON_PYANNOTE_TAG/$pyannote_tag}"
  USER_DATA="${USER_DATA//DALSTON_HF_TOKEN/$HF_TOKEN}"
}

# shellcheck source=_gpu-instance-lib.sh
source "$(dirname "$0")/_gpu-instance-lib.sh"
main "$@"
