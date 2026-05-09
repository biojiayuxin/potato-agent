#!/usr/bin/env bash
# Run HOMER motif enrichment for one DAP-Seq target.

set -euo pipefail

TARGET_ID=""
BED=""
GENOME=""
OUTDIR=""
EXTRA=""
PREPARSED_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-id) TARGET_ID="$2"; shift 2 ;;
    --bed) BED="$2"; shift 2 ;;
    --genome) GENOME="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --extra) EXTRA="$2"; shift 2 ;;
    --preparsed-dir) PREPARSED_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --target-id ERF1 --bed ERF1_peaks.bed --genome genome.fa --outdir results/04-homer/ERF1 [--extra '-mask'] [--preparsed-dir results/04-homer/.homer-preparsed]"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$TARGET_ID" || -z "$BED" || -z "$GENOME" || -z "$OUTDIR" ]]; then
  echo "ERROR: --target-id, --bed, --genome and --outdir are required" >&2
  exit 2
fi
if [[ ! -s "$BED" ]]; then
  echo "ERROR: HOMER input BED not found or empty: $BED" >&2
  exit 2
fi
if [[ ! -s "$GENOME" ]]; then
  echo "ERROR: HOMER genome FASTA not found or empty: $GENOME" >&2
  exit 2
fi

command -v findMotifsGenome.pl >/dev/null 2>&1 || { echo "ERROR: command not found: findMotifsGenome.pl" >&2; exit 127; }
mkdir -p "$OUTDIR"

if [[ -z "$PREPARSED_DIR" ]]; then
  PREPARSED_DIR="$(dirname "$OUTDIR")/.homer-preparsed"
fi
mkdir -p "$PREPARSED_DIR"

# shellcheck disable=SC2206 # EXTRA intentionally supports config argument string.
extra_args=($EXTRA)
extra_args+=("-preparsedDir" "$PREPARSED_DIR")

echo "[DAP-Seq] HOMER motif analysis target: $TARGET_ID"
findMotifsGenome.pl "$BED" "$GENOME" "$OUTDIR" "${extra_args[@]}"
echo "[DAP-Seq] HOMER output directory: $OUTDIR"
