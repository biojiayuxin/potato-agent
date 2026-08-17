---
name: ncbi-blast
description: 当用户需要把一条或多条核酸或蛋白查询序列比对到 /mnt/data/public_data 中指定马铃薯组装的 genome、CDS 或 pep 本地数据库时使用。优先复用共享 NCBI BLAST+ 数据库，缺库时仅为请求的目标类型建库，再运行 blastn、blastp、blastx、tblastn 或 tblastx。不要用于在线 nr/nt/Swiss-Prot、两条蛋白直接比较、全局比对、短读长比对或全基因组共线性分析。
version: 2.0.0
author: Potato Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [ncbi-blast, blast-plus, blastn, blastp, blastx, tblastn, genome, cds, protein]
    related_skills: [pairwise-protein-sequence-alignment, gffread-export-cds-pep, slurm-for-long-running-tasks]
---

# NCBI BLAST 本地基因组比对

## 能做什么

将用户提供的核酸或蛋白 FASTA 比对到指定马铃薯组装的整套基因组、CDS 或蛋白数据库。支持多条查询序列和 NCBI BLAST+ 的常用搜索参数。

不用于以下任务：

- 在线搜索 NCBI nr/nt、Swiss-Prot 等公共数据库；
- 仅比较两条蛋白序列，此时使用 `pairwise-protein-sequence-alignment`；
- 全局比对、短读长比对或全基因组共线性分析。

## 公共数据与数据库

默认公共数据根目录为 `/mnt/data/public_data`：

```text
/mnt/data/public_data/
  BLAST_DB/
  Genome_browser_DB/
    assemblies.tsv
```

用户明确提供其他根目录时使用用户指定位置。`assemblies.tsv` 中的 `sample` 或完整 `id` 用于确定 canonical assembly；读取表格并核对实际路径，不根据文件名猜测组装。

目标类型与标准数据库前缀：

| 目标 | 数据库分子类型 | 标准前缀 |
|---|---|---|
| `genome` | 核酸 | `BLAST_DB/<assembly>/<assembly>.genome` |
| `CDS` | 核酸 | `BLAST_DB/<assembly>/<assembly>.cds` |
| `pep` | 蛋白 | `BLAST_DB/<assembly>/<assembly>.protein` |

先查找目标前缀，并使用将要运行搜索的同一套 BLAST+ 环境执行：

```bash
blastdbcmd -db "${DB_PREFIX}" -info
```

验证通过后直接复用。若前缀文件存在但无法读取或验证失败，停止并报告问题，不覆盖或自动重建。

## 缺少数据库

只创建本次请求的目标数据库，不同时创建其他类型。

1. 从 `Genome_browser_DB/assemblies.tsv` 唯一定位组装，取得 `reference` 和需要时的 `annotation`。
2. 在任务工作目录准备临时 FASTA；`.gz/.bgz` 文件解压到临时位置，不修改公共 Genome Browser 原文件。
3. 根据目标执行对应流程：

### genome

直接用参考基因组 FASTA 建立核酸数据库：

```bash
makeblastdb -in "${GENOME_FASTA}" \
  -dbtype nucl -parse_seqids -blastdb_version 5 \
  -out "${DB_DIR}/${ASSEMBLY}.genome"
```

### CDS

加载 `gffread-export-cds-pep`，仅导出 CDS，再建立核酸数据库：

```bash
gffread "${ANNOTATION_GFF3}" -g "${GENOME_FASTA}" --adj-stop -x "${CDS_FASTA}"
makeblastdb -in "${CDS_FASTA}" \
  -dbtype nucl -parse_seqids -blastdb_version 5 \
  -out "${DB_DIR}/${ASSEMBLY}.cds"
```

### pep

加载 `gffread-export-cds-pep`，仅导出蛋白，再建立蛋白数据库：

```bash
gffread "${ANNOTATION_GFF3}" -g "${GENOME_FASTA}" --adj-stop -y "${PEP_FASTA}"
makeblastdb -in "${PEP_FASTA}" \
  -dbtype prot -parse_seqids -blastdb_version 5 \
  -out "${DB_DIR}/${ASSEMBLY}.protein"
```

建库前确认共享目录可写，并在写入前再次检查目标前缀，避免覆盖其他任务刚创建的数据库。建库后用 `blastdbcmd -info` 验证。大型参考组装或预计耗时较长时，加载 `slurm-for-long-running-tasks` 提交后台任务。

## 选择 BLAST 程序

根据查询序列和目标数据库的分子类型选择程序：

| 查询 | 目标 | 程序 |
|---|---|---|
| 核酸 | genome/CDS | `blastn` |
| 蛋白 | genome/CDS | `tblastn` |
| 核酸 | pep | `blastx` |
| 蛋白 | pep | `blastp` |

需要核酸到核酸的双向翻译搜索时可选择 `tblastx`。只含 A/C/G/T 等字符的短序列可能同时符合核酸和蛋白字母表，应结合用户描述和生物学语境判断。根据任务目标、序列长度和用户要求选择 `evalue`、`max_target_seqs`、`word_size`、线程数及输出格式，并在交付时说明重要参数。

## 运行 BLAST

本技能的 `scripts/run_ncbi_blast.py` 只负责组装并运行一条 BLAST 命令。调用前准备好查询 FASTA、数据库前缀、输出目录和参数。脚本路径必须从技能加载结果的 `skill_dir` 解析，不硬编码用户技能目录。

```bash
python3 "${SKILL_DIR}/scripts/run_ncbi_blast.py" \
  --program blastn \
  --query "${QUERY_FASTA}" \
  --database "${DB_PREFIX}" \
  --output "${RESULT_FILE}"
```

指定输出格式和搜索参数时，把 `--blast-args` 放在最后：

```bash
python3 "${SKILL_DIR}/scripts/run_ncbi_blast.py" \
  --program blastn \
  --query "${QUERY_FASTA}" \
  --database "${DB_PREFIX}" \
  --output "${RESULT_FILE}" \
  --outfmt '6 qseqid sseqid pident length qstart qend sstart send evalue bitscore' \
  --blast-args -evalue 1e-10 -max_target_seqs 20 -num_threads 4
```

若 BLAST+ 位于共享环境，在该环境中运行此脚本，例如：

```bash
potato-bio-run comparative-core python3 \
  "${SKILL_DIR}/scripts/run_ncbi_blast.py" \
  --program blastn \
  --query "${QUERY_FASTA}" \
  --database "${DB_PREFIX}" \
  --output "${RESULT_FILE}"
```

不指定 `--outfmt` 时保留 BLAST 默认 pairwise 格式。额外参数只放搜索参数，不重复传入脚本已经管理的 `-query`、`-db`、`-out` 和 `-outfmt`。

## 结果检查与交付

BLAST 结果通过 `-out` 直接写入 `${RESULT_FILE}`，不要把完整结果写到标准输出或整体读入对话上下文。

- 确认 BLAST 退出状态和输出文件是否符合所选格式；表格格式的零字节文件可表示无命中。
- 使用有上限的命令抽查结果，不使用 `cat` 或无界打印大文件。
- 表格结果可统计命中数并只查看前几条记录；pairwise 结果优先抽取 query、subject、score、identity 和坐标行。
- 根据抽查结果总结主要命中、identity、覆盖范围、E-value、异常或无命中情况。

用户未特别说明时，交付默认 pairwise 比对结果，并返回该结果文件的完整绝对路径。用户指定其他格式时，同样返回实际结果文件的绝对路径。最终回复还应说明使用的 BLAST 程序、数据库前缀、关键参数和结果摘要。不要把尚未完成的 Slurm 作业报告为已完成。
