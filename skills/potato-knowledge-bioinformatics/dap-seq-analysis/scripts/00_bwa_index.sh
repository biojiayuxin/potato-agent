#!/usr/bin/env bash
# Build BWA index with an explicit output prefix.
# This script is designed to be called by Snakefile rule bwa_index.

set -euo pipefail

GENOME=""
PREFIX=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --genome) GENOME="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --genome genome.fa --prefix results/reference/bwa/genome"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$GENOME" || -z "$PREFIX" ]]; then
  echo "ERROR: --genome and --prefix are required" >&2
  exit 2
fi
if [[ ! -s "$GENOME" ]]; then
  echo "ERROR: genome FASTA not found or empty: $GENOME" >&2
  exit 2
fi

command -v bwa >/dev/null 2>&1 || { echo "ERROR: command not found: bwa" >&2; exit 127; }
mkdir -p "$(dirname "$PREFIX")"

# If all expected BWA index files already exist, do nothing.
if [[ -s "${PREFIX}.amb" && -s "${PREFIX}.ann" && -s "${PREFIX}.bwt" && -s "${PREFIX}.pac" && -s "${PREFIX}.sa" ]]; then
  echo "[DAP-Seq] BWA index already exists: $PREFIX"
  exit 0
fi

echo "[DAP-Seq] Building BWA index"
echo "  genome: $GENOME"
echo "  prefix: $PREFIX"
bwa index -p "$PREFIX" "$GENOME"
