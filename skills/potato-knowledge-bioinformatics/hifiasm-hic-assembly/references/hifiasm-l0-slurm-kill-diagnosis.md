# hifiasm `-l0` rerun killed process diagnosis note

Session context: 58 diploid potato HiFi + Hi-C hifiasm rerun with only `-l0` changed. Submitted through Slurm with `--cpus 100 --mem-gb 40 --time 2-00:00:00`.

## Observed failure

- Slurm job left active queue; accounting unavailable, so historical state could not be queried with `sacct`.
- Slurm stderr contained only shell/SIGKILL-style message:
  ```text
  /var/lib/slurm/slurmd/job00120/slurm_script: line 58: 3142907 Killed hifiasm ...
  ```
- No final hifiasm `[M::main]` summary was written.
- `hic.p_ctg.gfa` existed, but haplotype outputs were missing:
  - missing `*.hic.hap1.p_ctg.gfa`
  - missing `*.hic.hap2.p_ctg.gfa`
  - missing `*.hic.hap1.p_ctg.fa`
  - missing `*.hic.hap2.p_ctg.fa`
  - missing `qc/*.seqkit_stats.txt`

## Important correction

Do **not** immediately state that Slurm killed the process because `--mem` was exceeded. On this host, Slurm may use memory for scheduling rather than cgroup enforcement.

In the observed configuration:

```text
ProctrackType = proctrack/linuxproc
TaskPlugin = task/affinity
SelectType = select/cons_tres
SelectTypeParameters = CR_CORE_MEMORY
AccountingStorageType = (null)
JobAcctGatherType = jobacct_gather/linux
/etc/slurm/cgroup.conf absent
```

There was no `task/cgroup` and no cgroup memory enforcement evidence. `--mem=40G` was not enough to prove Slurm hard-killed the process.

## Better diagnostic sequence

1. Inspect Slurm job status via the wrapper while active:
   ```bash
   SLURM_SKILL_DIR="${SLURM_SKILL_DIR:?set SLURM_SKILL_DIR to the slurm-for-long-running-tasks skill directory}"
   bash "${SLURM_SKILL_DIR}/scripts/job-status.sh" JOBID || true
   ```
2. Inspect stdout/stderr and hifiasm log:
   ```bash
   tail -n 80 logs/hifiasm_58_l0.JOBID.err
   tail -n 80 logs/hifiasm_58_l0.JOBID.out
   tail -n 80 logs/58.hifiasm.l0.log
   ```
3. Check expected final outputs explicitly:
   ```bash
   ls -lh hifiasm_out/58.hifiasm.l0.hic.hap{1,2}.p_ctg.gfa \
          hifiasm_out/58.hifiasm.l0.hic.hap{1,2}.p_ctg.fa \
          qc/58.hifiasm.l0.seqkit_stats.txt
   ```
4. Check Slurm memory enforcement configuration before attributing the kill to Slurm:
   ```bash
   scontrol show config | grep -E 'ProctrackType|TaskPlugin|SelectType|SelectTypeParameters|JobAcctGatherType|AccountingStorageType|DefMem|MaxMem|VSizeFactor|OverTimeLimit'
   scontrol show node agent-server | grep -E 'RealMemory|AllocMem|FreeMem|CfgTRES|AllocTRES|CPUAlloc|CPUTot|State='
   test -f /etc/slurm/cgroup.conf && cat /etc/slurm/cgroup.conf
   ```
5. If permitted, ask an admin or check privileged logs around the failure time:
   ```bash
   journalctl -k --since 'YYYY-MM-DD HH:MM:SS' --until 'YYYY-MM-DD HH:MM:SS'
   dmesg -T | grep -Ei 'oom|out of memory|killed process|hifiasm|PID'
   grep -Ei 'oom|killed|hifiasm|PID|jobID' /var/log/syslog /var/log/kern.log /var/log/slurm/slurmd.log
   ```

## Interpretation for this observed case

- The process received SIGKILL or equivalent external kill.
- There was no direct evidence that Slurm enforced the 40G memory request.
- System-level OOM killer / systemd-oomd / manual or other external kill remained plausible, but confirmation required privileged logs.
- hifiasm log showed `@44.720GB` before failure, but earlier successful standard runs had final `Peak RSS: 69.460 GB`; intermediate `@...GB` entries are not necessarily final peak RSS.
- The `-l0` primary GFA was larger than standard primary assembly, suggesting more retained redundancy and potentially higher downstream memory demand.

## Future improvement

For reruns likely to be killed, wrap hifiasm with `/usr/bin/time -v` and/or a lightweight memory monitor so the last observed RSS and system memory are captured even if the process dies unexpectedly.
