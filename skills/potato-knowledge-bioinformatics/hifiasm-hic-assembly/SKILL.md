---
name: "hifiasm-hic-assembly"
description: "HiFi + Hi-C 数据的 hifiasm 分型基因组组装执行方案；适用于二倍体/杂合马铃薯材料，从原始数据核对、环境准备、Slurm 提交到 hap1/hap2 FASTA 统计。"
---

# hifiasm HiFi + Hi-C haplotype-resolved assembly

用于用户提供 PacBio HiFi reads 和 Hi-C paired-end reads，希望用 hifiasm 获得 hap1/hap2 分型 contig 的任务。马铃薯二倍体、杂合材料优先使用本流程。

## 触发场景

- 用户提到 hifiasm、HiFi、Hi-C、分型组装、haplotype-resolved assembly。
- 用户给出原始数据目录和工作目录，要求写执行方案或运行方案。
- 用户需要解释 hifiasm 参数含义。

## 标准流程

1. 读取用户给出的工作目录中的既有方案/README/脚本，不要直接套模板。
2. 搜索原始数据目录，确认实际输入文件：
   - HiFi FASTQ：通常 `*.fastq.gz` / `*.fq.gz`
   - Hi-C R1/R2：paired-end 文件，需保持 read 顺序一致。
3. 如有 GenomeScope/k-mer 结果，读取 `summary.txt` 和 `model.txt`：
   - 记录单倍型基因组大小。
   - 记录杂合率。
   - k-mer coverage 只能作为参考，不要无脑填入 `--hom-cov`。
4. 检查工具环境：
   ```bash
   command -v hifiasm || true
   command -v seqkit || true
   /opt/micromamba/bin/micromamba --version 2>/dev/null || true
   ```
   本服务器即使 PATH 中没有 micromamba，通常也有 `/opt/micromamba/bin/micromamba`。
   如果既有脚本显式写了 `ENV=/path/to/workdir/envs/hifiasm` 并 `export PATH="${ENV}/bin:${PATH}"`，必须按脚本同样的 PATH 验证，而不是只看当前 shell：
   ```bash
   ENV=/path/to/workdir/envs/hifiasm
   test -d "${ENV}" || echo "MISSING_ENV: ${ENV}"
   PATH="${ENV}/bin:${PATH}" command -v hifiasm || true
   PATH="${ENV}/bin:${PATH}" command -v seqkit || true
   PATH="${ENV}/bin:${PATH}" command -v gfatools || true
   ```
   若该环境目录不存在或缺少 `hifiasm`，即使历史结果文件存在，重跑脚本也会立即失败；先创建/修复环境再提交。
5. 不复制大 FASTQ，优先在工作目录建立软链接；检查时要同时确认软链接目标、非空、FASTQ 前几条可 gzip 读取且四行格式正确。对 Hi-C paired-end，抽查 R1/R2 前几条 read ID 去掉 `/1`、`/2` 后是否一致；这只能证明开头配对正常，不等同于全文件完整性校验。若要严格确认大 `*.gz` 完整性，不要在前台长时间跑 `gzip -t`；写成 Slurm 后台校验任务，并优先查找同目录的 md5/checksum 文件。
   ```bash
   mkdir -p data hifiasm_out logs scripts qc
   ln -sf /abs/path/to/hifi.fastq.gz data/sample_hifi.fastq.gz
   ln -sf /abs/path/to/hic_R1.fq.gz data/sample_HiC_R1.fq.gz
   ln -sf /abs/path/to/hic_R2.fq.gz data/sample_HiC_R2.fq.gz
   ```
6. 若缺少软件，优先安装到用户/共享 micromamba 环境目录，**不要默认装到项目工作目录**，避免项目目录膨胀和后续混淆。先确认用户指定的环境位置；若无特别指定，在本服务器优先使用 `/mnt/data/potato_agent/.micromamba/envs/hifiasm` 这类 micromamba envs 目录：
   ```bash
   MAMBA=/opt/micromamba/bin/micromamba
   ROOT=/mnt/data/potato_agent/.micromamba
   ENV=${ROOT}/envs/hifiasm
   MAMBA_ROOT_PREFIX="${ROOT}" ${MAMBA} create -y -p "${ENV}" \
     -c bioconda -c conda-forge hifiasm seqkit gfatools
   export PATH="${ENV}/bin:${PATH}"
   ```
   若既有脚本写死了项目目录环境（如 `WORKDIR/envs/hifiasm`），应先改脚本和方案中的 `ENV`，再安装/验证；不要为了匹配旧脚本而把新环境建到项目目录。

