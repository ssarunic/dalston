#!/usr/bin/env bash
# Calibrate VRAM profiles for the co-located NeMo + Pyannote GPU box.
#
# Run this ON THE GPU INSTANCE (after `dalston-aws ssh nemo-pyannote`)
# once both engines are up and have preloaded their models. It:
#
#   1. Detects the GPU name via nvidia-smi (used in the profile filename).
#   2. Installs the calibration deps (pynvml, requests) into each engine
#      container — they're not in the runtime image but the dalston
#      package + numpy already are, so calibrate_vram is importable.
#   3. Runs `python -m dalston.tools.calibrate_vram` against each
#      engine's HTTP endpoint (NeMo on 9100, Pyannote on 9101).
#   4. Writes the resulting JSON profiles to /data/vram_profiles/ on
#      the host — bind-mounted read-only into both containers.
#   5. Restarts both containers via systemd so they pick up the
#      profiles on next boot.
#
# Modes (set via MODE env var or --mode flag):
#
#   solo        Default. Runs each engine in turn with the other still
#               serving traffic. Emits peak-VRAM data only.
#
#   coloc       M89.2.2 subject/background-paused protocol. For each
#               engine in turn: `docker pause` the other container so
#               its model weights stay resident but it doesn't compete
#               for GPU time, then run `--throughput-sweep --mode
#               coloc:<other_preset_key>` against the subject. Trap
#               handlers unpause on Ctrl-C / unexpected exit so the
#               box doesn't end up half-paused.
#
# Idempotent: safe to re-run. The calibrator now merges throughput
# results across modes in the same profile JSON, so re-running this
# script with MODE=solo and then MODE=coloc populates both blocks.

set -euo pipefail

NEMO_CONTAINER="${NEMO_CONTAINER:-stt-transcribe-nemo}"
PYANNOTE_CONTAINER="${PYANNOTE_CONTAINER:-stt-diarize-pyannote-4-0}"
NEMO_PORT="${NEMO_PORT:-9100}"
PYANNOTE_PORT="${PYANNOTE_PORT:-9101}"
PROFILE_DIR="${PROFILE_DIR:-/data/vram_profiles}"
NEMO_MODEL="${NEMO_MODEL:-nvidia/parakeet-tdt-0.6b-v3}"
PYANNOTE_MODEL="${PYANNOTE_MODEL:-pyannote/speaker-diarization-community-1}"
MODE="${MODE:-solo}"
SAFETY_MARGIN="${SAFETY_MARGIN:-0.85}"

# Parse --mode <value> if present (overrides env var).
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --mode=*)
      MODE="${1#--mode=}"
      shift
      ;;
    --safety-margin)
      SAFETY_MARGIN="$2"
      shift 2
      ;;
    --safety-margin=*)
      SAFETY_MARGIN="${1#--safety-margin=}"
      shift
      ;;
    -h|--help)
      sed -n '1,33p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

if [[ "$MODE" != "solo" && "$MODE" != "coloc" ]]; then
  echo "MODE must be 'solo' or 'coloc' (got: $MODE)" >&2
  exit 64
fi

GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1 | awk '{print $NF}')"
if [[ -z "$GPU_NAME" ]]; then
  echo "Could not detect GPU via nvidia-smi" >&2
  exit 1
fi
echo "GPU detected: $GPU_NAME (mode: $MODE)"

sudo mkdir -p "$PROFILE_DIR"
sudo chown "$(id -u):$(id -g)" "$PROFILE_DIR"

# Track which containers we paused / stopped so the trap can restore
# them on any exit path (including Ctrl-C). Setting these to empty
# after the inverse op keeps the trap idempotent.
PAUSED_CONTAINERS=""
STOPPED_CONTAINERS=""

