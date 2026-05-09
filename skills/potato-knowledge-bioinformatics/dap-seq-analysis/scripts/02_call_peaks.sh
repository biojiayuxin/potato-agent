#!/usr/bin/env bash
# Call peaks for one DAP-Seq target with MACS2.
# Designed for Snakemake rule call_peaks.

set -euo pipefail

TARGET_ID=""
CONTROL_BAM=""
OUTDIR=""
NAME=""
GENOME_SIZE="8e+8"
FORMAT="BAMPE"
CALL_SUMMITS="true"
BDG="true"
EXTRA=""
TREATMENT_BAMS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-id) TARGET_ID="$2"; shift 2 ;;
    --treatment-bams)
      shift
      while [[ $# -gt 0 && "$1" != --* ]]; do
        TREATMENT_BAMS+=("$1")
        shift
      done
      ;;
    --control-bam) CONTROL_BAM="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --genome-size) GENOME_SIZE="$2"; shift 2 ;;
    --format) FORMAT="$2"; shift 2 ;;
    --call-summits) CALL_SUMMITS="$2"; shift 2 ;;
    --bdg) BDG="$2"; shift 2 ;;
    --extra) EXTRA="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --target-id ERF1 --treatment-bams rep1.flt.bam rep2.flt.bam --control-bam input.flt.bam --outdir results/02-callpeaks/ERF1 --name ERF1"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

NAME=${NAME:-$TARGET_ID}
if [[ -z "$TARGET_ID" || -z "$CONTROL_BAM" || -z "$OUTDIR" || ${#TREATMENT_BAMS[@]} -eq 0 ]]; then
  echo "ERROR: --target-id, --treatment-bams, --control-bam and --outdir are required" >&2
  exit 2
fi
for bam in "${TREATMENT_BAMS[@]}" "$CONTROL_BAM"; do
  if [[ ! -s "$bam" ]]; then
    echo "ERROR: BAM not found or empty: $bam" >&2
    exit 2
  fi
done

command -v macs2 >/dev/null 2>&1 || { echo "ERROR: command not found: macs2" >&2; exit 127; }
mkdir -p "$OUTDIR"

summit_opt=()
bdg_opt=()
[[ "$CALL_SUMMITS" == "true" || "$CALL_SUMMITS" == "yes" || "$CALL_SUMMITS" == "1" ]] && summit_opt=(--call-summits)
[[ "$BDG" == "true" || "$BDG" == "yes" || "$BDG" == "1" ]] && bdg_opt=(-B)

echo "[DAP-Seq] MACS2 callpeak target: $TARGET_ID"
# shellcheck disable=SC2206 # EXTRA intentionally supports a small argument string from config.
extra_args=($EXTRA)
macs2 callpeak \
  -t "${TREATMENT_BAMS[@]}" \
  -c "$CONTROL_BAM" \
  -g "$GENOME_SIZE" \
  --outdir "$OUTDIR" \
  -n "$NAME" \
  "${summit_opt[@]}" \
  "${bdg_opt[@]}" \
  -f "$FORMAT" \
  "${extra_args[@]}"

echo "[DAP-Seq] MACS2 output directory: $OUTDIR"
