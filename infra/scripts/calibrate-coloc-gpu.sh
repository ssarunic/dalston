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
# Idempotent: safe to re-run. Existing profiles are overwritten.

set -euo pipefail

NEMO_CONTAINER="${NEMO_CONTAINER:-stt-transcribe-nemo}"
PYANNOTE_CONTAINER="${PYANNOTE_CONTAINER:-stt-diarize-pyannote-4-0}"
NEMO_PORT="${NEMO_PORT:-9100}"
PYANNOTE_PORT="${PYANNOTE_PORT:-9101}"
PROFILE_DIR="${PROFILE_DIR:-/data/vram_profiles}"
NEMO_MODEL="${NEMO_MODEL:-nvidia/parakeet-tdt-0.6b-v3}"
PYANNOTE_MODEL="${PYANNOTE_MODEL:-pyannote/speaker-diarization-community-1}"

GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1 | awk '{print $NF}')"
if [[ -z "$GPU_NAME" ]]; then
  echo "Could not detect GPU via nvidia-smi" >&2
  exit 1
fi
echo "GPU detected: $GPU_NAME"

sudo mkdir -p "$PROFILE_DIR"
sudo chown "$(id -u):$(id -g)" "$PROFILE_DIR"

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

run_calib() {
  local container="$1"
  local engine_id="$2"
  local stage="$3"
  local model_id="$4"
  local port="$5"
  local out_host="$6"

  local out_in_container="/data/vram_profiles/$(basename "$out_host")"
  echo
  echo "=== Calibrating $engine_id ($model_id) on $GPU_NAME ==="
  # Profile dir is mounted read-only — write to /tmp inside the container,
  # then cp to the host path with sudo.
  docker exec "$container" python -m dalston.tools.calibrate_vram \
    --engine-url "http://localhost:${port}" \
    --stage "$stage" \
    --engine-id "$engine_id" \
    --model-id "$model_id" \
    --gpu-id 0 \
    --output "/tmp/$(basename "$out_host")"
  docker cp "${container}:/tmp/$(basename "$out_host")" "$out_host"
  echo "Wrote $out_host"
}

wait_for_health "$NEMO_PORT" "NeMo"
wait_for_health "$PYANNOTE_PORT" "Pyannote"

install_calib_deps "$NEMO_CONTAINER"
install_calib_deps "$PYANNOTE_CONTAINER"

run_calib "$NEMO_CONTAINER" "nemo" "transcribe" "$NEMO_MODEL" "$NEMO_PORT" \
  "${PROFILE_DIR}/transcribe-nemo-${GPU_NAME}.json"

run_calib "$PYANNOTE_CONTAINER" "pyannote-4.0" "diarize" "$PYANNOTE_MODEL" \
  "$PYANNOTE_PORT" "${PROFILE_DIR}/diarize-pyannote-4.0-${GPU_NAME}.json"

echo
echo "Profiles written to $PROFILE_DIR:"
ls -la "$PROFILE_DIR"

echo
echo "Restarting engines so the budget calculator picks up the new profiles..."
sudo systemctl restart dalston-gpu.service
echo "Done. Tail logs with:  docker logs -f $NEMO_CONTAINER"
echo "Look for log lines: vram_profile_loaded / vram_budget_computed (profile_source=calibrated)."
