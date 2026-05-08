#!/usr/bin/env bash
set -euo pipefail

SLURM_DEFAULT_PARTITION="${SLURM_DEFAULT_PARTITION:-main}"
SLURM_MAX_MEM_MB="${SLURM_MAX_MEM_MB:-102400}"

die() {
  echo "Error: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

is_positive_int() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

abs_path() {
  local path="$1"
  readlink -f "$path"
}
