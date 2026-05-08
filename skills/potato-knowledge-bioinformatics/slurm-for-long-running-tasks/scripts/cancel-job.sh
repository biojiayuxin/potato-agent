#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

need_cmd squeue
need_cmd scancel

usage() {
  cat <<'EOF'
Usage:
  cancel-job.sh [--yes] JOBID

Options:
  --yes       Skip interactive confirmation
  --help      Show this help text
EOF
}

job_id=""
assume_yes=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      assume_yes=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$job_id" ]]; then
        job_id="$1"
        shift
      else
        die "unexpected argument: $1"
      fi
      ;;
  esac
done

[[ -n "$job_id" ]] || die "JOBID is required"

owner="$(squeue --jobs "$job_id" --noheader --format "%u" 2>/dev/null || true)"
if [[ -z "$owner" ]]; then
  die "job $job_id is not in the active queue"
fi

current_user="${USER:-$(id -un)}"
[[ "$owner" == "$current_user" ]] || die "job $job_id belongs to $owner, not $current_user"

if (( ! assume_yes )); then
  if [[ ! -t 0 ]]; then
    die "refusing to cancel without --yes in non-interactive mode"
  fi
  read -r -p "Cancel job $job_id owned by $current_user? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES)
      ;;
    *)
      echo "Canceled by user."
      exit 0
      ;;
  esac
fi

scancel "$job_id"
echo "Canceled job $job_id."
