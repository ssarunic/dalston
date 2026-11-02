#!/bin/bash
# Launch one Dalston FluidAudio adapter under launchd (M104).
#
# Usage: run-adapter.sh transcribe|diarize
#
# launchd has no env-file directive, so this wrapper sources the shared
# worker.env, adds the per-adapter values (metrics port, worker id suffix)
# and execs the engine from its own directory. The working directory
# matters: the SDK resolves engine.yaml from /etc/dalston/engine.yaml or
# ./engine.yaml.
set -euo pipefail

STAGE="${1:-}"
case "$STAGE" in
  transcribe)
    ENGINE_DIR_REL="engines/stt-transcribe/fluidaudio"
    METRICS_PORT=9100
    SUFFIX="tx"
    ;;
  diarize)
    ENGINE_DIR_REL="engines/stt-diarize/fluidaudio"
    METRICS_PORT=9101
    SUFFIX="dz"
    ;;
  *)
    echo "usage: $0 transcribe|diarize" >&2
    exit 64
    ;;
esac

DALSTON_HOME="${DALSTON_HOME:-$HOME/.dalston/fluidaudio}"
ENV_FILE="${DALSTON_WORKER_ENV:-$DALSTON_HOME/worker.env}"
REPO_DIR="${DALSTON_REPO_DIR:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
VENV="${DALSTON_VENV:-$DALSTON_HOME/venv}"

if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE (copy worker.env.example and edit it)" >&2
  exit 78
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Per-adapter values. Two adapters on one host must not share a metrics
# port: the runner silently skips binding when the port is taken.
export DALSTON_METRICS_PORT="$METRICS_PORT"
# Keep the base id short (the SDK keeps 12 chars) but distinct per stage.
export DALSTON_WORKER_ID="${DALSTON_WORKER_ID:-$(hostname -s)}-$SUFFIX"
export DALSTON_HOST_HOSTNAME="${DALSTON_HOST_HOSTNAME:-$(hostname -s)}"
export DALSTON_DEPLOY_ENV="${DALSTON_DEPLOY_ENV:-mac}"

cd "$REPO_DIR/$ENGINE_DIR_REL"
exec "$VENV/bin/python" engine.py
