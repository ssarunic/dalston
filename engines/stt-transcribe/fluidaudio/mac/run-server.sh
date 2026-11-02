#!/bin/bash
# Launch fluidaudio-server under launchd (M104).
#
# Sources the shared worker.env for FLUIDAUDIO_* settings, then execs the
# release binary built by install.sh. Runs under `caffeinate -i` so the Mac
# does not idle-sleep while the worker is up. (This does not prevent
# lid-close sleep; see docs/guides/14-mac-neural-engine-worker.md.)
set -euo pipefail

DALSTON_HOME="${DALSTON_HOME:-$HOME/.dalston/fluidaudio}"
ENV_FILE="${DALSTON_WORKER_ENV:-$DALSTON_HOME/worker.env}"
BIN="${FLUIDAUDIO_SERVER_BIN:-$DALSTON_HOME/bin/fluidaudio-server}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [ ! -x "$BIN" ]; then
  echo "missing $BIN (run install.sh to build it)" >&2
  exit 78
fi

exec /usr/bin/caffeinate -i "$BIN"
