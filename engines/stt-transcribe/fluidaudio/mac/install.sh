#!/bin/bash
# Install the Dalston FluidAudio Mac worker (M104).
#
#   bash engines/stt-transcribe/fluidaudio/mac/install.sh            # install / upgrade
#   bash engines/stt-transcribe/fluidaudio/mac/install.sh --uninstall
#
# What it does, from a checkout of the Dalston repo on an Apple Silicon Mac:
#   1. creates ~/.dalston/fluidaudio/venv (Python >= 3.11) and installs the
#      engine SDK plus the two adapters' requirements into it;
#   2. builds fluidaudio-server in release mode and copies the binary to
#      ~/.dalston/fluidaudio/bin/;
#   3. seeds ~/.dalston/fluidaudio/worker.env from worker.env.example if
#      absent (you edit it, then re-run to (re)start the agents);
#   4. renders the three launchd agents into ~/Library/LaunchAgents and
#      bootstraps them.
#
# It never touches the control plane. Stop everything with --uninstall.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$HERE/../../../.." && pwd)"
DALSTON_HOME="${DALSTON_HOME:-$HOME/.dalston/fluidaudio}"
LOG_DIR="$HOME/Library/Logs/dalston"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABELS=(com.dalston.fluidaudio-server com.dalston.fluidaudio-transcribe com.dalston.fluidaudio-diarize)
MIN_PYTHON="3.11"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

uid="$(id -u)"

bootout_all() {
  for label in "${LABELS[@]}"; do
    launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  done
}

if [ "${1:-}" = "--uninstall" ]; then
  log "Stopping launchd agents"
  bootout_all
  for label in "${LABELS[@]}"; do rm -f "$AGENTS_DIR/$label.plist"; done
  log "Agents removed. Left in place: $DALSTON_HOME (venv, binary, worker.env) and"
  log "FluidAudio's models in ~/Library/Application Support/FluidAudio/Models."
  exit 0
fi

# --- Preflight ---------------------------------------------------------------
[ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS"
[ "$(uname -m)" = "arm64" ] || die "FluidAudio needs Apple Silicon (arm64); this Mac is $(uname -m)"
command -v swift >/dev/null 2>&1 || die "swift not found: install Xcode command line tools (xcode-select --install)"
command -v tailscale >/dev/null 2>&1 || warn "tailscale CLI not found; the worker must reach the control plane over the tailnet"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"; then
      PYTHON="$candidate"; break
    fi
  done
fi
[ -n "$PYTHON" ] || die "Python >= $MIN_PYTHON not found (brew install python@3.12)"

mkdir -p "$DALSTON_HOME/bin" "$LOG_DIR" "$AGENTS_DIR"

# --- 1. Python venv with the engine SDK and both adapters ----------------------
VENV="$DALSTON_HOME/venv"
if [ ! -x "$VENV/bin/python" ]; then
  log "Creating venv at $VENV with $($PYTHON --version)"
  "$PYTHON" -m venv "$VENV"
fi
log "Installing engine SDK and adapter requirements"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO_DIR[engine-sdk]" \
  -r "$REPO_DIR/engines/stt-transcribe/fluidaudio/requirements.txt" \
  -r "$REPO_DIR/engines/stt-diarize/fluidaudio/requirements.txt"

# --- 2. Swift server ------------------------------------------------------------
log "Building fluidaudio-server (release). First build fetches FluidAudio and Vapor."
(cd "$REPO_DIR/engines/stt-transcribe/fluidaudio/server" && swift build -c release)
cp "$REPO_DIR/engines/stt-transcribe/fluidaudio/server/.build/release/fluidaudio-server" "$DALSTON_HOME/bin/fluidaudio-server"
chmod +x "$DALSTON_HOME/bin/fluidaudio-server"

# --- 3. worker.env ---------------------------------------------------------------
if [ ! -f "$DALSTON_HOME/worker.env" ]; then
  cp "$HERE/worker.env.example" "$DALSTON_HOME/worker.env"
  chmod 600 "$DALSTON_HOME/worker.env"
  log "Seeded $DALSTON_HOME/worker.env from the example."
  warn "Edit it (REDIS_URL, DALSTON_S3_BUCKET, AWS_PROFILE, DALSTON_WORKER_ID), then re-run this script to start the agents."
  exit 0
fi
if grep -q "CHANGE-ME" "$DALSTON_HOME/worker.env"; then
  die "$DALSTON_HOME/worker.env still contains CHANGE-ME placeholders"
fi

# --- 4. launchd agents -----------------------------------------------------------
log "Rendering launchd agents into $AGENTS_DIR"
bootout_all
for label in "${LABELS[@]}"; do
  sed -e "s|__REPO_DIR__|$REPO_DIR|g" \
      -e "s|__DALSTON_HOME__|$DALSTON_HOME|g" \
      -e "s|__LOG_DIR__|$LOG_DIR|g" \
      "$HERE/$label.plist.template" > "$AGENTS_DIR/$label.plist"
  plutil -lint "$AGENTS_DIR/$label.plist" >/dev/null
  launchctl bootstrap "gui/$uid" "$AGENTS_DIR/$label.plist"
done

log "Installed. Agents:"
for label in "${LABELS[@]}"; do
  printf '    %s\n' "$label"
done
cat <<EOF

Next:
  tail -f $LOG_DIR/fluidaudio-server.log        # model download/compile on first start
  curl -s http://127.0.0.1:9200/health | jq .
  # On the control plane: Models page -> Pull the three fluidaudio/* models,
  # then check /v1/engines for fluidaudio and fluidaudio-pyannote.

Stop / start:
  launchctl bootout gui/$uid/com.dalston.fluidaudio-transcribe   (and -diarize, -server)
  launchctl bootstrap gui/$uid $AGENTS_DIR/com.dalston.fluidaudio-transcribe.plist
Uninstall:
  bash $HERE/install.sh --uninstall
EOF
