---
name: syri-plotsr-workflow
description: Whole-genome collinearity and structural rearrangement workflow using nucmer, delta-filter, show-coords, Syri, and plotsr. Includes a recommended dedicated conda environment, pinned dependency versions, runnable shell templates, and guidance to generate a complete end-to-end workflow in one pass.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [genome-alignment, syri, plotsr, mummer4, comparative-genomics, workflow]
---

# SyRI + plotsr Whole-Genome Workflow

**Important:** Before running this workflow, first confirm the planned parameter settings with the user, especially the thread count (`THREADS`, `nucmer -t`, and `syri --nc`). Only execute the workflow after the user has explicitly approved the parameters.

Use this skill when the user wants to run a full genome-vs-genome comparison workflow based on:

1. `nucmer`
2. `delta-filter`
3. `show-coords`
4. `syri`
5. `plotsr`

This skill is for **whole-genome alignment / collinearity / structural rearrangement analysis** between two assemblies.

---

## Core rules

### 0. Confirm parameters before running
Before executing the workflow, first confirm the planned parameter settings with the user.

At minimum, confirm:
- reference genome path
- query genome path
- output prefix
- thread count (`THREADS`, `nucmer -t`, `syri --nc`)
- whether `--nosnp` is desired

Only run the workflow after the user has explicitly approved these settings.

### 1. Prefer a dedicated environment
Recommend using a **dedicated `syri_env` conda environment** for this workflow instead of mixing these tools into a general-purpose environment.

Reason:
- `syri` is sensitive to Python / pandas / numpy combinations
- newer `pandas 3.x` and `numpy 2.x` can break Syri with errors like:
  - `ValueError: buffer source array is read-only`
- keeping `syri_env` isolated makes the workflow reproducible and easier to repair

### 2. Always provide a complete pipeline
When the user asks for this workflow, prefer to generate **one complete end-to-end runnable workflow**, not isolated single commands unless the user explicitly asks for only one step.

That means you should usually provide:
- environment creation command(s)
- input file variable definitions
- all five analysis steps
- output file names
- recommended shell script structure

### 3. Keep file naming consistent
Use a single prefix across all steps, e.g.:
- `PREFIX="DM8.1_vs_DM8.2"`

Derived files should remain consistent, for example:
- `${PREFIX}.delta`
- `${PREFIX}.1to1.i90.l1000.delta`
- `${PREFIX}.1to1.i90.l1000.coords`
- `syri` output with `--prefix ${PREFIX}.`

---

## Recommended software environment

### Recommended dedicated environment name
```bash
syri_env
```

### Recommended install command
Use a dedicated environment and pin the versions that were verified to work together.

```bash
conda create -n syri_env -c conda-forge -c bioconda \
  python=3.11 \
  syri=1.7.1 \
  plotsr=1.1.1 \
  mummer4=4.0.1 \
  numpy=1.26.4 \
  pandas=2.2.3 \
  pysam=0.23.3 \
  scipy=1.17.1 \
  python-igraph=1.0.0 \
  -y
```

### Verified dependency versions
These versions were verified in practice for this workflow:

- `python 3.11.15`
- `syri 1.7.1`
- `plotsr 1.1.1`
- `mummer4 4.0.1`
- `numpy 1.26.4`
- `pandas 2.2.3`
- `pysam 0.23.3`
- `scipy 1.17.1`
- `igraph 1.0.0`
- `python-igraph 1.0.0`

### Important compatibility note
Avoid this combination for Syri:
- `pandas 3.x`
- `numpy 2.x`

It can cause Syri to fail with:
```text
ValueError: buffer source array is read-only
```

---

## Expected inputs

At minimum:
- reference genome FASTA
- query genome FASTA

Typical example:
- `REF=/path/to/DM8.1.no_contig.fa`
- `QRY=/path/to/DM8.2.fa`

Optional but often useful later:
- chromosome length table(s)
- chromosome ordering file(s)
- track/annotation files for `plotsr`

---

## Recommended full workflow script

Below is the recommended **one-shot complete workflow** template.

