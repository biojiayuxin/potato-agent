---
name: genome-softmask
description: Use when soft-masking a genome assembly with de novo repeat discovery and RepeatMasker. Provides a reusable Apptainer + Dfam TETools workflow for running RepeatModeler first, then RepeatMasker softmasking per genome or haplotype.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [genome, softmask, repeatmodeler, repeatmasker, apptainer, repeats]
    related_skills: [slurm-for-long-running-tasks]
---

# Genome Softmask

## Overview

Use this skill to perform genome repeat softmasking before gene structure annotation. The workflow is:

```text
Genome FASTA
  -> RepeatModeler de novo repeat library
  -> RepeatMasker softmasking with the de novo library
  -> softmasked FASTA + repeat annotation tables/GFF
```

The default runtime uses the system `apptainer` command and a Dfam TETools SIF image. If the account or environment does not already provide `dfam-tetools.sif`, download or build it before running the workflow.

## When to Use

Use this skill when the user asks to:

- softmask a genome before gene prediction or structural annotation;
- run RepeatModeler and RepeatMasker from a Dfam TETools container;
- process haplotype-resolved assemblies independently;
- generate `.masked`, `.out`, `.gff`, and `.tbl` RepeatMasker outputs.

Do not use this skill for:

- protein-level repeat annotation only;
- EDTA-specific workflows unless the user explicitly wants EDTA;
- repeat landscape dating or publication-style TE curation unless requested.

## Required Inputs

For each genome or haplotype unit, prepare:

| Item | Requirement |
|---|---|
| Genome FASTA | Existing, non-empty FASTA file. Prefer cleaned headers and no zero-length records. |
| Unit name | Short safe identifier such as `unitA`, `unitB`, or `sampleA`. |
| Output directory | Separate writable directory per unit. |
| Dfam TETools SIF | Local SIF image containing RepeatModeler, BuildDatabase, and RepeatMasker. |
| Apptainer | Use the system default `apptainer`; do not install a separate copy unless required. |

## Container Setup

First check whether the SIF exists in the current account or project environment. If it is missing, download it with Apptainer, for example:

```bash
apptainer pull dfam-tetools-latest.sif docker://dfam/tetools:latest
```

If Docker Hub is not reachable, transfer a verified SIF from another server or use an approved local mirror. Keep the SIF path configurable in scripts; do not hard-code a user-specific path in reusable code.

Some Dfam TETools SIF builds place tools under `/opt` but do not expose them in `PATH` during non-interactive `apptainer exec`. Set `PATH` explicitly before running tools:

```bash
export PATH="/opt/RepeatMasker:/opt/RepeatMasker/util:/opt/RepeatModeler:/opt/RepeatModeler/util:/opt/coseg:/opt/ucsc_tools:/opt:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/opt/rmblast/bin:/bin"
```

Verify inside the container:

```bash
apptainer exec "$SIF" bash -lc 'export PATH="..."; command -v RepeatModeler BuildDatabase RepeatMasker; RepeatModeler -version; RepeatMasker -version 2>&1 | head'
```

## Recommended Directory Layout

Keep each unit separate:

```text
workdir/
  01_repeatmodeler/
    unitA/
    unitB/
  02_softmask/
    unitA/
    unitB/
```

This avoids database and RepeatMasker temporary-file collisions.

## Step 1: RepeatModeler

Run RepeatModeler independently per genome/haplotype.

Reference script:

```text
references/run_repeatmodeler_template.sh
```

Core logic:

```bash
BuildDatabase -name repeatdatabase -engine ncbi /data/genome.fa
RepeatModeler -threads "$THREADS" -database repeatdatabase -engine ncbi
```

Expected useful outputs include:

```text
repeatdatabase-families.fa
repeatdatabase-families.stk
repeatdatabase-rmod.log
RM_*/consensi.fa.classified
```

`repeatdatabase-families.fa` is normally the same classified consensus library copied from the RepeatModeler working directory at successful completion. If exact reproducibility matters, verify with `cmp` or `sha256sum` before choosing a library file.

## Step 2: RepeatMasker Softmask

Run RepeatMasker with the de novo library from the matching unit.

