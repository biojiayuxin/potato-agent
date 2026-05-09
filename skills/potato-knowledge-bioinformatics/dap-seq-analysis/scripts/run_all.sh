#!/usr/bin/env bash
# DAP-Seq Snakemake entrypoint.
# Usage:
#   bash scripts/run_all.sh              # dry-run first, then run with default cores from config
#   bash scripts/run_all.sh --dry-run    # only dry-run
#   bash scripts/run_all.sh --cores 24   # run with 24 Snakemake cores
#   bash scripts/run_all.sh --config path/to/config.yaml

set -euo pipefail

MODE="run"
CORES=""
CONFIG="config/config.yaml"

usage() {
  printf '%s\n' \
    'Usage: bash scripts/run_all.sh [--dry-run] [--cores N] [--config config.yaml]' \
    '' \
    'Runs snakemake -n first. If dry-run succeeds and --dry-run is not set,' \
    'runs the DAP-Seq workflow with --rerun-incomplete --printshellcmds.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) MODE="dry-run"; shift ;;
    --cores|-j) CORES="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v snakemake >/dev/null 2>&1 || { echo "ERROR: command not found: snakemake" >&2; exit 127; }

if [[ -z "$CORES" ]]; then
  CORES=$(python3 - "$CONFIG" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
print(cfg.get('threads', 8))
PY
)
fi

echo "[DAP-Seq] Snakemake dry-run"
snakemake -s Snakefile --configfile "$CONFIG" -n --printshellcmds

if [[ "$MODE" == "dry-run" ]]; then
  exit 0
fi

echo "[DAP-Seq] Snakemake run with --cores $CORES"
snakemake -s Snakefile --configfile "$CONFIG" --cores "$CORES" --rerun-incomplete --printshellcmds --show-failed-logs