```bash
#!/usr/bin/env bash
set -euo pipefail

source /home/admin/miniconda3/etc/profile.d/conda.sh
conda activate syri_env

WORKDIR="/path/to/project_dir"
REF="$WORKDIR/ref.fa"
QRY="$WORKDIR/qry.fa"
PREFIX="ref_vs_qry"
THREADS=10

cd "$WORKDIR"

# 1) nucmer
nucmer -l 100 -c 500 -t "$THREADS" --prefix "$PREFIX" "$REF" "$QRY"

# 2) delta-filter
FILTER_DELTA="$WORKDIR/${PREFIX}.1to1.i90.l1000.delta"
delta-filter -1 -i 90 -l 1000 "$WORKDIR/${PREFIX}.delta" > "$FILTER_DELTA"

# 3) show-coords
COORDS="$WORKDIR/${PREFIX}.1to1.i90.l1000.coords"
show-coords -THrd "$FILTER_DELTA" > "$COORDS"

# 4) syri
syri \
  -c "$COORDS" \
  -d "$FILTER_DELTA" \
  -r "$REF" \
  -q "$QRY" \
  --prefix "${PREFIX}." \
  --nc "$THREADS" \
  --nosnp

# 5) plotsr
cat > genomes.txt <<EOF
${REF}	Reference	lc:blue
${QRY}	Query	lc:orange
EOF

plotsr --sr ${PREFIX}.syri.out --genomes genomes.txt -o ${PREFIX}.plotsr.pdf

echo "Workflow completed: $WORKDIR"
```

---

## Step-by-step reference commands

The defaults below assume the user has already approved the parameter set. In this skill, the default thread count for both `nucmer` and `syri` is `10` unless the user requests otherwise.

## Step 1: nucmer
Purpose:
- align query assembly to reference assembly
- generate the raw `.delta` file

Reference command:
```bash
nucmer -l 100 -c 500 -t 10 --prefix ref_vs_qry ref.fa qry.fa
```

Typical output:
- `ref_vs_qry.delta`

Notes:
- `-t` is thread count
- `-l 100 -c 500` is a common starting point for assembly comparison
- keep prefix simple and stable

---

## Step 2: delta-filter
Purpose:
- keep 1-to-1 alignments
- remove weaker or redundant hits

Reference command:
```bash
delta-filter -1 -i 90 -l 1000 ref_vs_qry.delta > ref_vs_qry.1to1.i90.l1000.delta
```

Parameter meaning:
- `-1`: one-to-one alignment filtering
- `-i 90`: minimum identity 90%
- `-l 1000`: minimum alignment length 1000 bp

Typical output:
- `ref_vs_qry.1to1.i90.l1000.delta`

---

## Step 3: show-coords
Purpose:
- convert filtered delta into tabular coordinates for Syri

Reference command:
```bash
show-coords -THrd ref_vs_qry.1to1.i90.l1000.delta > ref_vs_qry.1to1.i90.l1000.coords
```

Option meaning:
- `-T`: tab-delimited output
- `-H`: suppress header
- `-r`: sort by reference coordinates
- `-d`: show alignment direction

Typical output:
- `ref_vs_qry.1to1.i90.l1000.coords`

---

## Step 4: Syri
Purpose:
- identify syntenic regions and structural rearrangements

Reference command:
```bash
syri \
  -c ref_vs_qry.1to1.i90.l1000.coords \
  -d ref_vs_qry.1to1.i90.l1000.delta \
  -r ref.fa \
  -q qry.fa \
  --prefix ref_vs_qry. \
  --nc 10 \
  --nosnp
```

Parameter meaning:
- `-c`: coords file from `show-coords`
- `-d`: filtered delta file
- `-r`: reference fasta
- `-q`: query fasta
- `--prefix`: Syri output prefix
- `--nc`: number of CPU cores
- `--nosnp`: skip SNP/small indel calling when the focus is structural rearrangements

Typical key outputs:
- `ref_vs_qry.syri.out`
- `ref_vs_qry.synOut.txt`
- `ref_vs_qry.invOut.txt`
- `ref_vs_qry.TLOut.txt`
- `ref_vs_qry.ctxOut.txt`
- `ref_vs_qry.syri.log`

Important note:
- if Syri crashes with `buffer source array is read-only`, check that the environment is using:
  - `numpy 1.26.4`
  - `pandas 2.2.3`
  and not `numpy 2.x` / `pandas 3.x`

---

## Step 5: plotsr
Purpose:
- visualize syntenic blocks and structural rearrangements from Syri output

Minimal reference command:
```bash
plotsr --sr ref_vs_qry.syri.out --genomes genomes.txt -o ref_vs_qry.plotsr.pdf
```

A minimal `genomes.txt` example:
```text
/path/to/ref.fa	Reference	lc:blue
/path/to/qry.fa	Query	lc:orange
```

Notes:
- `--sr` uses Syri structural rearrangement output
- `--genomes` must contain **genome FASTA paths in column 1**, genome IDs in column 2, and optional semicolon-separated `key:value` tags in column 3
- for colors, use tags such as `lc:blue` or `lc:#1f77b4`; plain values like `blue` without a key will fail in `plotsr 1.1.1`
- output can be PDF/SVG depending on `-o`

