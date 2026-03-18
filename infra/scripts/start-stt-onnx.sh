#!/usr/bin/env bash
# start-stt-onnx.sh — Launch or terminate a spot GPU instance running the stt-onnx container
#
# Usage:
#   start-stt-onnx.sh [start] [TAG]   # Launch instance (default command)
#   start-stt-onnx.sh stop             # Terminate running instance

INSTANCE_TAG="dalston-stt-transcribe-onnx"
CONTAINERS_TO_WAIT=("stt-transcribe-onnx")

build_container_run_block() {
  cat <<'BLOCK'
docker pull DALSTON_ECR/dalston/stt-onnx:DALSTON_TAG
docker run -d --name stt-transcribe-onnx --gpus all --restart unless-stopped \
  -p 9000:9000 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_ENGINE_ID=onnx \
  -e REDIS_URL=redis://DALSTON_MAC_TS_IP:6379 \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_ENDPOINT_URL=http://DALSTON_MAC_TS_IP:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  DALSTON_ECR/dalston/stt-onnx:DALSTON_TAG
BLOCK
}

apply_extra_substitutions() {
  USER_DATA="${USER_DATA//DALSTON_TAG/$TAG}"
}

# shellcheck source=_gpu-instance-lib.sh
source "$(dirname "$0")/_gpu-instance-lib.sh"
main "$@"