cleanup() {
  local rc=$?
  if [[ -n "$PAUSED_CONTAINERS" ]]; then
    echo
    echo "Cleanup: unpausing $PAUSED_CONTAINERS"
    for c in $PAUSED_CONTAINERS; do
      docker unpause "$c" >/dev/null 2>&1 || true
    done
  fi
  if [[ -n "$STOPPED_CONTAINERS" ]]; then
    echo
    echo "Cleanup: starting $STOPPED_CONTAINERS"
    for c in $STOPPED_CONTAINERS; do
      docker start "$c" >/dev/null 2>&1 || true
    done
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

pause_container() {
  local container="$1"
  echo "Pausing $container (model weights stay resident, no GPU work in flight)..."
  docker pause "$container" >/dev/null
  PAUSED_CONTAINERS="$PAUSED_CONTAINERS $container"
  # Brief settle to let any in-flight HTTP requests finish before the
  # subject sweep starts measuring.
  sleep 2
}

unpause_container() {
  local container="$1"
  echo "Unpausing $container..."
  docker unpause "$container" >/dev/null
  PAUSED_CONTAINERS="${PAUSED_CONTAINERS// $container/}"
}

# Stop a container fully so its GPU memory is released. Used to capture
# a clean "other engine alone" baseline before a solo sweep — pause
# alone keeps weights resident, so it can't separate engines on the GPU.
stop_container() {
  local container="$1"
  echo "Stopping $container (releases GPU memory; takes ~10s)..."
  docker stop "$container" >/dev/null
  STOPPED_CONTAINERS="$STOPPED_CONTAINERS $container"
  # Wait for nvml to see the freed VRAM before any measurement.
  sleep 10
}

start_container() {
  local container="$1"
  local port="$2"
  local label="$3"
  echo "Starting $container..."
  docker start "$container" >/dev/null
  STOPPED_CONTAINERS="${STOPPED_CONTAINERS// $container/}"
  wait_for_health "$port" "$label"
}

wait_for_health() {
  local port="$1"
  local label="$2"
  echo "Waiting for $label on :$port to be ready..."
  for _ in $(seq 1 60); do
    if curl -sf "http://localhost:${port}/health" >/dev/null 2>&1; then
      echo "  $label ready."
      return 0
    fi
    sleep 5
  done
  echo "  $label did not become ready in 5 minutes" >&2
  return 1
}

install_calib_deps() {
  local container="$1"
  echo "Installing calibration deps in $container..."
  docker exec "$container" pip install --quiet --no-cache-dir \
    --break-system-packages nvidia-ml-py3 requests >/dev/null
}

# Runs the calibrator inside ``container`` against ``http://localhost:$port``
# and copies the resulting profile JSON to ``out_host``. Extra args are
# passed verbatim — used to inject ``--mode`` / ``--throughput-sweep`` etc.
run_calib() {
  local container="$1"
  local engine_id="$2"
  local stage="$3"
  local model_id="$4"
  local port="$5"
  local out_host="$6"
  shift 6
  local extra_args=("$@")

  local basename
  basename="$(basename "$out_host")"
  echo
  echo "=== Calibrating $engine_id ($model_id) on $GPU_NAME (${extra_args[*]:-no extra args}) ==="
  # Profile dir is mounted read-only — write to /tmp inside the container,
  # then cp to the host path. The calibrator's merge-write step reads the
  # existing host profile if present, so we also push it in beforehand
  # to preserve modes from earlier runs.
  if [[ -f "$out_host" ]]; then
    docker cp "$out_host" "${container}:/tmp/${basename}"
  fi
  docker exec "$container" python -m dalston.tools.calibrate_vram \
    --engine-url "http://localhost:${port}" \
    --stage "$stage" \
    --engine-id "$engine_id" \
    --model-id "$model_id" \
    --gpu-id 0 \
    --output "/tmp/${basename}" \
    "${extra_args[@]}"
  docker cp "${container}:/tmp/${basename}" "$out_host"
  echo "Wrote $out_host"
}

wait_for_health "$NEMO_PORT" "NeMo"
wait_for_health "$PYANNOTE_PORT" "Pyannote"

install_calib_deps "$NEMO_CONTAINER"
install_calib_deps "$PYANNOTE_CONTAINER"

NEMO_PROFILE="${PROFILE_DIR}/transcribe-nemo-${GPU_NAME}.json"
PYANNOTE_PROFILE="${PROFILE_DIR}/diarize-pyannote-4.0-${GPU_NAME}.json"

if [[ "$MODE" = "solo" ]]; then
  # Legacy behaviour: peak-VRAM sweep against each engine in turn. Other
  # engine keeps serving traffic — peaks reflect coloc reality but no
  # throughput data is emitted.
  run_calib "$NEMO_CONTAINER" "nemo" "transcribe" "$NEMO_MODEL" "$NEMO_PORT" \
    "$NEMO_PROFILE"
  run_calib "$PYANNOTE_CONTAINER" "pyannote-4.0" "diarize" "$PYANNOTE_MODEL" \
    "$PYANNOTE_PORT" "$PYANNOTE_PROFILE"
else
  # M89.2.2 subject/background-paused protocol.
  # NeMo subject, pyannote paused.
  pause_container "$PYANNOTE_CONTAINER"
  run_calib "$NEMO_CONTAINER" "nemo" "transcribe" "$NEMO_MODEL" "$NEMO_PORT" \
    "$NEMO_PROFILE" \
    --throughput-sweep --safety-margin "$SAFETY_MARGIN" --mode "coloc:pyannote"
  unpause_container "$PYANNOTE_CONTAINER"

  # Pyannote subject, nemo paused.
  pause_container "$NEMO_CONTAINER"
  run_calib "$PYANNOTE_CONTAINER" "pyannote-4.0" "diarize" "$PYANNOTE_MODEL" \
    "$PYANNOTE_PORT" "$PYANNOTE_PROFILE" \
    --throughput-sweep --safety-margin "$SAFETY_MARGIN" --mode "coloc:nemo"
  unpause_container "$NEMO_CONTAINER"
fi

echo
echo "Profiles written to $PROFILE_DIR:"
ls -la "$PROFILE_DIR"

echo
echo "Restarting engines so the budget calculator picks up the new profiles..."
sudo systemctl restart dalston-gpu.service
echo "Done. Tail logs with:  docker logs -f $NEMO_CONTAINER"
echo "Look for log lines: vram_profile_loaded / vram_budget_computed (profile_source=calibrated)."
