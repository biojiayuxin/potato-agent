---
name: find-orthogroups
description: Prepare protein FASTA inputs and run OrthoFinder to infer orthogroups, including input cleaning, QC, Slurm submission, and result validation.
version: 0.2.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [orthofinder, orthogroups, pangenome, protein, fasta, potato, slurm, qc]
    related_skills: [slurm-for-long-running-tasks]
---

# Find Orthogroups with OrthoFinder

## Use When

Use this skill when the user wants to cluster multiple protein FASTA files into orthogroups with OrthoFinder, usually for pan-genome or multi-genome gene family analysis.

Do not use this for two-genome synteny-based ortholog mapping; use `ortholog-finder` or `genome-synteny` instead.

## Workdir Layout

Use a project workdir and never edit raw FASTA files in place.

```text
<workdir>/
├── 00_input_links/                  # optional symlinks to raw protein FASTA files
├── 01_orthofinder_input_clean/      # cleaned FASTA files passed to OrthoFinder
├── 02_orthofinder_run/              # OrthoFinder output parent
├── logs/
├── qc/
└── scripts/
```

## Input Cleaning and Filtering

Clean inputs into `01_orthofinder_input_clean/` and write QC/provenance tables under `qc/`. The former `orthofinder-input-cleaning` rules are merged here; implement them directly with a small FASTA parser or local one-off script as needed.

Filtering rules:

- Prefix every protein ID with the sample ID and `__`, e.g. `PG1015__chr01_H1G000050.mRNA1`.
- Derive the sample ID from the filename token before the first dot, e.g. `PG1015.repre.pep.fa` becomes `PG1015`.
- Truncate translated protein sequences at the first `.` and discard the dot plus downstream sequence.
- Omit proteins that become zero length after truncation and record them in `qc/clean_empty_after_truncation.tsv`.
- Keep ambiguous but accepted protein symbols `B Z X J U O * -`.
- Treat any other non-dot sequence character as a blocking QC error.
- Warn on DNA-like protein records, but keep them by default.
- Block if sample IDs are duplicated, within-file IDs are duplicated, or cleaned IDs are not globally unique.

Important output tables:

```text
qc/clean_input_manifest.tsv
qc/clean_generation_summary.tsv
qc/clean_id_mapping.tsv
qc/clean_truncation_records.tsv
qc/clean_empty_after_truncation.tsv
qc/clean_qc_issues.tsv
qc/clean_cross_file_duplicate_ids.tsv
```

Only proceed to OrthoFinder when input cleaning has zero blocking errors.

## OrthoFinder Environment

Prefer an existing OrthoFinder on `PATH`. If it is missing, create a dedicated environment. Put the environment `bin` first in `PATH` because some OrthoFinder wrappers use `/usr/bin/env python3`.

```bash
ENV="${ORTHOFINDER_ENV:-$HOME/.micromamba/envs/orthofinder}"

if ! command -v orthofinder >/dev/null 2>&1 && [ ! -x "$ENV/bin/orthofinder" ]; then
  if command -v micromamba >/dev/null 2>&1; then
    micromamba create -y -p "$ENV" -c conda-forge -c bioconda orthofinder
  elif command -v mamba >/dev/null 2>&1; then
    mamba create -y -p "$ENV" -c conda-forge -c bioconda orthofinder
  elif command -v conda >/dev/null 2>&1; then
    conda create -y -p "$ENV" -c conda-forge -c bioconda orthofinder
  else
    echo "ERROR: orthofinder is missing and no micromamba/mamba/conda was found" >&2
    exit 1
  fi
fi

export PATH="$ENV/bin:$PATH"
orthofinder -v
mcl --version || true
diamond version || diamond --version || true
python3 - <<'PY'
import sklearn, Bio
print('sklearn', sklearn.__version__)
print('biopython', Bio.__version__)
PY
```

If OrthoFinder is already installed outside `$ENV`, adjust `ENV` or `PATH` so `command -v orthofinder` and `command -v python3` point to a compatible environment.

## Run Mode

Default full run:

```bash
orthofinder -f "$WORK/01_orthofinder_input_clean" \
  -o "$WORK/02_orthofinder_run/orthofinder_full" \
  -t 32 -a 32
```

Orthogroups-only run:

```bash
orthofinder -f "$WORK/01_orthofinder_input_clean" \
  -o "$WORK/02_orthofinder_run/orthofinder_orthogroups_only" \
  -t 80 -a 80 -og
```

Notes:

- Use `-og` / `--only-groups` when the user wants to stop after orthogroup inference. In OrthoFinder v3.1.5 this option can be supported even when hidden from `orthofinder -h`.
- Avoid changing `-S`, `-I`, `-M`, `-A`, or `-T` unless the user asks or there is a scientific reason.
- A non-default OrthoFinder v3 `-o` path must not already exist. Create the parent directory only. If the exact output path exists and is non-empty, do not overwrite it without explicit user direction.

## Slurm Submission

For long jobs, use the `slurm-for-long-running-tasks` skill wrappers instead of raw `sbatch`. Write a runnable payload script, then submit it.

