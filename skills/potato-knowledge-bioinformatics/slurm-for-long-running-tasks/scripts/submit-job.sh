#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

need_cmd sbatch

usage() {
  cat <<'EOF'
Usage:
  submit-job.sh --job-name NAME --cpus N --mem-gb N --time TIME --command 'cmd'
  submit-job.sh --job-name NAME --cpus N --mem-gb N --time TIME --script job.slurm

Required:
  --job-name NAME       Slurm job name
  --mem-gb N            Memory request in GiB (2–100)
  --time TIME           Slurm time limit, e.g. 02:00:00 or 1-00:00:00

Exactly one of:
  --command 'cmd'       Submit a shell command via sbatch --wrap
  --script PATH         Submit an existing batch script

Optional:
  --cpus N              CPUs per task, default 1
  --partition NAME      Partition name, default main
  --workdir PATH        Working directory, default current directory
  --output PATH         Stdout path, default WORKDIR/slurm-%j.out
  --error PATH          Stderr path, default follows Slurm stdout behavior
  --print-only          Print the resolved sbatch command and exit
  --help                Show this help text
EOF
}

job_name=""
cpus=1
mem_gb=""
time_limit=""
partition="$SLURM_DEFAULT_PARTITION"
workdir="$PWD"
output=""
error_file=""
command_string=""
script_path=""
print_only=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job-name)
      [[ $# -ge 2 ]] || die "--job-name requires a value"
      job_name="$2"
      shift 2
      ;;
    --cpus)
      [[ $# -ge 2 ]] || die "--cpus requires a value"
      cpus="$2"
      shift 2
      ;;
    --mem-gb)
      [[ $# -ge 2 ]] || die "--mem-gb requires a value"
      mem_gb="$2"
      shift 2
      ;;
    --time)
      [[ $# -ge 2 ]] || die "--time requires a value"
      time_limit="$2"
      shift 2
      ;;
    --partition)
      [[ $# -ge 2 ]] || die "--partition requires a value"
      partition="$2"
      shift 2
      ;;
    --workdir)
      [[ $# -ge 2 ]] || die "--workdir requires a value"
      workdir="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || die "--output requires a value"
      output="$2"
      shift 2
      ;;
    --error)
      [[ $# -ge 2 ]] || die "--error requires a value"
      error_file="$2"
      shift 2
      ;;
    --command)
      [[ $# -ge 2 ]] || die "--command requires a value"
      command_string="$2"
      shift 2
      ;;
    --script)
      [[ $# -ge 2 ]] || die "--script requires a value"
      script_path="$2"
      shift 2
      ;;
    --print-only)
      print_only=1
      shift
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

[[ -n "$job_name" ]] || die "--job-name is required"
[[ -n "$time_limit" ]] || die "--time is required"
[[ -n "$mem_gb" ]] || die "--mem-gb is required"
is_positive_int "$cpus" || die "--cpus must be a positive integer"
is_positive_int "$mem_gb" || die "--mem-gb must be a positive integer"

if [[ -n "$command_string" && -n "$script_path" ]]; then
  die "choose only one of --command or --script"
fi
if [[ -z "$command_string" && -z "$script_path" ]]; then
  die "one of --command or --script is required"
fi

mem_mb=$((mem_gb * 1024))
(( mem_mb <= SLURM_MAX_MEM_MB )) || die "--mem-gb exceeds cluster limit of 100 GiB"

workdir="$(abs_path "$workdir")"
[[ -d "$workdir" ]] || die "workdir does not exist: $workdir"

if [[ -n "$script_path" ]]; then
  script_path="$(abs_path "$script_path")"
  [[ -f "$script_path" ]] || die "script does not exist: $script_path"
fi

if [[ -z "$output" ]]; then
  output="$workdir/slurm-%j.out"
fi

sbatch_args=(
  "--parsable"
  "--partition=$partition"
  "--job-name=$job_name"
  "--cpus-per-task=$cpus"
  "--mem=$mem_mb"
  "--time=$time_limit"
  "--chdir=$workdir"
  "--output=$output"
)

if [[ -n "$error_file" ]]; then
  sbatch_args+=("--error=$error_file")
fi

if [[ -n "$command_string" ]]; then
  sbatch_args+=("--wrap=$command_string")
else
  sbatch_args+=("$script_path")
fi

if (( print_only )); then
  printf 'Resolved sbatch command:\n'
  printf 'sbatch'
  for arg in "${sbatch_args[@]}"; do
    printf ' %q' "$arg"
  done
  printf '\n'
  exit 0
fi

submit_out="$(sbatch "${sbatch_args[@]}")"
job_id="${submit_out%%;*}"

echo "Submitted job: $job_id"
echo "Partition: $partition"
echo "Workdir: $workdir"
echo "Output: $output"
squeue --jobs "$job_id" --Format=JobID,Partition,Name,UserName,State,TimeUsed,TimeLimit,NumCPUs,Reason
