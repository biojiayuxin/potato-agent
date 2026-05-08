---
name: "slurm-for-long-running-tasks"
description: "将长时间运行的计算任务（批量下载、MD5 校验、基因组索引构建、大规模转录组比对/定量等）提交到 Slurm 后台，避免前台超时中断。提供 submit-job / list-jobs / job-status / cancel-job 四个脚本，优先使用打包脚本而非手写 sbatch/squeue。"
---

# Slurm for Long-Running Tasks

将耗时较长的生物信息学计算任务提交到 Slurm 后台运行。适用于：
- 批量 FASTQ 下载与完整性校验
- 大规模 MD5 / 文件校验
- 基因组索引构建（STAR / HISAT2 / bowtie2 等）
- 转录组比对、定量、差异表达等 Snakemake 流程
- 其他需要数小时、不可在前台跑完的计算

This host currently has:
- one partition: `main`
- one node: `agent-server`
- max schedulable memory per job: `100G`
- no Slurm accounting database, so `sacct` history is unavailable

## Rules

- Use the bundled scripts under `scripts/` instead of hand-writing `sbatch`, `squeue`, `scontrol`, or `scancel`.
- Stay in normal-user mode. Do not use `sudo` for job submission or job inspection.
- Do not cancel a job unless the user explicitly asks to cancel that specific job.
- If the user has not provided enough resource information for a safe submission, use `scripts/submit-job.sh --print-only ...` first and show the command you plan to submit.
- Do not claim that completed or failed historical jobs can be queried reliably with `sacct` on this host. They cannot, because accounting is disabled.

## Script Map

All scripts are relative to this skill's root directory (`SKILL_DIR`). After loading the skill, resolve `SKILL_DIR` to the actual installation path (e.g., `~/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks/`), then call scripts as `"${SKILL_DIR}/scripts/..."`.

- `scripts/submit-job.sh`
  - Submit a job from either `--command` or `--script`
  - Requires explicit `--time` and `--mem-gb`
  - Defaults to partition `main`, current directory as workdir, and `slurm-%j.out` for stdout/stderr

- `scripts/list-jobs.sh`
  - List active jobs for the current user by default
  - Good first step when the user asks "what is running" or "what is queued"

- `scripts/job-status.sh`
  - Show detailed status for one job ID
  - Uses active-controller data only
  - If the job has already left the queue, the script explains that historical state is unavailable

- `scripts/cancel-job.sh`
  - Cancel one active job
  - Requires an explicit job ID
  - Use `--yes` only after the user has clearly asked to cancel that job

## Workflow

1. Determine whether the user wants to submit, list, inspect, or cancel jobs.
2. Choose the matching script from `scripts/`.
3. For submission:
   - Prefer `--script` when the user already has a batch script.
   - Use `--command` for straightforward one-liners.
   - If the payload needs multiple shell steps, exports environment variables, or contains `$VARS` that may interact badly with shell quoting or `set -u`, write a small wrapper script and submit with `--script` instead of forcing everything through `--command`.
   - Require explicit `--time` and `--mem-gb`.
   - Respect the 100G per-job memory cap.

## Pitfalls

- **Always resolve scripts via `SKILL_DIR`.** Hard-coded shortcuts like `~/slurm-skill/` are not portable and may not exist on a given host. After loading the skill, resolve `SKILL_DIR` to the actual installation path before calling any script.
- On this server, `AccountingStorageType=(null)` means Slurm has no accounting database configured. **Do NOT pass `--account=X` to `sbatch`** — jobs submitted with `--account` will stay `PENDING` forever with reason `InvalidAccount`. The `submit-job.sh` wrapper correctly omits `--account`, so prefer it over raw `sbatch`. If using raw `sbatch`, omit `--account` entirely.
- On this host, complex inline `--command` payloads can fail when variable expansion collides with strict-shell behavior such as `set -u`. A real observed example was `XDG_CACHE_HOME` triggering an "unbound variable" error during submission logic.
- For Snakemake or other tools that need exported cache/config variables, prefer a dedicated batch script that does `set -euo pipefail`, `export ...`, `mkdir -p ...`, then `exec` the real command.
- Use `scripts/submit-job.sh --print-only ...` first when validating quoting, resource flags, output path, and working directory for a new submission pattern.
4. For inspection:
   - Use `scripts/list-jobs.sh` for a queue summary.
   - Use `scripts/job-status.sh JOBID` for a single job.
5. For cancellation:
   - Only use `scripts/cancel-job.sh --yes JOBID` after explicit user confirmation.

## Examples

```bash
# SKILL_DIR is the root of this skill (resolve to actual path after loading)
bash "${SKILL_DIR}/scripts/submit-job.sh" \
  --job-name fastqc \
  --cpus 8 \
  --mem-gb 16 \
  --time 04:00:00 \
  --command 'fastqc *.fastq.gz'
```

```bash
bash "${SKILL_DIR}/scripts/list-jobs.sh"
```

```bash
bash "${SKILL_DIR}/scripts/job-status.sh" 12345
```

```bash
bash "${SKILL_DIR}/scripts/cancel-job.sh" --yes 12345
```
