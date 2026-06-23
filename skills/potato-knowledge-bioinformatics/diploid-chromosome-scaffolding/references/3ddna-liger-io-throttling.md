# 3D-DNA LIGer I/O throttling note

Context: diagnosing a historical local `58_assm/hic_based_on_luojilin` run after a hap1 3D-DNA run saturated system I/O and was cancelled. The reference examples used Juicer CPU followed by 3D-DNA `run-asm-pipeline.sh -m haploid -i 15000 -r 0 --mapq 10 --sort-output -g 1000`.

## Observed symptom

- Slurm job cancelled while 3D-DNA log was at `...Starting iteration # 1`.
- `h.scores.step.1.txt` remained 0 bytes.
- `merged_nodups.txt` was about 104 GB, estimated ~244 million lines.
- No final 3D-DNA outputs were produced.

## Root cause pattern

The outer 3D-DNA parameters may match the reference script, but the installed 3D-DNA internals can ignore the job's intended CPU/I/O envelope:

- `run-asm-pipeline.sh` auto-detects GNU Parallel and sets `parallel=true` when available.
- It calls `scaffold/run-liger-scaffolder.sh -p ${parallel} ...` during round 0 scaffolding even when `-r 0` is set. `-r 0` disables additional misjoin-correction rounds; it does **not** skip round 0 LIGer scaffolding.
- `run-liger-scaffolder.sh` contains heavy commands such as:
  - `parallel -a $mergelib --jobs 80% --pipepart --block 1G ... | LC_ALL=C sort ...`
  - `sort -S8G --parallel=48 ...`
- These defaults use whole-node-ish resources and hard-coded sort parallelism rather than Slurm `--cpus`, causing high concurrent reads of the MND file and large sort temporary I/O.

## Diagnostic checks

```bash
# Queue and residual processes
squeue -u "$USER"
ps -eo pid,ppid,stat,pcpu,pmem,etime,comm,args --sort=-pcpu | grep -E '3d-dna|run-asm|parallel -a|scrape-mnd|merged_nodups' || true

# Logs and partial outputs
ls -lh work/*/3ddna logs/*3ddna* 2>/dev/null || true
tail -n 80 logs/<sample>.3ddna.log

# MND size and rough line estimate (avoid full wc -l on huge files in foreground)
python3 - <<'PY'
from pathlib import Path
p=Path('work/hap1/juicer/aligned/merged_nodups.txt')
with p.open('rb') as f:
    data=f.read(1024*1024)
print(p.stat().st_size, 'bytes; first_MB_lines=', data.count(b'\n'))
PY

# Slurm isolation clues; lack of task/cgroup means do not assume CPU/I/O containment
scontrol show config | sed -n '/^ProctrackType/p;/^TaskPlugin/p;/^SelectType/p;/^AccountingStorageType/p'
```

## Safer rerun approach

1. Do not rerun the original installed 3D-DNA scripts unchanged after an I/O saturation event.
2. Clean only the failed haplotype 3D-DNA work directory after confirming final outputs are absent; stale `*.cprops`, `*.mnd.*.txt`, and `h.*` files can cause rerun failures or mixed intermediates.
3. Before running, inspect the original 3D-DNA code actually being used:
   ```bash
   THREEDDNA_DIR=/path/to/3d-dna
   bash "${THREEDDNA_DIR}/run-asm-pipeline.sh" -h 2>&1 | sed -n '1,80p'
   grep -nEi 'thread|cpu|core|parallel|jobs|--threads|--cpus' "${THREEDDNA_DIR}/run-asm-pipeline.sh" || true
   grep -RInE -- '--jobs|--parallel=' "${THREEDDNA_DIR}"/{scaffold,visualize,edit,polish,split} 2>/dev/null || true
   ```
4. If `run-asm-pipeline.sh` still lacks a real thread/CPU option and the module scripts still contain high/fixed parallelism such as `--jobs 80%`, `--parallel=48`, or `--parallel=24`, **stop before execution** and notify the user. State that the intended plan is to make a project-local 3D-DNA copy and patch internal parallelism to:
   - `--jobs 24`
   - `--parallel=24`
   Explain that this avoids excessive internal parallelism and reduces the risk of high I/O pressure on large `merged_nodups.txt` files. Request explicit approval before editing or running the patched copy.
5. Use a project-local copy or wrapper of the 3D-DNA scripts, not a global environment modification, when changing throttling behavior.
6. Prefer launching the actual 3D-DNA payload through `srun` so Slurm CPU affinity is applied to the payload process, and wrap it with low CPU/I/O scheduling priority:
   ```bash
   srun --ntasks=1 \
        --cpus-per-task="${SLURM_CPUS_PER_TASK:-24}" \
        --cpu-bind=cores \
        ionice -c2 -n7 nice -n 10 \
        bash "${THREEDDNA_DIR}/run-asm-pipeline.sh" \
          -m haploid \
          -i 15000 \
          -r 0 \
          --mapq 10 \
          --sort-output \
          -g 1000 \
          "${REF}" "${MND}"
   ```
   Add an affinity check before the real run when diagnosing resource behavior:
   ```bash
   srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-24}" --cpu-bind=verbose,cores \
     bash -lc 'grep Cpus_allowed_list /proc/self/status; nproc'
   ```
7. Run only one haplotype's 3D-DNA step at a time.
8. If prefiltering MND by `--mapq` to reduce input size, treat that scan as an I/O-heavy job too: submit it separately with low parallelism/priority and do not run it in the foreground.

## Parameter consistency lesson

When a user provides reference scripts, preserve both flags and resource-shaped details. In the Luo-Jilin-style `juicer.sh`, `-t 24` was the reference thread count. Do not silently record or default Juicer to 80 threads unless the user explicitly requests it.
