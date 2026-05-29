# Juicer/3D-DNA lessons from 58 Luo-Jilin-style run

Context: 58 material hap1/hap2 were scaffolded with Juicer CPU outputs under `/mnt/data/potato_agent/work/58_assm/hic_based_on_luojilin`, using shared env `/mnt/data/potato_agent/envs/juicer-3ddna` and reference scripts in `ref_scripts/`.

## User workflow corrections

- Install Juicer/3D-DNA in a shared micromamba-style env (e.g. `/mnt/data/potato_agent/envs/juicer-3ddna`), not under the project work directory.
- When the user provides reference scripts, preserve their parameter set exactly unless explicitly asked to improve/extend it. Do not add convenience flags such as Juicer `-d/-g/-b/-S early` just because they seem useful.
- For long jobs, submit with the exact user-requested resources when provided; do not silently change CPU/memory/time.

## Juicer CPU specifics verified

Reference command shape:

```bash
bash ${JUICER_CPU}/scripts/juicer_final.sh \\
  -D ${JUICER_CPU} \\
  -z ${REF} \\
  -p ${CHROM_SIZES} \\
  -y ${SITE_FILE} \\
  -s DpnII \\
  -t 24 \\
  --assembly
```

- `--assembly` is sufficient for a 3D-DNA-prep run: official Juicer CPU help says `--assembly: For use before 3D-DNA; early exit and create old style merged_nodups`.
- Source behavior: `--assembly` sets `earlyexit=1; assembly=1`, and the early-exit branch writes `aligned/merged_nodups.txt` then exits.
- Therefore do **not** add `-S early` when `--assembly` is already used in a reference-script-compatible run.
- `SITE_FILE` is generated with Juicer's `generate_site_positions.py` from the prepared hap FASTA, e.g. `python3 ${JUICER_CPU}/misc/generate_site_positions.py DpnII ${GENOME_ID} ${REF}`.

## Performance and log interpretation

- After BWA alignment, Juicer's post-processing is partly single-thread bottlenecked by AWK:
  - `merged1.txt` and `merged30.txt` use `samtools view -@ threads | awk -f sam_to_pre.awk`.
  - `merged_nodups.txt` also uses `samtools view -@ threads | awk -v mnd=1 -f sam_to_pre.awk`.
  - Even with 80 CPUs, the AWK side may dominate wall time.
- `-T threadsHic` is for `.hic` creation and not useful when `--assembly` early-exits after `merged_nodups.txt`.
- With a newer `juicebox_tools.jar`, `juicer_tools statistics` may emit `Unknown command: statistics`; in this observed run, `merged_nodups.txt` was still produced and Juicer reported success, so for 3D-DNA the key acceptance check is a non-empty `aligned/merged_nodups.txt` plus inspected logs.

## 3D-DNA specifics verified

Reference command shape:

```bash
bash ${THREEDDNA_DIR}/run-asm-pipeline.sh \
  -m haploid \
  -i 15000 \
  -r 0 \
  --mapq 10 \
  --sort-output \
  -g 1000 \
  ${REF} ${MND}
```

- 3D-DNA derives `genomeid` from the FASTA basename, so output files are usually `<draft_prefix>_HiC.fasta` and `<draft_prefix>_HiC.assembly`, not necessarily `<prefix>.final.fasta`.
- Result-linking scripts should look for `<GENOME_PREFIX>_HiC.fasta` and `<GENOME_PREFIX>_HiC.assembly` and then create stable project-level symlinks such as `<GENOME_ID>.chromosome.fa` and `<GENOME_ID>.chromosome.assembly`.
