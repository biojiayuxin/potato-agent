---
name: busco-assessment
version: 1.0.0
description: Use when running BUSCO completeness assessment for genome, transcriptome, or protein FASTA files, including offline lineage selection from public_data/BUSCO_DB and Slurm-backed execution. Do not use this skill for general genome statistics such as N50 or BUSCO-unrelated annotation checks.
metadata:
  hermes:
    tags: [busco, genome-qc, annotation-qc, protein-qc, conda, micromamba, bioconda, slurm]
    related_skills: [slurm-for-long-running-tasks]
---

# BUSCO Assessment

## Overview

Use this skill to evaluate assembly or annotation completeness with BUSCO. BUSCO can assess different FASTA input types:

- genome assembly FASTA: `-m genome`
- transcript FASTA: `-m transcriptome`
- protein FASTA: `-m proteins`

For large genomes or multiple samples, prefer Slurm/background execution rather than a long foreground command.

## When to Use

Use this skill when the user asks to:

- run BUSCO for genome assemblies;
- run BUSCO for predicted protein FASTA files;

## Environment Setup

Prefer an existing package manager in this order:

1. `micromamba`
2. `mamba`
3. `conda`

Examples below use `micromamba`. If the current system only provides `mamba` or `conda`, substitute the equivalent `create` and `run` commands instead of assuming `micromamba` exists.

Install BUSCO version **6.1.0 or newer**. Do not install unpinned `busco` blindly if the solver may select an old or unusable build. Query available versions if needed, then request a version constraint such as:

```bash
micromamba create -y -p "$ENV_PREFIX" -c conda-forge -c bioconda 'busco>=6.1.0'
```

If BUSCO is already installed, verify it before running analyses.

## Post-Install Verification

Run real command checks before claiming the environment is ready. Example:

```bash
micromamba run -p "$ENV_PREFIX" busco --version
TMPDIR="${TMPDIR:-/tmp}"
micromamba run -p "$ENV_PREFIX" busco --help >"$TMPDIR/busco_help_check.txt"
micromamba run -p "$ENV_PREFIX" python - <<'PY'
import shutil, subprocess
cmds = ['busco','hmmsearch','augustus','blastp','tblastn','makeblastdb','metaeuk','miniprot','prodigal']
missing = []
for cmd in cmds:
    path = shutil.which(cmd)
    print(f'{cmd}: {path or "NOT_FOUND"}')
    if not path:
        missing.append(cmd)
print('python:', subprocess.check_output(['python','--version'], text=True).strip())
if missing:
    raise SystemExit('Missing commands: ' + ', '.join(missing))
PY
```

If package listing is needed and pip metadata scanning causes permission or path errors, list conda packages without pip metadata scanning where supported, rather than reinstalling a working environment.

## BUSCO Database Handling

Use the project's local BUSCO database root first:

```bash
BUSCO_DB_ROOT="${BUSCO_DB_ROOT:-public_data/BUSCO_DB}"
```

Inspect only the top-level lineage directories before choosing a database:

```bash
for lineage_dir in "$BUSCO_DB_ROOT"/*_odb*; do
  [ -d "$lineage_dir" ] && basename "$lineage_dir"
done | sort
```

Choose the most specific lineage appropriate for the organism and input. Examples:

- Solanaceae/Solanales plants: prefer `solanales_odb*` if present.

Run BUSCO in offline mode when using a local lineage dataset:

```bash
--offline --lineage_dataset "$BUSCO_DB_ROOT/<lineage>"
```

If no suitable lineage exists under the selected database root, ask the user whether they want to download the needed BUSCO database. Explain that BUSCO database downloads may take a long time or fail/retry slowly under network restrictions, so manual download or reuse of an existing local lineage may be preferable.

## Input and Output Conventions

Before running BUSCO, verify:

- each input FASTA exists and is non-empty;
- the chosen BUSCO mode matches the input type;
- output names are unique and will not overwrite prior results;
- the output directory is writable;

Recommended output layout:

```text
busco/
├── logs/
├── scripts/
├── <sample>.genome.busco/
└── <sample>.protein.busco/
```

Use clear sample names that encode the mode or input type, for example:

- `<sample>.genome.busco`
- `<sample>.protein.busco`

## Simple Genome BUSCO Example

Set variables from the current task instead of hard-coding paths:

```bash
ENV_PREFIX="<busco-env-prefix>"
INPUT_FASTA="<assembly.fa>"
OUTDIR="<busco-output-dir>"
BUSCO_DB_ROOT="${BUSCO_DB_ROOT:-public_data/BUSCO_DB}"
LINEAGE="$BUSCO_DB_ROOT/<lineage>"
THREADS=16
SAMPLE="sample.genome.busco"

mkdir -p "$OUTDIR"
micromamba run -p "$ENV_PREFIX" busco \
  -i "$INPUT_FASTA" \
  -o "$SAMPLE" \
  -m genome \
  --offline \
  --lineage_dataset "$LINEAGE" \
  -c "$THREADS" \
  --out_path "$OUTDIR"
```

## Simple Protein BUSCO Example

For predicted proteins, use `-m proteins`:

```bash
ENV_PREFIX="<busco-env-prefix>"
INPUT_FASTA="<proteins.fa>"
OUTDIR="<busco-output-dir>"
BUSCO_DB_ROOT="${BUSCO_DB_ROOT:-public_data/BUSCO_DB}"
LINEAGE="$BUSCO_DB_ROOT/<lineage>"
THREADS=16
SAMPLE="sample.protein.busco"

mkdir -p "$OUTDIR"
micromamba run -p "$ENV_PREFIX" busco \
  -i "$INPUT_FASTA" \
  -o "$SAMPLE" \
  -m proteins \
  --offline \
  --lineage_dataset "$LINEAGE" \
  -c "$THREADS" \
  --out_path "$OUTDIR"
```

## Slurm Execution Pattern

Before submitting, create `$RUN_SCRIPT` and make sure it contains the resolved input checks, environment invocation, BUSCO command, and output path for the current task.

Always test the Slurm submission with `--print-only` first. Review the resolved command, paths, CPU, memory, time limit, and log paths. Submit only after confirming the generated command is correct.

Example print-only check:

```bash
SLURM_SKILL_DIR="<resolved-slurm-skill-dir>"
OUTDIR="<busco-output-dir>"
RUN_SCRIPT="$OUTDIR/scripts/run_busco.sh"

bash "$SLURM_SKILL_DIR/scripts/submit-job.sh" \
  --print-only \
  --job-name busco_assessment \
  --cpus 16 \
  --mem-gb 20 \
  --time 24:00:00 \
  --workdir "$OUTDIR" \
  --output "$OUTDIR/logs/slurm-%j.out" \
  --error "$OUTDIR/logs/slurm-%j.err" \
  --script "$RUN_SCRIPT"
```

If the print-only command is correct, rerun the same command without `--print-only` to submit.

## Verification Checklist

- [ ] BUSCO version is 6.1.0 or newer.
- [ ] Input FASTA files exist and are non-empty.
- [ ] `BUSCO_DB_ROOT` was resolved from the user-provided path or the default relative `public_data/BUSCO_DB`, then inspected for suitable lineage directories.
- [ ] A lineage matching the organism/taxon was selected, or the user was asked about downloading one.
- [ ] BUSCO mode matches the input type.
- [ ] Output directory and sample names will not overwrite existing results.
- [ ] Slurm `--print-only` was checked before real submission for long-running jobs.
