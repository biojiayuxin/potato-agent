# 58 材料 hifiasm `-l0` 重跑记录

This note captures a reusable pattern observed while preparing a strict one-parameter rerun for diploid potato HiFi + Hi-C assembly.

## Situation

- Existing assembly outputs already existed under `hifiasm_out/` for the same sample.
- User requested a rerun with only `-l0`, all other parameters default.
- Goal was a clean comparison against earlier standard, `-s 0.45`, and `--hom-cov 51` runs.

## Reusable pattern

- Use a new prefix, e.g. `sample.hifiasm.l0`, not the old `sample.hifiasm` prefix.
- Keep the command minimal:
  ```bash
  hifiasm -o PREFIX -t THREADS -l0 --h1 HIC_R1 --h2 HIC_R2 HIFI
  ```
- Do not add optional tuning flags unless the user explicitly requests them.
- Write a guard into the wrapper script so existing files for the new prefix cause an immediate exit.
- Convert GFA to FASTA after the run and collect `seqkit stats` if available.
- Verify with `bash -n` and a Slurm `--print-only` submission dry-run before starting a long job.

## Comparison reminder

For strict reruns, avoid reusing old `.bin` files by default. This keeps the new result independent and easier to interpret. If the user later asks for a speed-optimized rerun, a separate cached variant can be created intentionally.
