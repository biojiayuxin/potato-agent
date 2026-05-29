# hifiasm 调参重跑：micromamba 环境、Slurm 提交与结果检查注意事项

本记录来自 58 材料 `--hom-cov 51` 派生 `--hom-cov 26` 的一次实际会话，用于补充 hifiasm 调参重跑类任务。适用于“只改一个 hifiasm 参数、用独立前缀重跑、检查结果是否可用”的场景。

## 关键经验

1. **环境位置优先级**
   - 用户明确纠正：hifiasm/seqkit/gfatools 环境不要安装到项目工作目录 `WORKDIR/envs/hifiasm`。
   - 本服务器应优先使用用户 micromamba 环境目录，例如：
     ```bash
     /mnt/data/potato_agent/.micromamba/envs/hifiasm
     ```
   - 如果旧脚本写死 `ENV=/path/to/workdir/envs/hifiasm`，先改脚本和方案，再安装/验证环境；不要为了匹配旧脚本把新环境建到项目目录。

2. **提交前最小复核**
   ```bash
   cd /mnt/data/potato_agent/work/58_assm
   SCRIPT=scripts/run_58_hifiasm_hic_homcov26.sh
   ENV=/mnt/data/potato_agent/.micromamba/envs/hifiasm
   PREFIX=hifiasm_out/58.hifiasm.homcov26
   LOG=logs/58.hifiasm.homcov26.log
   STATS=qc/58.hifiasm.homcov26.seqkit_stats.txt

   bash -n "${SCRIPT}"
   test -x "${ENV}/bin/hifiasm"
   test -x "${ENV}/bin/seqkit"
   "${ENV}/bin/hifiasm" --version
   "${ENV}/bin/seqkit" version
   for f in data/58_hifi_reads.fastq.gz data/58_HiC_R1.fq.gz data/58_HiC_R2.fq.gz; do test -s "$f"; done
   if compgen -G "${PREFIX}.*" >/dev/null; then exit 1; fi
   if [ -e "${LOG}" ] || [ -e "${STATS}" ]; then exit 1; fi
   ```

3. **用户明确给出 Slurm 资源时**
   - 若用户明确指定 CPU、内存和时长，可直接按用户值提交，不再二次确认。
   - 仍需提交前做脚本语法、软件、输入、输出冲突检查。
   - 若用户指定内存低于历史同类运行的 Peak RSS（例：`homcov51` Peak RSS 约 59.553 GB，而用户要求 40G），按要求提交，但在结果中提示风险；不要擅自改大内存。

4. **提交后记录**
   - 返回 Job ID、状态、资源、脚本路径、stdout/stderr、hifiasm 主日志。
   - 可把实际提交记录追加到该任务的运行方案 Markdown，避免方案中推荐资源与实际提交资源不一致。

## 示例提交

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks
bash "${SKILL_DIR}/scripts/submit-job.sh" \
  --job-name hifiasm_58_homcov26 \
  --cpus 80 \
  --mem-gb 40 \
  --time 4-00:00:00 \
  --workdir /mnt/data/potato_agent/work/58_assm \
  --output /mnt/data/potato_agent/work/58_assm/logs/hifiasm_58_homcov26.%j.out \
  --error /mnt/data/potato_agent/work/58_assm/logs/hifiasm_58_homcov26.%j.err \
  --script /mnt/data/potato_agent/work/58_assm/scripts/run_58_hifiasm_hic_homcov26.sh
```

## 运行后检查模板

Slurm accounting 未启用时，作业离开 active queue 后不能用 `sacct` 判定最终状态。按以下顺序检查：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks
bash "${SKILL_DIR}/scripts/job-status.sh" JOBID || true
bash "${SKILL_DIR}/scripts/list-jobs.sh" || true

# 检查 stdout/stderr 是否出现 Done 或错误
cat logs/hifiasm_58_homcov26.JOBID.out
cat logs/hifiasm_58_homcov26.JOBID.err

# 检查 hifiasm 主日志关键字段
grep -E "homozygous read coverage threshold|purge duplication coverage threshold|heterozygous bases|homozygous bases|CMD|Version|Real time|Peak RSS|ERROR|WARNING|Killed|已杀死" \
  logs/58.hifiasm.homcov26.log

# 检查输出和 seqkit 统计
ls -lh hifiasm_out/58.hifiasm.homcov26.hic.*.gfa hifiasm_out/58.hifiasm.homcov26.hic.*.fa
cat qc/58.hifiasm.homcov26.seqkit_stats.txt
```

正常完成的常见证据：

- Slurm stdout 中出现 `hifiasm finished`、`Done.` 和 hap1/hap2/primary FASTA 路径。
- hifiasm 主日志末尾有 `[M::main] Version`、`CMD`、`Real time`、`Peak RSS`。
- hap1/hap2/primary 的 GFA、FA 和 seqkit stats 均存在且非空。
- stderr 只有 seqkit 进度条不算错误。

## 本次 homcov26 结果解释

实际关键日志：

```text
hifiasm version: 0.25.0-r726
CMD: hifiasm -o hifiasm_out/58.hifiasm.homcov26 -t 80 --hom-cov 26 --h1 data/58_HiC_R1.fq.gz --h2 data/58_HiC_R2.fq.gz data/58_hifi_reads.fastq.gz
homozygous read coverage threshold: 26
purge duplication coverage threshold: 32
heterozygous bases: 542,244,388
homozygous bases: 1,057,152,298
Real time: 8526.873 sec
CPU time: 415792.446 sec
Peak RSS: 61.006 GB
```

seqkit 统计：

```text
hap1:   1,702 contigs; 1,326,599,329 bp; max 60,168,697 bp
hap2:     438 contigs; 1,267,480,317 bp; max 65,300,041 bp
primary: 1,683 contigs; 1,350,073,969 bp; max 65,856,599 bp
```

解释要点：

- `homcov26` 的 hap1/hap2 长度彼此更平衡（大/小约 1.05），但二者都显著高于 GenomeScope 约 688–690 Mb 的单倍型预期。
- hap1 约为预期的 1.92 倍，hap2 约为 1.84 倍，primary 约 1.96 倍；这通常提示 `--hom-cov 26` 可能保留了过多冗余/重复序列。
- 不要只因 hap1/hap2 更平衡就认为该版本更优；需要同时比较长度是否接近预期、BUSCO、Hi-C contact map、污染/冗余筛查。
- 该例中不建议直接把 `homcov26` 作为最终版本；`homcov51`、standard 或 `s045` 需要结合下游质量评估再定。
