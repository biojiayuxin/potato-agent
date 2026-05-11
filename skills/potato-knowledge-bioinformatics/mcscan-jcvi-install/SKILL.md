---
name: mcscan-jcvi-install
description: Install and verify the Python version of MCScan via the jcvi package in an isolated micromamba/conda environment, including common external aligners and the ete4 dependency needed by current jcvi graphics modules.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [mcscan, jcvi, synteny, comparative-genomics, installation]
---

# MCScan Python / jcvi installation

Use this skill when the user asks to install the Python version of MCScan from the jcvi project or needs a working environment for `jcvi.compara.catalog`, `jcvi.compara.synteny`, or `jcvi.graphics.karyotype`.

## Recommended environment

Prefer an isolated environment instead of adding `jcvi` to a general RNA-seq or genome-analysis environment.

Default environment name:

```bash
mcscan_jcvi
```

Install with micromamba/conda from `conda-forge` and `bioconda`:

```bash
/opt/micromamba/bin/micromamba create -y -n mcscan_jcvi \
  -c conda-forge -c bioconda \
  python=3.11 \
  jcvi \
  ete4 \
  last \
  diamond \
  blast \
  minimap2 \
  samtools \
  bedtools \
  seqkit \
  gffread
```

If the environment was already created without `ete4`, add it:

```bash
/opt/micromamba/bin/micromamba install -y -n mcscan_jcvi -c conda-forge -c bioconda ete4
```

## Why include these packages

- `jcvi`: provides the Python MCScan/synteny modules.
- `last`: default aligner used by `jcvi.compara.catalog ortholog`.
- `diamond` and `blast`: useful alternatives for protein comparisons.
- `gffread`, `seqkit`, `bedtools`, `samtools`: common preprocessing/checking tools for genome, GFF/GTF, BED and FASTA inputs.
- `ete4`: required by current `jcvi.graphics.karyotype` import path; without it, karyotype help/import can fail with `ModuleNotFoundError: No module named 'ete4'`.

## Verification

Use direct environment paths for verification to avoid `micromamba run` overhead or timeouts on large environments:

```bash
ENV=/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi
export PATH="$ENV/bin:$PATH"

python - <<'PY'
import jcvi, sys
from jcvi.compara import catalog, synteny
from jcvi.graphics import karyotype
print('python', sys.version.split()[0])
print('jcvi', getattr(jcvi, '__version__', 'unknown'))
print('catalog/synteny/karyotype imports OK')
PY

jcvi --version
python -m jcvi.compara.catalog ortholog -h | sed -n '1,30p'
python -m jcvi.compara.synteny mcscan -h | sed -n '1,35p'
python -m jcvi.graphics.karyotype -h | sed -n '1,35p'
```

Check external tools:

```bash
for x in python jcvi lastdb lastal diamond blastp minimap2 gffread seqkit bedtools samtools; do
  printf '%-10s %s\n' "$x" "$(command -v "$x")"
done

lastal --version | sed -n '1p'
diamond --version | sed -n '1p'
blastp -version | sed -n '1,2p'
minimap2 --version | sed -n '1p'
gffread --version 2>&1 | sed -n '1p'
seqkit version | sed -n '1p'
bedtools --version
samtools --version | sed -n '1p'
```

## Common entry points

```bash
python -m jcvi.compara.catalog ortholog species_a species_b
python -m jcvi.compara.synteny mcscan bedfile anchorfile
python -m jcvi.graphics.karyotype seqids layout
```

## Pitfalls

1. `python -m jcvi.compara.catalog --help` is not accepted as a normal help flag and exits with an error. Use no action to list actions, or action-specific `-h`, for example:

```bash
python -m jcvi.compara.catalog
python -m jcvi.compara.catalog ortholog -h
```

2. On this Hermes environment, repeated `micromamba run -n mcscan_jcvi ...` calls may time out during verification. Prefer setting `ENV=/path/to/env` and prepending `$ENV/bin` to `PATH` for multiple checks.

3. If `jcvi.graphics.karyotype` fails with missing `ete4`, install `ete4` from conda-forge in the same environment.