Example payload at `$WORK/scripts/run_orthofinder.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

WORK=/path/to/workdir
ENV="${ORTHOFINDER_ENV:-$HOME/.micromamba/envs/orthofinder}"
INPUT="$WORK/01_orthofinder_input_clean"
OUT_PARENT="$WORK/02_orthofinder_run"
RUN_NAME=orthofinder_orthogroups_only
OUT="$OUT_PARENT/$RUN_NAME"
LOG="$WORK/logs"
THREADS=80

mkdir -p "$LOG" "$OUT_PARENT" "$WORK/tmp"

if [ -e "$OUT" ]; then
  if [ -d "$OUT" ] && [ -z "$(find "$OUT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    rmdir "$OUT"
  else
    echo "ERROR: OrthoFinder output path already exists and is not empty: $OUT" >&2
    exit 2
  fi
fi

export PATH="$ENV/bin:$PATH"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export TMPDIR="$WORK/tmp"

{
  echo "started: $(date '+%F %T')"
  echo "host: $(hostname)"
  echo "orthofinder: $(command -v orthofinder)"
  echo "python3: $(command -v python3)"
  orthofinder -v
  echo "input_files: $(find "$INPUT" -maxdepth 1 -type f -name '*.fa' | wc -l)"
  echo "input_sequences: $(grep -h '^>' "$INPUT"/*.fa | wc -l)"
  echo "command: orthofinder -f $INPUT -o $OUT -t $THREADS -a $THREADS -og"
} | tee "$LOG/orthofinder.preflight.log"

set +e
orthofinder -f "$INPUT" -o "$OUT" -t "$THREADS" -a "$THREADS" -og \
  2>&1 | tee "$LOG/orthofinder.run.log"
status=${PIPESTATUS[0]}
set -e

latest=$(find "$OUT" -maxdepth 2 -type d -name 'Results_*' -printf '%T@\t%p\n' 2>/dev/null \
  | sort -nr | head -n1 | cut -f2- || true)

{
  echo "finished: $(date '+%F %T')"
  echo "exit_code: $status"
  echo "latest_results: ${latest:-NA}"
  if [ -n "${latest:-}" ]; then
    find "$latest" -maxdepth 4 -type f \( \
      -name 'Orthogroups.tsv' -o \
      -name 'Orthogroups.GeneCount.tsv' -o \
      -name 'Orthogroups_UnassignedGenes.tsv' \
    \) -print
  fi
} | tee "$LOG/orthofinder.postflight.log"

test "$status" -eq 0
test -n "${latest:-}"
test -s "$latest/Orthogroups/Orthogroups.tsv"
test -s "$latest/Orthogroups/Orthogroups.GeneCount.tsv"
```

Submit:

```bash
SLURM_SKILL_DIR=/path/to/slurm-for-long-running-tasks
bash "$SLURM_SKILL_DIR/scripts/submit-job.sh" \
  --job-name pan_of_og \
  --cpus 80 \
  --mem-gb 40 \
  --time 7-00:00:00 \
  --workdir "$WORK" \
  --output "$WORK/logs/slurm-%j.orthofinder.out" \
  --error "$WORK/logs/slurm-%j.orthofinder.err" \
  --script "$WORK/scripts/run_orthofinder.sh"
```

Use user-provided resources when available. Otherwise choose conservative resources based on input scale and confirm before submitting.

## Progress Checks

Use the Slurm wrapper first:

```bash
bash "$SLURM_SKILL_DIR/scripts/list-jobs.sh"
bash "$SLURM_SKILL_DIR/scripts/job-status.sh" <JOBID>
```

Then inspect logs and intermediates:

```bash
tail -n 80 "$WORK/logs/orthofinder.run.log"
ps -u "$USER" -o pid,ppid,stat,etime,pcpu,pmem,rss,cmd --sort=-pcpu \
  | grep -E 'orthofinder|diamond|mcl' | head
find "$WORK/02_orthofinder_run" -maxdepth 4 -type f \
  \( -name 'Orthogroups.tsv' -o -name 'Orthogroups.GeneCount.tsv' -o -name 'Orthogroups_UnassignedGenes.tsv' \) -print
```

On hosts without Slurm accounting, do not rely on `sacct` after the job leaves the queue; use stdout, stderr, and payload logs.

## Result Validation

Expected orthogroup outputs:

```text
Results_*/Orthogroups/Orthogroups.tsv
Results_*/Orthogroups/Orthogroups.GeneCount.tsv
Results_*/Orthogroups/Orthogroups_UnassignedGenes.tsv
```

After completion:

```bash
RESULTS=$(find "$WORK/02_orthofinder_run" -type d -name 'Results_*' -printf '%T@\t%p\n' \
  | sort -nr | head -n1 | cut -f2-)
test -s "$RESULTS/Orthogroups/Orthogroups.tsv"
test -s "$RESULTS/Orthogroups/Orthogroups.GeneCount.tsv"
wc -l "$RESULTS/Orthogroups/Orthogroups.tsv" \
      "$RESULTS/Orthogroups/Orthogroups.GeneCount.tsv" \
      "$RESULTS/Orthogroups/Orthogroups_UnassignedGenes.tsv"
```

Report:

- OrthoFinder version and exact command.
- Input FASTA count and cleaned protein count.
- Whether input cleaning finished with zero blocking errors.
- Slurm job ID and resources, if submitted.
- Result directory.
- Orthogroup count, computed as `Orthogroups.tsv` rows minus header.
- Unassigned gene count, if available.
- Warnings from `qc/clean_qc_issues.tsv`, especially DNA-like records and proteins omitted after `.` truncation.

## Checklist

Before launch:

- Raw FASTA files are untouched.
- Clean FASTA files exist under `01_orthofinder_input_clean/`.
- Cleaning script exited 0.
- OrthoFinder environment and Python are consistent.
- Exact `-o` output path does not already exist.
- Slurm resources are user-specified or confirmed.

After launch:

- Job state/logs show the command actually ran.
- `Orthogroups.tsv` and `Orthogroups.GeneCount.tsv` are non-empty.
- Mapping tables are preserved for clean ID to raw ID lookup.
