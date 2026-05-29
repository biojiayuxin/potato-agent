# hifiasm `--hom-cov` 调参：复用前一轮 bin 缓存的安全做法

## 场景

用户在同一 HiFi + Hi-C 数据上只调整 `--hom-cov`，例如 `homcov26` 跑完后再运行 `homcov53`，希望复用上一轮生成的 hifiasm bin 文件，避免从纠错/overlap 阶段重头计算。

## 已验证模式

工作目录示例：`/mnt/data/potato_agent/work/58_assm`

前一轮缓存前缀：

```bash
CACHE_PREFIX=hifiasm_out/58.hifiasm.homcov26
```

新一轮输出前缀：

```bash
PREFIX=hifiasm_out/58.hifiasm.homcov53
```

可复用的 bin 文件：

```bash
${CACHE_PREFIX}.ec.bin
${CACHE_PREFIX}.ovlp.source.bin
${CACHE_PREFIX}.ovlp.reverse.bin
${CACHE_PREFIX}.hic.tlb.bin
${CACHE_PREFIX}.hic.lk.bin
```

## 推荐缓存准备方式

不要默认直接软链接旧缓存到新前缀，因为 `--hom-cov` 变化后 hifiasm 可能检测并更新 `hic.*.bin`；软链接会带来改写源缓存的风险。

默认使用 copy/reflink：

```bash
cp --reflink=auto -p "${CACHE_PREFIX}.${ext}" "${PREFIX}.${ext}"
```

原因：

- 若文件系统支持 CoW reflink，几乎不额外占用完整空间；
- 若不支持，会复制约数十 GB 缓存，但仍比完整重跑 correction/overlap 快；
- 可保护前一轮缓存和结果的可复现性。

仅当用户明确接受源缓存可能被更新/改写风险，并且希望最小化额外空间时，才提供 `CACHE_MODE=symlink` 选项。

## 脚本安全检查

新脚本应在运行前检查：

```bash
if compgen -G "${PREFIX}.*" >/dev/null; then exit 1; fi
[ ! -e "${LOG}" ] || exit 1
[ ! -e "${STATS}" ] || exit 1
for ext in ec.bin ovlp.source.bin ovlp.reverse.bin hic.tlb.bin hic.lk.bin; do
  test -s "${CACHE_PREFIX}.${ext}" || exit 1
done
```

并使用独立日志与统计：

```bash
LOG=logs/58.hifiasm.homcov53.log
STATS=qc/58.hifiasm.homcov53.seqkit_stats.txt
```

## Slurm 提交记录示例

用户明确要求资源时照做，不要擅自提高资源：

```bash
bash "${SKILL_DIR}/scripts/submit-job.sh" \
  --job-name hifiasm_58_homcov53 \
  --cpus 80 \
  --mem-gb 40 \
  --time 2-00:00:00 \
  --workdir /mnt/data/potato_agent/work/58_assm \
  --output /mnt/data/potato_agent/work/58_assm/logs/hifiasm_58_homcov53.%j.out \
  --error /mnt/data/potato_agent/work/58_assm/logs/hifiasm_58_homcov53.%j.err \
  --script /mnt/data/potato_agent/work/58_assm/scripts/run_58_hifiasm_hic_homcov53_reuse_bins.sh
```

提交后记录 job ID、stdout/stderr、hifiasm 主日志到方案文档。

## 运行初期“看起来没跑起来”的诊断

使用 `CACHE_MODE=copy` 时，脚本会先复制/CoW reflink 多个大型 bin（本例约 55 GB），然后才启动 hifiasm。因此运行初期可能出现：

- Slurm 作业状态为 `RUNNING`；
- `logs/<sample>.hifiasm.homcov53.log` 尚不存在或大小为 0；
- Slurm stdout 只打印到 `copy/reflink cache: ...hic.tlb.bin` / `...hic.lk.bin`；
- 进程列表中看到的是 `cp --reflink=auto -p ...hic.lk.bin ...`，而不是 hifiasm。

这不是失败，而是在准备缓存。不要急于取消；先检查复制是否仍在推进：

```bash
# Slurm 仍在运行
bash "${SKILL_DIR}/scripts/job-status.sh" JOBID

# 查看 stdout 停在哪个缓存文件
 tail -n 50 logs/hifiasm_58_homcov53.JOBID.out

# 看 cp/hifiasm 进程
ps -u potato_agent -o pid,ppid,stat,etime,pcpu,pmem,args | \
  grep -E 'run_58_hifiasm_hic_homcov53|cp --reflink|hifiasm -o .*homcov53' | grep -v grep

# 看目标 bin 是否持续增大；若增大则说明缓存复制正在进行
stat -c '%n %s %y' hifiasm_out/58.hifiasm.homcov53.hic.lk.bin
sleep 10
stat -c '%n %s %y' hifiasm_out/58.hifiasm.homcov53.hic.lk.bin
```

复制完成后 stdout 会继续打印 `=== hifiasm HiFi + Hi-C rerun...`，随后进程列表会出现 `hifiasm -o ...homcov53 ...`，主日志才开始有内容。若复制卡住很久且目标文件大小不变，再进一步诊断磁盘/IO/进程状态。

## 结果检查

完成后检查缓存是否被复用、参数是否生效：

```bash
grep -E "loaded corrected reads and overlaps from disk|Loading|Renew Hi-C|homozygous read coverage threshold|purge duplication coverage threshold|CMD|Peak RSS|ERROR|WARNING" \
  logs/58.hifiasm.homcov53.log
```

期望至少确认：

```text
homozygous read coverage threshold: 53
[M::main] CMD: ... --hom-cov 53 ...
```

同时输出基础统计和 N50/L50：

```bash
seqkit stats hifiasm_out/58.hifiasm.homcov53.hic.*.p_ctg.fa \
  > qc/58.hifiasm.homcov53.seqkit_stats.txt
seqkit stats -a \
  hifiasm_out/58.hifiasm.homcov53.hic.hap1.p_ctg.fa \
  hifiasm_out/58.hifiasm.homcov53.hic.hap2.p_ctg.fa \
  > qc/58.hifiasm.homcov53.hap_N50_L50.seqkit_stats_a.txt
```

`seqkit stats -a` 的 `N50_num` 可作为 L50。

## 已验证结果模式（58_assm homcov53）

本例 `homcov53` 复用 `homcov26` bin 后正常完成，日志确认：

```text
[M::ha_assemble::...] ==> loaded corrected reads and overlaps from disk
[M::purge_dups] homozygous read coverage threshold: 53
[M::purge_dups] purge duplication coverage threshold: 66
[M::main] Real time: 3841.558 sec; CPU: 29434.806 sec; Peak RSS: 62.674 GB
```

结果统计：

```text
hap1:   1,432 contigs; 771,230,941 bp; N50 19,909,810 bp; L50 13; max 59,850,728 bp
hap2:     334 contigs; 882,974,823 bp; N50 30,023,502 bp; L50 10; max 65,395,841 bp
primary: 1,303 contigs; 1,018,404,820 bp; max 67,113,587 bp
```

判断要点：`homcov53` 与 `homcov51` 结果几乎一致（hap1 约 771 Mb、hap2 约 883–886 Mb、primary 约 1.018 Gb），明显优于 `homcov26` 的双 hap 约 1.27–1.33 Gb 冗余膨胀。做最终选择时仍需结合 BUSCO、Hi-C contact map 和污染/冗余筛查。