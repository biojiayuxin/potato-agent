#!/usr/bin/env bash
# Map one paired-end DAP-Seq sample and generate sorted, duplicate-removed,
# mapped-only BAM files. Designed for Snakemake rule map_sample.

set -euo pipefail

SAMPLE_ID=""
R1=""
R2=""
INDEX_PREFIX=""
THREADS=8
SORT_BAM=""
RMDUP_BAM=""
FLT_BAM=""
DEDUP_METHOD="rmdup"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample-id) SAMPLE_ID="$2"; shift 2 ;;
    --r1) R1="$2"; shift 2 ;;
    --r2) R2="$2"; shift 2 ;;
    --index-prefix) INDEX_PREFIX="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    --sort-bam) SORT_BAM="$2"; shift 2 ;;
    --rmdup-bam) RMDUP_BAM="$2"; shift 2 ;;
    --flt-bam) FLT_BAM="$2"; shift 2 ;;
    --dedup-method) DEDUP_METHOD="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --sample-id ID --r1 R1.fq.gz --r2 R2.fq.gz --index-prefix IDX --sort-bam out.sort.bam --rmdup-bam out.rmdup.bam --flt-bam out.flt.bam [--threads 8] [--dedup-method rmdup]"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

for value_name in SAMPLE_ID R1 R2 INDEX_PREFIX SORT_BAM RMDUP_BAM FLT_BAM; do
  if [[ -z "${!value_name}" ]]; then
    echo "ERROR: --${value_name,,} is required" >&2
    exit 2
  fi
done
if [[ ! -s "$R1" || ! -s "$R2" ]]; then
  echo "ERROR: FASTQ not found or empty for $SAMPLE_ID: $R1 $R2" >&2
  exit 2
fi
if [[ ! -s "${INDEX_PREFIX}.bwt" ]]; then
  echo "ERROR: BWA index prefix not found: $INDEX_PREFIX" >&2
  exit 2
fi

command -v bwa >/dev/null 2>&1 || { echo "ERROR: command not found: bwa" >&2; exit 127; }
command -v samtools >/dev/null 2>&1 || { echo "ERROR: command not found: samtools" >&2; exit 127; }
mkdir -p "$(dirname "$SORT_BAM")" "$(dirname "$RMDUP_BAM")" "$(dirname "$FLT_BAM")"

echo "[DAP-Seq] Mapping sample: $SAMPLE_ID"
bwa mem -t "$THREADS" "$INDEX_PREFIX" "$R1" "$R2" \
  | samtools sort -@ "$THREADS" -o "$SORT_BAM" -

case "$DEDUP_METHOD" in
  rmdup)
    # Mirrors the original example. Note: rmdup is deprecated in newer samtools.
    samtools rmdup "$SORT_BAM" "$RMDUP_BAM"
    ;;
  markdup)
    echo "ERROR: dedup_method=markdup is not implemented in this compact reference script." >&2
    echo "       Use rmdup, or replace this script with a fixmate/markdup workflow." >&2
    exit 2
    ;;
  *)
    echo "ERROR: unknown dedup method: $DEDUP_METHOD" >&2
    exit 2
    ;;
esac

# -F 4 removes unmapped reads. Index is useful for downstream inspection.
samtools view -@ "$THREADS" -F 4 -b "$RMDUP_BAM" -o "$FLT_BAM"
samtools index -@ "$THREADS" "$FLT_BAM"

echo "[DAP-Seq] Wrote mapped-only BAM: $FLT_BAM"
