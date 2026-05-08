#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

need_cmd squeue

usage() {
  cat <<'EOF'
Usage:
  list-jobs.sh [--user USER] [--states STATES] [--watch SECONDS]

Options:
  --user USER        Username to query, default current user
  --states STATES    Comma-separated states, default PENDING,RUNNING
  --watch SECONDS    Refresh every N seconds
  --help             Show this help text
EOF
}

target_user="${USER:-$(id -un)}"
states="PENDING,RUNNING"
watch_seconds=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      [[ $# -ge 2 ]] || die "--user requires a value"
      target_user="$2"
      shift 2
      ;;
    --states)
      [[ $# -ge 2 ]] || die "--states requires a value"
      states="$2"
      shift 2
      ;;
    --watch)
      [[ $# -ge 2 ]] || die "--watch requires a value"
      watch_seconds="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

format="%.18i %.9P %.24j %.10u %.10T %.10M %.10l %.6C %R"

if [[ -n "$watch_seconds" ]]; then
  is_positive_int "$watch_seconds" || die "--watch must be a positive integer"
  exec squeue --user "$target_user" --states "$states" --iterate "$watch_seconds" --format "$format"
fi

squeue --user "$target_user" --states "$states" --format "$format"
