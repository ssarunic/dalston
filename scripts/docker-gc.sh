#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/docker-gc.sh <soft|hard|auto>

Modes:
  soft  - Safe, frequent cleanup (containers/networks dangling images + old builder cache)
  hard  - soft + remove unused dalston/* images + deeper builder cleanup
  auto  - run soft, then hard only if free disk remains below threshold

Environment:
  DALSTON_DOCKER_MIN_FREE_GB            Minimum free GB required after auto GC (default: 15)
  DALSTON_DOCKER_SOFT_BUILDER_UNTIL     Builder cache age for soft GC (default: 168h)
  DALSTON_DOCKER_HARD_BUILDER_UNTIL     Builder cache age for hard GC (default: 336h)
  DALSTON_DOCKER_GC_DRY_RUN             Set to 1 to print commands without executing
EOF
}

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

mode="$1"
case "$mode" in
  soft|hard|auto) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Docker GC." >&2
  exit 1
fi

min_free_gb="${DALSTON_DOCKER_MIN_FREE_GB:-15}"
soft_builder_until="${DALSTON_DOCKER_SOFT_BUILDER_UNTIL:-168h}"
hard_builder_until="${DALSTON_DOCKER_HARD_BUILDER_UNTIL:-336h}"
dry_run="${DALSTON_DOCKER_GC_DRY_RUN:-0}"

log() {
  echo "$*"
}

run_cmd() {
  if [[ "$dry_run" == "1" ]]; then
    log "[dry-run] $*"
    return 0
  fi
  "$@"
}

free_gb() {
  # POSIX-ish disk free for current filesystem.
  df -Pk . | awk 'NR==2 {printf "%d", $4/1024/1024}'
}

collect_running_dalston_image_ids() {
  local container_ids
  container_ids="$(docker ps -aq --filter label=com.docker.compose.project=dalston || true)"
  if [[ -z "$container_ids" ]]; then
    return 0
  fi

  while IFS= read -r cid; do
    [[ -z "$cid" ]] && continue
    docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true
  done <<< "$container_ids" | sort -u
}

collect_all_dalston_image_ids() {
  docker image ls --format '{{.Repository}} {{.ID}}' \
    | awk '$1 ~ /^dalston\// {print $2}' \
    | sort -u
}

prune_unused_dalston_images() {
  local used_ids
  used_ids="$(collect_running_dalston_image_ids)"
  local all_ids
  all_ids="$(collect_all_dalston_image_ids)"

  if [[ -z "$all_ids" ]]; then
    log "No dalston/* images found to prune."
    return 0
  fi

  local to_remove=()
  while IFS= read -r image_id; do
    [[ -z "$image_id" ]] && continue
    if grep -qx "$image_id" <<< "$used_ids"; then
      continue
    fi
    to_remove+=("$image_id")
  done <<< "$all_ids"

  if [[ "${#to_remove[@]}" -eq 0 ]]; then
    log "No unused dalston/* images to remove."
    return 0
  fi

  log "Removing ${#to_remove[@]} unused dalston/* image(s)..."
  run_cmd docker image rm -f "${to_remove[@]}" || true
}

soft_gc() {
  log "Running soft Docker GC..."
  run_cmd docker container prune -f
  run_cmd docker image prune -f
  run_cmd docker network prune -f
  run_cmd docker builder prune -f --filter "until=${soft_builder_until}"
}

hard_gc() {
  log "Running hard Docker GC..."
  soft_gc
  prune_unused_dalston_images
  run_cmd docker builder prune -a -f --filter "until=${hard_builder_until}"
}

auto_gc() {
  local before after
  before="$(free_gb)"
  log "Free disk before Docker GC: ${before}GB"

  soft_gc
  after="$(free_gb)"
  if (( after < min_free_gb )); then
    log "Free disk (${after}GB) below threshold (${min_free_gb}GB), escalating to hard GC..."
    hard_gc
    after="$(free_gb)"
  fi

  if (( after < min_free_gb )); then
    echo "Docker GC finished but free disk is still low (${after}GB < ${min_free_gb}GB)." >&2
    echo "Free up additional disk space before running heavy test stacks." >&2
    exit 1
  fi

  log "Free disk after Docker GC: ${after}GB"
}

case "$mode" in
  soft) soft_gc ;;
  hard) hard_gc ;;
  auto) auto_gc ;;
esac
