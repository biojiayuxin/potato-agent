#!/usr/bin/env bash
# Template: run RepeatModeler with Dfam TETools via system Apptainer.
# Copy this script into a project work directory and edit the variables below.
# Keep one output directory per genome/haplotype unit.

set -euo pipefail

SIF="${SIF:-dfam-tetools-latest.sif}"
GENOME_FASTA="${GENOME_FASTA:?set GENOME_FASTA to an input FASTA path}"
OUTDIR="${OUTDIR:?set OUTDIR to a writable RepeatModeler output directory}"
THREADS="${THREADS:-32}"
DB_NAME="${DB_NAME:-repeatdatabase}"

TETOOLS_PATH="${TETOOLS_PATH:-/opt/RepeatMasker:/opt/RepeatMasker/util:/opt/RepeatModeler:/opt/RepeatModeler/util:/opt/coseg:/opt/ucsc_tools:/opt:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/opt/rmblast/bin:/bin}"

mkdir -p "$OUTDIR"
GENOME_DIR="$(cd "$(dirname "$GENOME_FASTA")" && pwd)"
GENOME_BASENAME="$(basename "$GENOME_FASTA")"
OUTDIR_ABS="$(cd "$OUTDIR" && pwd)"

if [[ ! -s "$SIF" ]]; then
  echo "ERROR: missing Dfam TETools SIF: $SIF" >&2
  echo "Download example: apptainer pull dfam-tetools-latest.sif docker://dfam/tetools:latest" >&2
  exit 1
fi
if [[ ! -s "$GENOME_FASTA" ]]; then
  echo "ERROR: missing genome FASTA: $GENOME_FASTA" >&2
  exit 1
fi

apptainer exec \
  -B "$OUTDIR_ABS:/run" \
  -B "$GENOME_DIR:/data:ro" \
  "$SIF" \
  bash -lc "
    set -euo pipefail
    export PATH='$TETOOLS_PATH'
    cd /run
    echo '[RepeatModeler] start: ' \\$(date)
    command -v BuildDatabase
    command -v RepeatModeler
    BuildDatabase -name '$DB_NAME' -engine ncbi '/data/$GENOME_BASENAME' > BuildDatabase.out 2> BuildDatabase.err
    RepeatModeler -threads '$THREADS' -database '$DB_NAME' -engine ncbi > run.out 2> run.err
    echo '[RepeatModeler] done: ' \\$(date)
    find . -maxdepth 2 -type f \\( -name 'consensi.fa*' -o -name '*families.fa' -o -name '*families.stk' -o -name '*rmod.log' -o -name 'run.out' -o -name 'run.err' \\) -printf '%p\\t%s bytes\\n' | sort
  "
