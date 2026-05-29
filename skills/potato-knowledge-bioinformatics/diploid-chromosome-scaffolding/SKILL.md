---
name: diploid-chromosome-scaffolding
description: "Snakemake workflow template for haplotype Hi-C scaffolding: Juicer CPU -> 3D-DNA."
version: 0.2.3
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [hic, juicer, 3d-dna, diploid, chromosome-scaffolding, snakemake]
    related_skills: [slurm-for-long-running-tasks]
---

# Diploid Chromosome Scaffolding

## Files

```text
templates/Snakefile
templates/config.yaml
references/juicer-3ddna-58-luojilin-lessons.md
references/3ddna-liger-io-throttling.md
```

## Copy

```bash
WORK=hic_scaffolding
mkdir -p "$WORK"
cp /path/to/diploid-chromosome-scaffolding/templates/Snakefile "$WORK/Snakefile"
cp /path/to/diploid-chromosome-scaffolding/templates/config.yaml "$WORK/config.yaml"
cd "$WORK"
```

Edit `config.yaml`.

## Dependencies

```text
snakemake
python3
java
bwa
samtools
juicer_final.sh or juicer.sh
generate_site_positions.py
run-asm-pipeline.sh
parallel
lastz  # only for three_d_dna.mode: diploid
```

```bash
micromamba create -y -n juicer-3ddna -c conda-forge -c bioconda \
  python=3.11 openjdk=11 bwa samtools snakemake-minimal \
  coreutils gawk parallel lastz 3d-dna
micromamba activate juicer-3ddna
mkdir -p "$CONDA_PREFIX/share"
git clone https://github.com/aidenlab/juicer.git "$CONDA_PREFIX/share/juicer"
```

## Configuration

Edit `config.yaml` for sample names, assembly FASTA paths, Hi-C FASTQ paths, enzyme settings, software paths, threads, 3D-DNA options, and rerun policy.

When the user provides known-good reference scripts, preserve their parameter set exactly unless they explicitly ask for extensions. For a Juicer CPU -> 3D-DNA assembly-style run, `--assembly` already implies early exit and old-style `merged_nodups.txt` creation; do not add `-S early` on top of it. For 3D-DNA, mirror reference-script flags such as `-m haploid -i 15000 -r 0 --mapq 10 --sort-output -g 1000` when those are supplied.

Detailed configurable parameters are intentionally kept in `templates/config.yaml`; the exact execution logic and command construction are in `templates/Snakefile`.

Per-sample Hi-C reads can override global `hic_reads` by setting `samples.<sample>.r1` and `samples.<sample>.r2` in `config.yaml`.

Software paths may be set in `config.yaml`, resolved from supported environment variables, or found from `PATH` where implemented by the Snakefile.

## Run

```bash
snakemake -n -s Snakefile --configfile config.yaml --cores 1 --printshellcmds
snakemake -s Snakefile --configfile config.yaml --cores 24 --printshellcmds --rerun-incomplete
```

Long jobs: submit the same command through Slurm.

To run Juicer only, set `three_d_dna.enabled: false` in `config.yaml`.

## Outputs

Juicer:

```text
<outdir>/work/juicer/<sample>/references/<sample>.fa
<outdir>/work/juicer/<sample>/references/<sample>.chrom.sizes
<outdir>/work/juicer/<sample>/restriction_sites/<sample>_<enzyme>.txt
<outdir>/work/juicer/<sample>/aligned/merged_nodups.txt
<outdir>/results/juicer/<sample>/merged_nodups.txt
<outdir>/results/juicer/<sample>/juicer.done
<outdir>/logs/juicer/<sample>.juicer.log
```

3D-DNA:

```text
<outdir>/work/3ddna/<sample>/<sample>_HiC.fasta
<outdir>/work/3ddna/<sample>/<sample>_HiC.assembly
<outdir>/results/3ddna/<sample>/<sample>_HiC.fasta
<outdir>/results/3ddna/<sample>/<sample>_HiC.assembly
<outdir>/results/3ddna/<sample>/3ddna.done
<outdir>/logs/3ddna/<sample>.3ddna.log
```

## Rerun guard

```text
Juicer stale dirs: work/juicer/<sample>/aligned, work/juicer/<sample>/splits
3D-DNA stale files: work/3ddna/<sample>/<sample>.*.cprops, *.asm, *.hic, *.mnd.*.txt
```

Clean only the failed sample directory.

## Checks

```text
snakemake -n passes
config.yaml paths edited
FASTA/R1/R2 non-empty
juicer_final.sh or juicer.sh exists
generate_site_positions.py exists
run-asm-pipeline.sh exists
merged_nodups.txt non-empty
<sample>_HiC.fasta non-empty
<sample>_HiC.assembly non-empty
logs checked
```

## Notes

```text
hap1/hap2 separate scaffolding: three_d_dna.mode: haploid
three_d_dna.mode: diploid requires lastz
three_d_dna.early_exit: true does not produce final *_HiC outputs
Juicer --assembly already early-exits and creates old-style merged_nodups for 3D-DNA; -S early is redundant in this mode
Juicer post-alignment merged1/merged30/merged_nodups conversion is partly single-thread AWK-limited, even if samtools is given many threads
For Luo-Jilin-style reference scripts, preserve the Juicer reference thread count (`-t 24`) unless the user explicitly asks to change resources; do not silently default to 80 threads.
3D-DNA final outputs are typically <draft_prefix>_HiC.fasta and <draft_prefix>_HiC.assembly; link those rather than assuming <prefix>.final.fasta
3D-DNA jobs should preferentially launch the actual `run-asm-pipeline.sh` payload through `srun --ntasks=1 --cpus-per-task=${SLURM_CPUS_PER_TASK:-24} --cpu-bind=cores`, and wrap the payload with `ionice -c2 -n7 nice -n 10` to lower CPU/I/O scheduling priority on shared nodes.
Before running 3D-DNA, inspect the original `run-asm-pipeline.sh` and module scripts. If `run-asm-pipeline.sh` still lacks a real thread/CPU option and internal scripts still contain GNU Parallel / sort settings such as `--jobs 80%`, `--parallel=48`, or `--parallel=24`, notify the user that a local copy should be patched to `--jobs 24` and `--parallel=24` before execution, explain that this is to avoid high parallelism causing severe I/O pressure, and request approval before modifying or running.
3D-DNA round-0 LIGer scaffolding can saturate shared I/O on very large `merged_nodups.txt` files; for large MND inputs, use a user-approved local throttled 3D-DNA copy/wrapper, one haplotype at a time, and see `references/3ddna-liger-io-throttling.md`.
```
