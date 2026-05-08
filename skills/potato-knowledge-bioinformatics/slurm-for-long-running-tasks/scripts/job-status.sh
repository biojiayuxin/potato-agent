#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

need_cmd squeue
need_cmd scontrol

usage() {
  cat <<'EOF'
Usage:
  job-status.sh JOBID [--details]

Options:
  --details   Also print full `scontrol show job`
  --help      Show this help text
EOF
}

job_id=""
show_details=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --details)
      show_details=1
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

summary_format="%.18i %.9P %.24j %.10u %.10T %.10M %.10l %.6C %R"
summary="$(squeue --jobs "$job_id" --noheader --format "$summary_format" 2>/dev/null || true)"

if [[ -n "$summary" ]]; then
  echo "Active queue entry:"
  echo "$summary"
  echo
  echo "Controller view:"
  scontrol show job "$job_id"
  exit 0
fi

echo "Job $job_id is not in the active queue."
echo "This server currently has Slurm accounting disabled, so completed or failed historical jobs are not queryable with sacct."
echo "If the job finished, inspect its stdout/stderr files in the job work directory."

if (( show_details )); then
  echo
  echo "No active controller record is available for job $job_id."
fi

exit 3