## 推荐 hifiasm 脚本模板

将样品名、工作目录、输入文件替换为实际值。

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKDIR=/path/to/workdir
ENV=/mnt/data/potato_agent/.micromamba/envs/hifiasm  # 或用户指定的 micromamba envs 目录
export PATH="${ENV}/bin:${PATH}"

cd "${WORKDIR}"
mkdir -p data hifiasm_out logs qc

HIFI="data/sample_hifi.fastq.gz"
HIC_R1="data/sample_HiC_R1.fq.gz"
HIC_R2="data/sample_HiC_R2.fq.gz"

SAMPLE="sample"
THREADS="${SLURM_CPUS_PER_TASK:-48}"
OUTDIR="hifiasm_out"
LOGDIR="logs"
PREFIX="${OUTDIR}/${SAMPLE}.hifiasm"

if ! command -v hifiasm >/dev/null 2>&1; then
  echo "ERROR: hifiasm not found in PATH. Expected env: ${ENV}" >&2
  exit 1
fi

hifiasm \
  -o "${PREFIX}" \
  -t "${THREADS}" \
  --h1 "${HIC_R1}" \
  --h2 "${HIC_R2}" \
  "${HIFI}" \
  2> "${LOGDIR}/${SAMPLE}.hifiasm.hic.log"

awk '/^S/{print ">"$2; print $3}' "${PREFIX}.hic.hap1.p_ctg.gfa" > "${PREFIX}.hic.hap1.p_ctg.fa"
awk '/^S/{print ">"$2; print $3}' "${PREFIX}.hic.hap2.p_ctg.gfa" > "${PREFIX}.hic.hap2.p_ctg.fa"
awk '/^S/{print ">"$2; print $3}' "${PREFIX}.hic.p_ctg.gfa" > "${PREFIX}.hic.p_ctg.fa"

if command -v seqkit >/dev/null 2>&1; then
  seqkit stats \
    "${PREFIX}.hic.hap1.p_ctg.fa" \
    "${PREFIX}.hic.hap2.p_ctg.fa" \
    "${PREFIX}.hic.p_ctg.fa" \
    > "qc/${SAMPLE}.hifiasm.seqkit_stats.txt"
fi
```

## 参数说明要点

- 版本升级参考：若用户询问 hifiasm `0.19.8-r603` 与新版本差异、默认参数是否变化，先看 `references/hifiasm-0.19.8-to-0.25.0-version-notes.md`。要点：0.20.0 引入新 error correction，0.25.0 修复小尺度误组装/重复拷贝 collapse；0.19.8 到 0.25.0 未发现已有 HiFi+Hi-C 核心默认参数大改，主要新增 ONT/端粒/小 contig 相关参数。
- `-o PREFIX`：输出前缀；hifiasm 会生成 `${PREFIX}.hic.hap1.p_ctg.gfa`、`${PREFIX}.hic.hap2.p_ctg.gfa`、`${PREFIX}.hic.p_ctg.gfa` 等。
- `-t THREADS`：线程数；Slurm 中应与 `--cpus` 一致。
- `--h1 HIC_R1`：Hi-C paired-end read 1，用于分相。
- `--h2 HIC_R2`：Hi-C paired-end read 2，必须与 R1 配对且顺序一致。
- 位置参数 HiFi FASTQ：hifiasm 主要长读长输入；多个 HiFi 文件可直接追加多个路径。
- `2> LOG`：shell stderr 重定向；hifiasm 主要日志写 stderr，需保存。

## 初次运行不建议添加的参数

- `-l0`：常用于近交/高度纯合基因组；二倍体杂合马铃薯初次组装不加。
- `--hom-cov INT`：仅当 hifiasm 自动估计的 **homozygous read coverage peak** 明显异常时再手动指定；它调的是同型峰，不是杂合峰。日志中先看 `peak_hom`、`peak_het`、`[M::purge_dups] homozygous read coverage threshold` 和 `[M::stat] # heterozygous bases ... # homozygous bases ...`。`--hom-cov` 会影响 `*p_utg*gfa`；官方文档说明调它时 `*hic*.bin` 需要重建（v0.15.5+ 可自动检测并更新，但为了可控比较，建议用新前缀；若用户明确希望复用已有 `*.bin`，可将旧前缀的 `ec.bin`、`ovlp.*.bin`、`hic.*.bin` 软链接到新前缀，hifiasm v0.25.0 会自动检测并更新需要重建的 Hi-C bin）。GenomeScope k-mer coverage 不是可直接填入的 read coverage。
- `--purge-max INT`：手动设置 Purge-dups 的 coverage upper bound，对应日志中的 `[M::purge_dups] purge duplication coverage threshold: N`。例如 `--hom-cov 51` 可能自动得到 `purge duplication coverage threshold: 63`；若用户想固定这个上限，可额外加 `--purge-max 63`。每次调整 `--purge-max` 都用新前缀，避免覆盖结果并便于比较 hap1/hap2/primary 长度。
- `-s FLOAT`：这是 duplicate haplotig purge 的相似度阈值，不是“杂合峰位置”。当 hap1/hap2 总长度严重不平衡时可尝试更小值，例如 `-s 0.45`；官方文档说明 `-s` 不改变 `*p_utg*gfa`，因此同一前缀下 `*hic*.bin` 可复用。
- `--dual-scaf`：可作为第二轮优化；先跑标准 Hi-C 分型组装，再比较连续性、BUSCO 和 contact map。

