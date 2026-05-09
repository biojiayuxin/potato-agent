#!/usr/bin/env bash
# Run ChIPseeker post-processing for one target.

set -euo pipefail

TARGET_ID=""
INPUT=""
OUTDIR=""
CONFIG="config/config.yaml"
BASE_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-id|--sample-id) TARGET_ID="$2"; shift 2 ;;
    --input) INPUT="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --base-dir) BASE_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash scripts/run_chipseeker_summary.sh --target-id ERF1 --input results/03-chipseeker/ERF1.anno.with_intergenic.txt --outdir results/03-chipseeker [--config config/config.yaml] [--base-dir .]"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$TARGET_ID" || -z "$INPUT" || -z "$OUTDIR" ]]; then
  echo "ERROR: --target-id, --input and --outdir are required" >&2
  exit 2
fi
if [[ -z "$BASE_DIR" ]]; then
  BASE_DIR="$(pwd)"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/06_sum_chipseeker.py" \
  --config "$CONFIG" \
  --base-dir "$BASE_DIR" \
  --sample-id "$TARGET_ID" \
  --input "$INPUT" \
  --outdir "$OUTDIR"