If chromosome ordering or subset display is needed, add options such as:
- `--chrord`
- `--chrname`
- `--chr`
- `--reg`

Always inspect `plotsr --help` for dataset-specific customization.

---

## Recommended script split

If the user wants modular execution, generate these scripts:

1. `run_nucmer.sh`
2. `run_delta_filter.sh`
3. `run_show_coords.sh`
4. `run_syri.sh`
5. `run_plotsr.sh`
6. optional master script:
   - `run_syri_pipeline.sh`

But when the user asks for a workflow, **prefer generating the master script too**, so the full pipeline exists in one place.

---

## Example using the observed DM8.1 / DM8.2 naming

```bash
#!/usr/bin/env bash
set -euo pipefail

source /home/admin/miniconda3/etc/profile.d/conda.sh
conda activate syri_env

WORKDIR="/home/data/admin/potato_agent/Work/01-DM8.1_8.2_align"
REF="$WORKDIR/DM8.1.no_contig.fa"
QRY="$WORKDIR/DM8.2.fa"
PREFIX="DM8.1_vs_DM8.2"
THREADS=10

cd "$WORKDIR"

nucmer -l 100 -c 500 -t "$THREADS" --prefix "$PREFIX" "$REF" "$QRY"

delta-filter -1 -i 90 -l 1000 "$WORKDIR/${PREFIX}.delta" > "$WORKDIR/${PREFIX}.1to1.i90.l1000.delta"

show-coords -THrd "$WORKDIR/${PREFIX}.1to1.i90.l1000.delta" > "$WORKDIR/${PREFIX}.1to1.i90.l1000.coords"

syri \
  -c "$WORKDIR/${PREFIX}.1to1.i90.l1000.coords" \
  -d "$WORKDIR/${PREFIX}.1to1.i90.l1000.delta" \
  -r "$REF" \
  -q "$QRY" \
  --prefix "${PREFIX}." \
  --nc "$THREADS" \
  --nosnp

cat > "$WORKDIR/genomes.txt" <<EOF
${REF}	DM8.1	lc:blue
${QRY}	DM8.2	lc:orange
EOF

plotsr --sr "$WORKDIR/${PREFIX}.syri.out" --genomes "$WORKDIR/genomes.txt" -o "$WORKDIR/${PREFIX}.plotsr.pdf"
```

---

## Validation checklist

After generating or running the workflow, verify:

- `nucmer` produced `${PREFIX}.delta`
- `delta-filter` produced a filtered `.delta`
- `show-coords` produced a non-empty `.coords`
- `syri` exited successfully and produced `${PREFIX}.syri.out`
- `plotsr` produced the requested figure file
- if Syri printed chromosome ID mismatch warnings, inspect `${PREFIX}.mapids.txt`

Useful quick checks:
```bash
wc -l ${PREFIX}.1to1.i90.l1000.coords
head -n 3 ${PREFIX}.1to1.i90.l1000.coords
ls -lh ${PREFIX}.syri.out ${PREFIX}.plotsr.pdf
```

---

## Common pitfalls

### 1. Wrong environment
If `which syri`, `which nucmer`, or `which plotsr` do not point into the dedicated `syri_env`, the workflow may break or become irreproducible.

Check with:
```bash
conda activate syri_env
which nucmer delta-filter show-coords syri plotsr
```

### 2. pandas/numpy too new
If Syri throws:
```text
ValueError: buffer source array is read-only
```
check for:
- `numpy 2.x`
- `pandas 3.x`

Use the pinned versions in this skill instead.

### 3. Inconsistent file prefixes
A mismatch between:
- delta file names
- coords file names
- Syri `--prefix`

is a common source of confusion. Keep one naming scheme.

### 4. Numeric chromosome IDs or mismatched chromosome IDs
Syri may fail if chromosome IDs are purely numeric. Prefer IDs like:
- `Chr1`
- `chr01`

instead of:
- `1`

Syri can also emit warnings when reference and query chromosome IDs do not match exactly. In such cases, inspect `${PREFIX}.mapids.txt` to see the automatic chromosome mapping that was used.

### 5. Expecting SNP outputs while using `--nosnp`
If `--nosnp` is enabled, Syri may warn about SNP-related outputs. That is acceptable when the task is structural rearrangement analysis only.

---

## Preferred assistant behavior when using this skill

When helping the user, prefer this order:

1. inspect actual input filenames in the working directory
2. normalize them into consistent variables
3. confirm the planned parameters with the user before running
4. generate the **full pipeline script in one pass**
5. if requested, also generate per-step scripts
6. verify the environment and outputs after running

Do not only provide abstract advice when the task is actionable.