## 调参重跑与对照实验

当用户明确要求基于已有 hifiasm 结果调一个参数重跑（例如 `-l0`、`--hom-cov`、`-s`、`--purge-max`）时：

1. 先读取既有工作目录中的 `scripts/`、`logs/`、`qc/` 和方案文档，确认上一轮实际命令、线程、版本、峰值内存、输出前缀和已有结果；不要只按模板新写。
2. 使用独立输出前缀，例如 `sample.hifiasm.l0`、`sample.hifiasm.homcov51`，避免覆盖标准版或其它调参版。
3. 如果用户要做“只改一个参数，其它默认”的严格对照，脚本中只加入该参数；不要顺手加入 `--hom-cov`、`-s`、`--hg-size`、`--dual-scaf` 等其它组装调参项。
4. 对 `-l0` 对照：说明 `-l` 是 purge level，`-l0` 表示不做 purge；它可能保留更多重复/冗余序列，适合做对照但不一定更优。严格对照时默认不复用已有 `*.bin`，让新前缀完整生成；若用户明确要求加速，再单独做缓存复用版。
5. 脚本应检查输入软链接是否非空，并在同名前缀已有输出时退出，防止覆盖或混用旧中间文件：`if compgen -G \"${PREFIX}.*\" >/dev/null; then exit 1; fi`。
6. 从已有调参脚本派生新 `--hom-cov` 对照（如 `homcov51` → `homcov26`）时，必须系统性替换：脚本名、`PREFIX`、`LOG`、`STATS`、echo 中的命令展示、Slurm job name/stdout/stderr；不要只改 hifiasm 命令本体。若复用缓存，只链接标准基线前缀的 `ec.bin`、`ovlp.*.bin`、`hic.*.bin`，不要把其它调参前缀（如 `s045.*`）的缓存也混入，除非用户明确要求。
7. 只写方案时，也应同时落地 Markdown 方案和可运行脚本，并验证：`bash -n script.sh` 与 Slurm wrapper `--print-only`。

示例细节见 `references/hifiasm-l0-rerun-note.md`。`--hom-cov` 调参重跑时的 micromamba 环境位置、提交前复核、按用户指定 40G/80 CPU 提交及低于历史 Peak RSS 的风险提示，见 `references/hifiasm-homcov26-micromamba-slurm-note.md`。同一数据只调整 `--hom-cov` 并希望复用前一轮 bin 缓存时，优先用 `cp --reflink=auto -p` 将旧前缀 bin 准备为新前缀，避免软链接导致源缓存被 hifiasm 更新；若提交后 hifiasm 主日志暂时不存在/为 0，但 stdout 停在 `copy/reflink cache` 且 `cp --reflink` 进程或目标 bin 文件大小在增长，这是缓存准备阶段，不是没跑起来；诊断步骤见 `references/hifiasm-homcov53-bin-reuse-note.md`。

## 失败诊断与 SIGKILL 注意事项

