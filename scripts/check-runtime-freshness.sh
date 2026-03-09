#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/check-runtime-freshness.sh [--require-running]

Checks that running Dalston containers were created with the current git revision
via DALSTON_RUNTIME_REVISION.

Options:
  --require-running   Fail if no Dalston containers are currently running.
EOF
}

require_running=0
if [[ "${1:-}" == "--require-running" ]]; then
  require_running=1
  shift
fi

if [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for runtime freshness checks." >&2
  exit 1
fi

expected_revision="${DALSTON_RUNTIME_REVISION:-$(git rev-parse HEAD)}"
services="$(docker compose ps --status running --services 2>/dev/null || true)"

if [[ -z "$services" ]]; then
  if [[ "$require_running" -eq 1 ]]; then
    echo "No running services found in docker compose." >&2
    echo "Start a stack first (for example: make dev-minimal)." >&2
    exit 1
  fi
  echo "No running services found; runtime freshness check skipped."
  exit 0
fi

checked=0
stale=0

while IFS= read -r service; do
  [[ -z "$service" ]] && continue

  container_id="$(docker compose ps -q "$service" 2>/dev/null | head -n1)"
  [[ -z "$container_id" ]] && continue

  image="$(docker inspect --format '{{.Config.Image}}' "$container_id" 2>/dev/null || true)"
  case "$image" in
    dalston/*) ;;
    *) continue ;;
  esac

  checked=$((checked + 1))

  runtime_revision="$(
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" 2>/dev/null \
      | awk -F= '/^DALSTON_RUNTIME_REVISION=/{print $2; exit}'
  )"

  if [[ -z "$runtime_revision" ]]; then
    stale=1
    echo "STALE: $service ($image) is missing DALSTON_RUNTIME_REVISION."
    continue
  fi

  if [[ "$runtime_revision" != "$expected_revision" ]]; then
    stale=1
    echo "STALE: $service ($image) revision=$runtime_revision expected=$expected_revision"
  fi
done <<< "$services"

if [[ "$checked" -eq 0 ]]; then
  if [[ "$require_running" -eq 1 ]]; then
    echo "No running Dalston-managed containers found (image prefix dalston/*)." >&2
    exit 1
  fi
  echo "No running Dalston-managed containers found; runtime freshness check skipped."
  exit 0
fi

if [[ "$stale" -ne 0 ]]; then
  echo ""
  echo "Runtime freshness check failed."
  echo "To rebuild running services with the current revision, run:"
  echo "  make sync-test-stack"
  exit 1
fi

echo "Runtime freshness OK: $checked Dalston container(s) at revision $expected_revision"
