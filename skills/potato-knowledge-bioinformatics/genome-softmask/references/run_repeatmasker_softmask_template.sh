#!/usr/bin/env bash
# Template: run RepeatMasker softmasking with a RepeatModeler library.
# Copy this script into a project work directory and edit the variables below.
# Keep one output directory per genome/haplotype unit.

set -euo pipefail

SIF="${SIF:-dfam-tetools-latest.sif}"
GENOME_FASTA="${GENOME_FASTA:?set GENOME_FASTA to an input FASTA path}"
REPEAT_LIBRARY="${REPEAT_LIBRARY:?set REPEAT_LIBRARY to consensi.fa.classified or repeatdatabase-families.fa}"
OUTDIR="${OUTDIR:?set OUTDIR to a writable RepeatMasker output directory}"
THREADS="${THREADS:-32}"
UNIT_NAME="${UNIT_NAME:-$(basename "$GENOME_FASTA" | sed 's/\.fa\(sta\)\?$//')}"

TETOOLS_PATH="${TETOOLS_PATH:-/opt/RepeatMasker:/opt/RepeatMasker/util:/opt/RepeatModeler:/opt/RepeatModeler/util:/opt/coseg:/opt/ucsc_tools:/opt:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/opt/rmblast/bin:/bin}"

mkdir -p "$OUTDIR"
GENOME_DIR="$(cd "$(dirname "$GENOME_FASTA")" && pwd)"
GENOME_BASENAME="$(basename "$GENOME_FASTA")"
LIB_DIR="$(cd "$(dirname "$REPEAT_LIBRARY")" && pwd)"
LIB_BASENAME="$(basename "$REPEAT_LIBRARY")"
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
if [[ ! -s "$REPEAT_LIBRARY" ]]; then
  echo "ERROR: missing repeat library: $REPEAT_LIBRARY" >&2
  exit 1
fi

apptainer exec \
  -B "$OUTDIR_ABS:/run" \
  -B "$GENOME_DIR:/data:ro" \
  -B "$LIB_DIR:/repeatlib:ro" \
  "$SIF" \
  bash -lc "
    set -euo pipefail
    export PATH='$TETOOLS_PATH'
    cd /run
    mkdir -p masked_soft
    echo '[RepeatMasker] start: ' \\$(date)
    command -v RepeatMasker
    grep -c '^>' '/repeatlib/$LIB_BASENAME' > repeat_library.sequence_count.txt
    RepeatMasker \
      -xsmall \
      -gff \
      -pa '$THREADS' \
      -dir ./masked_soft/ \
      -lib '/repeatlib/$LIB_BASENAME' \
      '/data/$GENOME_BASENAME' \
      > RepeatMasker.stdout \
      2> RepeatMasker.stderr
    ln -sfn 'masked_soft/$GENOME_BASENAME.masked' '$UNIT_NAME.softmasked.fa'
    ln -sfn 'masked_soft/$GENOME_BASENAME.out' '$UNIT_NAME.repeatmasker.out'
    ln -sfn 'masked_soft/$GENOME_BASENAME.out.gff' '$UNIT_NAME.repeatmasker.gff'
    ln -sfn 'masked_soft/$GENOME_BASENAME.tbl' '$UNIT_NAME.repeatmasker.tbl'
    echo '[RepeatMasker] done: ' \\$(date)
    find . -maxdepth 2 -type f \\( -name '*.masked' -o -name '*.out' -o -name '*.out.gff' -o -name '*.tbl' -o -name '*.cat.gz' -o -name 'RepeatMasker.stdout' -o -name 'RepeatMasker.stderr' \\) -printf '%p\\t%s bytes\\n' | sort
  "