Reference script:

```text
references/run_repeatmasker_softmask_template.sh
```

Core logic:

```bash
RepeatMasker \
  -xsmall \
  -gff \
  -pa "$THREADS" \
  -dir ./masked_soft/ \
  -lib /repeatlib/repeatdatabase-families.fa \
  /data/genome.fa
```

Notes:

- `-xsmall` produces softmasking by converting masked bases to lowercase.
- `-gff` emits GFF output in addition to `.out`, `.tbl`, and `.masked`; it does not change the softmasked FASTA.
- Use a non-system bind mount such as `/repeatlib` for the library directory. Do **not** bind a host directory to `/lib`, because it can shadow container system libraries and break `/bin/bash`.

Expected outputs:

```text
masked_soft/<genome>.masked
masked_soft/<genome>.out
masked_soft/<genome>.out.gff
masked_soft/<genome>.tbl
masked_soft/<genome>.cat.gz
```

Create stable symlinks if useful:

```text
<unit>.softmasked.fa -> masked_soft/<genome>.masked
<unit>.repeatmasker.out -> masked_soft/<genome>.out
<unit>.repeatmasker.gff -> masked_soft/<genome>.out.gff
<unit>.repeatmasker.tbl -> masked_soft/<genome>.tbl
```

## Slurm Guidance

For large plant genomes, use Slurm or another batch scheduler rather than running in the foreground. If Slurm is available, load and follow the `slurm-for-long-running-tasks` skill.

Resource settings must come from the user or from project conventions. If resources are not specified, print a proposed submit command and ask for confirmation before submitting.

Common patterns:

- Run each haplotype sequentially in one job when the user wants controlled resource usage.
- Run haplotypes as separate jobs only when the user explicitly wants parallelism and enough resources are free.
- Keep RepeatModeler and RepeatMasker logs per unit.

## Repeat Summary Extraction

RepeatMasker writes the key summary in the `.tbl` file. Important lines include:

```text
total length:
bases masked:
Total interspersed repeats:
Retroelements:
LTR elements:
DNA transposons:
Unclassified:
Simple repeats:
Low complexity:
```

The total softmasked fraction is the `bases masked` percentage. It should match the lowercase-base fraction in the `.masked` FASTA.

Optional validation:

```bash
python3 - <<'PY'
from pathlib import Path
masked = Path('genome.fa.masked')
total = lower = 0
for line in masked.open():
    if line.startswith('>'):
        continue
    s = line.strip()
    total += len(s)
    lower += sum(1 for c in s if c.islower())
print(f'lowercase_masked_bp={lower}')
print(f'lowercase_pct={lower / total * 100:.2f}')
PY
```

## Common Pitfalls

1. **Container PATH missing tools** — Dfam TETools may contain RepeatModeler and RepeatMasker under `/opt`, but non-interactive exec may not load the container environment. Set `PATH` explicitly.
2. **Binding to `/lib` breaks the container** — use `/repeatlib` or another non-system mount point for the repeat library.
3. **Mixing haplotype outputs** — do not run multiple units in the same working directory.
4. **Using the wrong library** — use the repeat library generated from the same unit unless the user explicitly wants a shared combined library.
5. **Assuming historical Slurm status is available** — on clusters without Slurm accounting, inspect stdout/stderr and output files after jobs leave the active queue.
6. **Hard-coding user paths** — scripts should accept paths through arguments or environment variables.

## Verification Checklist

- [ ] `apptainer` resolves to the system command.
- [ ] Dfam TETools SIF exists or has been downloaded/transferred.
- [ ] `RepeatModeler`, `BuildDatabase`, and `RepeatMasker` are visible inside the container.
- [ ] Each unit has its own RepeatModeler and RepeatMasker output directory.
- [ ] RepeatModeler produced a non-empty classified consensus library.
- [ ] RepeatMasker produced `.masked`, `.out`, `.out.gff`, and `.tbl` files.
- [ ] `RepeatMasker.stderr` is empty or contains only understood non-fatal messages.
- [ ] The `.tbl` `bases masked` percentage agrees with lowercase-base validation when checked.