- 如果 hifiasm 任务 stderr 只有 shell 的 `Killed` / `已杀死`，不要直接断言“Slurm 因 `--mem` 超限杀掉”。先检查 Slurm 是否启用了 `task/cgroup`、`/etc/slurm/cgroup.conf`、`ProctrackType`、`TaskPlugin`、`SelectTypeParameters`、`VSizeFactor`、`OverTimeLimit` 等配置。
- 在本服务器曾观察到：`ProctrackType=proctrack/linuxproc`、`TaskPlugin=task/affinity`、无 `cgroup.conf`，此时 `--mem` 更可能是调度资源而非硬限制；即使进程日志内存超过申请值，也不能据此认定 Slurm kill。
- 对失败结果要显式检查 hap1/hap2 GFA/FA 和 QC 文件是否存在；只生成 `*.hic.p_ctg.gfa` 不代表分型组装完成。
- hifiasm 日志中的中间 `@xxGB` 不是最终 Peak RSS；若无 `[M::main] ... Peak RSS` 行，需结合 `/usr/bin/time -v`、监控日志或系统 OOM 日志判断。
- 详细诊断记录见 `references/hifiasm-l0-slurm-kill-diagnosis.md`。

## Slurm 提交

hifiasm 属于长时间高内存任务，优先用 Slurm 后台提交。本服务器单任务上限约 100G，避免写 `--mem=120G`。建议先 dry-run。

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks
bash "${SKILL_DIR}/scripts/submit-job.sh" \
  --print-only \
  --job-name hifiasm_SAMPLE \
  --cpus 48 \
  --mem-gb 96 \
  --time 4-00:00:00 \
  --workdir /path/to/workdir \
  --output /path/to/workdir/logs/hifiasm_SAMPLE.%j.out \
  --error /path/to/workdir/logs/hifiasm_SAMPLE.%j.err \
  --script /path/to/workdir/scripts/run_hifiasm_hic.sh
```

正式提交前，若会实际启动长任务，应向用户确认 CPU、内存和时长。

## 组装目录清理与压缩前审计

当用户要求检查 hifiasm/Hi-C/3D-DNA 组装工作目录、列出可删除中间文件或压缩目录大小时：

1. 只做只读扫描，不要未经确认删除文件。
2. 先统计顶层和二级目录大小、最大文件、软链接目标，尤其检查 `results/` 中 final-looking 文件是否只是指向 `work/` 的软链接。
3. 常见可删除候选包括：失败运行目录、hifiasm `*.bin` 缓存、`p_utg/r_utg` GFA、Juicer `aligned/` 与 `splits/`、BWA index、非最终 3D-DNA `.hic` 中间版本、`.snakemake` 元数据。
4. 常见应保留内容包括：`data/` 软链接、`scripts/`、`logs/`、`qc/`、方案文档、最终 hifiasm FASTA、已解析为实体文件的最终 3D-DNA FASTA/assembly/contact map。
5. 用“优先可删除 / 谨慎删除 / 建议保留”给出紧凑表格，列 path/pattern、大小和理由；对软链接 final 文件给出明确风险提示。

详细审计命令和判定清单见 `references/assembly-directory-cleanup-audit.md`。

## 结果检查

- 检查输出存在且非空：
  ```bash
  ls -lh hifiasm_out/*.hic.*.gfa hifiasm_out/*.hic.*.fa
  ```
- 统计 hap1/hap2/primary：
  ```bash
  seqkit stats hifiasm_out/*.fa
  ```
- 与 GenomeScope 单倍型大小比较：hap1/hap2 应大致接近预期单倍型大小，不应极端不平衡。
- 查看日志：
  ```bash
  grep -E "coverage|homozygous|purge|Writing|ERROR|WARNING" logs/*.hifiasm*.log | head -n 100
  ```
- 调参重跑结果判断不要只看 hap1/hap2 是否平衡；还要与 GenomeScope 单倍型大小比较。若两套 haplotype 都接近预期的 2 倍，即使彼此平衡，也通常提示保留过多冗余/重复序列；`--hom-cov 26` 实例及检查模板见 `references/hifiasm-homcov26-micromamba-slurm-note.md`。
- 需要 N50/L50 时可用 `seqkit stats -a`，其中 `N50` 为 N50 长度，`N50_num` 对应 L50；也可保存到 `qc/<sample>.hap_N50_L50.seqkit_stats_a.txt` 便于对照。
- 后续建议：BUSCO、Hi-C contact map、污染筛查（GC/coverage/BLAST/kraken）。

## 注意事项

- 大 FASTQ 不要复制，除非用户明确要求；用软链接更安全。
- 软件环境不要默认放进项目目录；优先放到用户/共享 micromamba envs 目录（如 `/mnt/data/potato_agent/.micromamba/envs/hifiasm`），并同步更新运行脚本和方案中的 `ENV`。
- 不要把 Slurm 脚本中的内存写超过当前集群上限。
- 如果只写执行方案，可同时保存 Markdown 方案和可运行 shell 脚本，并用 `bash -n` 验证脚本语法、用 submit wrapper `--print-only` 验证提交命令。