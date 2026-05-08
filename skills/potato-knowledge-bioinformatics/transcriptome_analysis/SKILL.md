---
name: transcriptome_analysis
description: 植物转录组全流程分析，基于 Snakemake 流程框架，从原始 fastq 测序数据完成质控、比对、定量到差异表达基因筛选的完整分析。支持断点续传、并行执行、自动跳过已完成步骤。使用场景：RNA-seq 数据分析、差异表达基因鉴定、转录组水平的比较分析等需求。
---
# 转录组分析技能（Snakemake 版）

基于 Snakemake 的 RNA-seq 全流程分析，支持断点续传和并行执行。

## 0. 工作模式

**核心思路**：智能体收集参数 → 在任务目录生成流程文件 → 向用户展示核心参数与关键代码供检查 → 用户确认无误后由智能体提交运行

```
任务目录结构：
/work/{jobID}/
├── Snakefile          # Snakemake 流程文件（核心）
├── config.yaml        # 参数配置
├── groups.txt         # 分组信息
├── prepDE.py3         # count 矩阵生成脚本
├── run_deseq2.R       # DESeq2 分析脚本
└── readme.md          # 说明文档
```

---

## 1. 执行流程

### 步骤 1：收集参数

向用户确认以下信息：

| 参数 | 说明 | 示例 |
|------|------|------|
| INPUT_DIR | 原始数据目录 | `/data/rna_seq/fastq` |
| GENOME_FASTA | 参考基因组 FASTA 文件 | `/data/reference/genome.fasta` |
| GFF_FILE | 基因注释文件 | `/data/reference/genes.gff3` |
| HISAT2_INDEX_PREFIX | hisat2 索引前缀（若不提供则自动构建） | `01-hisat2/index/genome` |
| THREADS | 线程数 | `8` |
| READ_LENGTH | read 长度 | `150` |
| FASTP_TIMEOUT | fastp 单样本超时时间，防止假死 | `3h` |
| FASTP_KILL_AFTER | fastp 超时后强杀等待时间 | `5m` |
| SAMPLES | 样本列表 | `["CK1", "CK2", "Treat1", "Treat2"]` |
| CONTROL_GROUP | 对照组名 | `CK` |
| TREAT_GROUP | 处理组名 | `Treat` |

**数据命名要求**：`{样本名}_R1.fq.gz` 和 `{样本名}_R2.fq.gz`

**混合文库注意事项**：若输入目录同时包含 single-end 与 paired-end 数据，或 FASTQ 位于 ENA/SRA 风格的按 run 分子目录中（如 `SRRxxxx/SRRxxxx.fastq.gz`、`SRRxxxx/SRRxxxx_1.fastq.gz`、`SRRxxxx/SRRxxxx_2.fastq.gz`），必须先生成 manifest（至少包含 `run/sample/layout/r1/r2` 列）再驱动流程；不要强行假设所有样本都满足统一的 `_R1/_R2` 命名规则。**单文件 run 的判定应以每个 run 目录内 FASTQ 文件数为准，而不是仅看 FASTQ header 中的 `/1` 或 `/2` 标记；若一个 run 只有 1 个 FASTQ 文件，即使 header 显示 `@... 1/1` 或 `@... 1/2`，也不能可靠拆分回双端，应按单端处理。对于本地只缺失一个 mate 的“半个 paired-end run”，不要仅凭 manifest 的 paired 标记继续按 PE 跑；应先核对实际文件数，再决定是转为 single-end、排除，还是补下载缺失 mate。**

**run 级命名建议**：若同一 biological sample 对应多个 ENA/SRA runs，不要直接用 `sample_name` 作为全部中间文件和结果目录名，否则不同 runs 会互相覆盖。应在 manifest 中增加唯一 `sample_id`（推荐格式 `{sample_name}__{run}`），并以 `sample_id` 作为 fastp/BAM/StringTie 输出前缀；最终 TPM/count 矩阵的列名也应使用该唯一 `sample_id`。如果后续确需合并 technical runs，应在定量完成后单独设计 merge 规则，而不是在原始 run 级流程中直接覆盖。

**补下载后重新纳入原则**：若执行计划中曾因缺失 mate 暂时排除部分 paired-end runs，而用户说明缺失 FASTQ 已重新下载，应重新扫描实际 FASTQ 文件与元数据（如 `data_list*.txt`、`logs_ena_fastq/ena_fastq_manifest.tsv`），用“R1 与 R2 均实际存在”作为纳入标准重新生成 manifest；不要沿用旧计划中的排除清单。生成后必须验证：manifest 行数等于完整 PE run 数、`sample_id` 唯一、`r1/r2` 路径缺失数为 0，并明确列出此前异常 runs 已纳入。

---

### 步骤 2：检查依赖

```bash
fastp --version
hisat2 --version
samtools --version
stringtie --version
snakemake --version
python3 --version
R --version
R -q -e "if(require('DESeq2', quietly=TRUE)) cat('OK\n') else quit(status=1)"
```

**若系统没有 conda / mamba，但有 micromamba**，可直接用 micromamba 建环境，不要因为缺少 conda 命令而阻塞。若 `command -v micromamba` 为空，也不要立刻判定系统无 micromamba；应进一步检查常见固定安装路径（例如本机已验证的 `/opt/micromamba/bin/micromamba`）或 `/etc/profile.d/micromamba.sh`。一个已验证可用的最小环境创建命令是：

```bash
/opt/micromamba/bin/micromamba create -y -p /path/to/env -c conda-forge -c bioconda \
  python=3.11 snakemake fastp hisat2 samtools stringtie
```

创建后可用以下方式执行命令而不必先激活：

```bash
micromamba run -p /path/to/env snakemake --version
micromamba run -p /path/to/env fastp --version
micromamba run -p /path/to/env hisat2 --version
micromamba run -p /path/to/env samtools --version
micromamba run -p /path/to/env stringtie --version
```

**FASTQ 完整性预检（新增建议）**：如果 `fastp` 在后台长时间无进展、日志只停留在 `Detecting adapter sequence...`，或日志出现 `ERROR: igzip: encountered while decompressing file`、`ERROR: sequence and quality have different length`，优先怀疑**输入 FASTQ / gzip 文件损坏或截断**，不要先把问题归咎于 fastp 安装本身。建议先对可疑输入做：

```bash
gzip -t sample.fastq.gz
zcat sample.fastq.gz | head -8
```

必要时可先对单个样本做小规模 smoke test；若 `gzip -t` 失败，通常应重新下载原始 FASTQ，而不是仅重装 fastp。已验证在本机上，即使更换到另一版 fastp，损坏输入仍可能表现为卡住或超时，因此该类问题的根因通常在输入数据。

**fastp 卡死/假死防护（Snakemake 规则级别）**：若遇到 fastp 进程长期存活、clean FASTQ 可能已写出但 `.json/.html` 未生成、日志为空或不再更新的情况，应在生成的 `Snakefile` 的 `rule fastp` shell 命令中给 fastp 加 GNU `timeout` 包装，而不是只依赖人工发现后 kill。推荐在配置中提供 `fastp_timeout`（默认 `3h`）与 `fastp_kill_after`（默认 `5m`），并在 fastp 命令前写：

```bash
timeout --kill-after={params.kill_after} {params.timeout} fastp \
  -w {threads} \
  -i "{input.r1}" \
  -I "{input.r2}" \
  -o "{output.r1}" \
  -O "{output.r2}" \
  -h "{output.html}" \
  -j "{output.json}" \
  {params.extra} \
  > "{log}" 2>&1
```

说明：正常样本不会受影响；若单个 fastp 超过设定时间，Snakemake 会把该 rule 判定为失败并清理可能不完整的输出，后续可断点重跑该样本，避免一个假死进程长期占用后台任务。若同一数据重跑后快速完成，通常提示是 fastp/多线程/I/O 偶发卡死，而不是持续性 FASTQ 损坏；但仍应先对原始 FASTQ 做 `gzip -t` 或 MD5 复核。

**不要把 fastp 输出存在当作原始 FASTQ 完整性的证据**：已验证 `fastp 1.3.3` 在某些 gzip 尾部 CRC/length 错误或 FASTQ 尾部异常时，可能在日志中写出 `ERROR: sequence and quality have different length:`、`Your FASTQ may be invalid, please check the tail of your FASTQ file`、`WARNING: different read numbers ...`、`Ignore the unmatched reads`，但仍以成功退出码结束并生成 `.clean.fastq.gz`、`.json`、`.html`，从而被 Snakemake 判定为完成。遇到“gzip 检查失败但 fastp 已完成”的矛盾时，应以 `gzip -t`/CRC 结果和 fastp 日志中的 invalid FASTQ 警告为准；这些 fastp 结果不应直接进入 HISAT2/StringTie。建议交叉核对：

```bash
# 1) 从 gzip_integrity_failed.tsv 取 sample_id/run/mate
# 2) 检查对应 fastp 日志是否含异常而非只看输出文件是否存在
grep -E 'ERROR:|Your FASTQ may be invalid|different read numbers|Ignore the unmatched reads' logs/fastp/{sample_id}.log
# 3) 修复策略：重下载损坏 FASTQ 后，删除或强制重跑受影响 sample_id 的 fastp 输出
```

注意：文件大小等于 ENA manifest `bytes` 也不能完全证明 gzip 内容正确；CRC/ISIZE 不一致时，`gzip -t` 仍会失败，应重新下载。

**大批量 FASTQ gzip 完整性检测（Slurm 后台模式）**：当用户要求对全部 FASTQ 做 gzip 完整性检测且“不要长时间占用前台窗口”时，应生成独立检测目录并提交 Slurm 后台任务，而不是在前台循环 `gzip -t`。推荐布局：

```text
paired_end/00-gzip-check/
├── scripts/
│   ├── check_fastq_gzip_integrity.py
│   ├── run_gzip_integrity_check.sh
│   └── gzip_integrity_check.slurm.sh
├── logs/
└── latest -> results_YYYYMMDD_HHMMSS
```

实现要点：
1. 以 paired-end manifest（如 `samples.manifest.tsv`，包含 `run/sample_id/r1/r2`）为准展开 R1/R2 文件列表；不要重新扫描目录猜测样本。
2. Python 检测脚本用 `ProcessPoolExecutor` 并发调用 `gzip -t`（或等价 gzip 流读取），输出：`gzip_integrity_results.tsv`、`gzip_integrity_failed.tsv`、`gzip_integrity_summary.json`、`gzip_integrity_progress.log`，全部通过时写 `gzip_integrity.ok`，有失败时写 `gzip_integrity.failed` 并以非零退出。
3. 提交前只做轻量验证：`python3 -m py_compile`、`bash -n`、以及 `--limit 1` 的单文件 smoke test；不要在前台完整检测全部 FASTQ。
4. Slurm wrapper 建议按数据量设置如 `--cpus-per-task=16`、`--mem=16G`、`--time=08:00:00`，用 `slurm-user-jobs/scripts/submit-job.sh --script ...` 提交，并立即用 `list-jobs.sh` / `job-status.sh JOBID` 确认进入 RUNNING/PENDING。
5. 向用户汇报时给出 Job ID、检测文件总数、`latest/gzip_integrity_progress.log`、`logs/slurm-JOBID.out/.err`、完成后结果文件路径；不要声称检测已完成，除非已经读取 summary/marker 验证。


---

### 步骤 3：生成流程文件

#### 3.1 创建任务目录

```bash
mkdir -p /work/{jobID}
```

#### 3.2 生成 Snakefile（核心）

**这是最关键的文件，定义了整个分析流程的运行代码。**

Snakefile 包含以下规则：

| 规则 | 功能 | 输入 | 输出 | 参数参考 |
|------|------|------|------|----------|
| `hisat2_index` | 构建 HISAT2 索引 | genome_fasta | .ht2 索引文件 | `references/02-hisat2.md` |
| `fastp` | 质量控制 | R1/R2 fastq | 质控后 fastq + 报告 | `references/01-fastp.md` |
| `hisat2_align` | 比对 | 质控后 fastq + 索引 | 排序 BAM | `references/02-hisat2.md` |
| `stringtie_quant` | 定量 | BAM + GFF | GTF + 丰度表 | `references/03-stringtie.md` |
| `prep_count_matrix` | 生成矩阵 | 各样本 GTF | count 矩阵 CSV | `references/04-prepDE.md` |
| `deseq2_analysis` | 差异分析 | count 矩阵 + 分组 | 结果 + 图表 | `references/05-deseq2.md` |

**完整 Snakefile 模板**：`/skills/transcriptome_analysis/scripts/Snakefile`

**要求**：优先基于 `scripts/Snakefile` 模板填充生成，不要从零自由生成。

**若技能目录中未实际提供 `scripts/` 模板与 `references/` 文档文件**（例如本地仅安装了 `SKILL.md`），则应：
1. 明确记录模板资产缺失；
2. 依据本技能中列出的规则、输入输出约定和用户当前数据布局，生成**自包含**的 Snakefile / config / 辅助脚本；
3. 在 readme.md 中写明这是基于技能规范生成的 fallback workflow；
4. 不要因为模板缺失而阻塞任务。

**智能体生成 Snakefile 时必须参考 `references/` 目录下的参数文档；若这些参考文件缺失，则以主技能文档中的参数约定为准，并在 readme.md 记录该情况。**

---

#### 3.3 生成 config.yaml

```yaml
input_dir: "/data/rna_seq/fastq"
genome_fasta: "/data/reference/genome.fasta"
hisat2_index_prefix: "01-hisat2/index/genome"
gff_file: "/data/reference/genes.gff3"
threads: 8
read_length: 150
fastp_extra: ""
fastp_timeout: "3h"
fastp_kill_after: "5m"
samples:
  - CK1
  - CK2
  - Treat1
  - Treat2
groups:
  control: "CK"
  treatment: "Treat"
```

**说明**：
- `genome_fasta`：参考基因组 FASTA 文件，用于构建索引
- `hisat2_index_prefix`：索引存放路径（不含 .ht2 后缀）；若索引已存在，可直接指向现有索引前缀
- `fastp_timeout` / `fastp_kill_after`：用于在 `rule fastp` 中包装 GNU `timeout`，防止 fastp 偶发假死长期占用后台任务；默认 `3h` 后终止，额外等待 `5m` 后强杀。

---

#### 3.4 生成 groups.txt

**注意**：prepDE.py3 会自动处理样本名中的 `_stringtie` 后缀，groups.txt 中直接使用原始样本名即可。

```
sample_name	group
CK1	CK
CK2	CK
Treat1	Treat
Treat2	Treat
```

---

#### 3.5 生成 prepDE.py3

参考：`/skills/transcriptome_analysis/scripts/references/04-prepDE.md`

可直接复制：
```bash
cp /skills/transcriptome_analysis/scripts/prepDE.py3 /work/{jobID}/
```

---

#### 3.6 生成 run_deseq2.R

参考：`/skills/transcriptome_analysis/scripts/references/05-deseq2.md`

核心内容（需根据实际分组修改）：
```r
CONTROL_GROUP <- "CK"      # 对照组名称
TREAT_GROUP <- "Treat"     # 处理组名称
```

模板：`/skills/transcriptome_analysis/scripts/run_deseq2.R`

---

#### 3.7 生成 readme.md

记录：
- 分析参数
- 运行命令
- 输出文件说明

---

### 步骤 4：用户检查与执行前确认

向用户展示生成的文件、核心参数、关键代码片段和运行计划，等待用户确认：

```
📋 流程文件已生成：

📁 任务目录: /work/{jobID}/
📄 输入数据: /data/rna_seq/fastq (4 个样本)
🧬 参考基因组 FASTA: /data/reference/genome.fasta
🗂 HISAT2 索引前缀: 01-hisat2/index/genome
📝 注释文件: /data/reference/genes.gff3
⚡ 线程数: 8

📊 分组信息:
  - 对照组 (CK): CK1, CK2
  - 处理组 (Treat): Treat1, Treat2

📦 已生成文件:
  - Snakefile (流程定义)
  - config.yaml (参数配置)
  - groups.txt (分组信息)
  - prepDE.py3 (count 矩阵脚本)
  - run_deseq2.R (差异分析脚本)
  - readme.md (说明文档)

🔎 请用户重点检查：
  - config.yaml 中的输入目录、参考基因组、注释文件、线程数、样本名、分组名
  - Snakefile 中的 rule all、hisat2_index、fastp、hisat2_align、stringtie_quant、prep_count_matrix、deseq2_analysis
  - run_deseq2.R 中的 CONTROL_GROUP 与 TREAT_GROUP

▶️ 计划执行命令:
  cd /work/{jobID}
  snakemake -n
  export XDG_CACHE_HOME=/tmp/snakemake_cache
  mkdir -p $XDG_CACHE_HOME
  nohup snakemake -j {threads} >pipeline.log 2>&1 &
  disown

**要求**：`snakemake -j {threads}` 中的线程数必须与 `config.yaml` 中的 `threads` 保持一致。
```

**要求**：智能体必须在此步骤向用户展示核心参数与关键代码，并明确询问“确认无误后是否提交运行？”，只有在用户明确同意后才能执行。

---

### 步骤 5：提交运行

用户确认无误后，由智能体按以下方式提交运行。

```bash
# 进入任务目录
cd /work/{jobID}

# 干跑检查
snakemake -n

# 设置缓存目录（可选但推荐）
export XDG_CACHE_HOME=/tmp/snakemake_cache
mkdir -p $XDG_CACHE_HOME

# 后台提交并脱离当前 shell / OpenClaw exec 会话
nohup snakemake -j {threads} >pipeline.log 2>&1 &
disown
```

**要求**：若 `snakemake -n` 报错，必须先修复流程文件后再提交运行。

**Slurm 资源提交建议**：如果用户明确给出 CPU/内存配额，或本机任务适合走 Slurm，应同时生成但不要擅自提交：
1. 一个实际运行脚本（例如 `run_snakemake_80cpu_50g.sh`），内部设置 `XDG_CACHE_HOME`、进入工作目录，并用 micromamba 环境执行 `snakemake --cores {CPU} --resources mem_mb={MEM_MB} --rerun-incomplete --printshellcmds`，主日志写入 `pipeline.log`；
2. 一个 Slurm 脚本（例如 `submit_snakemake_80cpu_50g.slurm.sh`），设置 `--cpus-per-task`、`--mem`、`--time`、`--chdir`、`--output/--error`，调用运行脚本。
**要求**：若 `snakemake -n` 报错，必须先修复流程文件后再提交运行。

**Snakemake 版本兼容注意**：在本机已验证 Snakemake 9.20.0 不支持旧版常见参数 `--reason`，正式运行脚本中不要使用该参数，否则任务会打印 usage 后立即退出。需要详细日志时可使用 `--printshellcmds --show-failed-logs`；Snakemake 9.x 的日志通常仍会输出 rule 的 reason 信息。

**修复 FASTQ 后重跑原则**：若原始 FASTQ 曾损坏并已补下载/替换，正式重跑前应删除所有受影响的旧 fastp 输出；如果不容易精确定位受影响样本，用户要求“重新运行整套分析”时可删除整个 `01-fastp/` 及 `logs/fastp/` 后重建空目录，避免 Snakemake 复用由损坏 FASTQ 产生的 clean FASTQ。若存在旧坏文件备份（如 `fastq_ena/*/*.fastq.gz.bad.*`），用户明确要求删除时可先写入删除清单到 `logs/cleanup_before_rerun_TIMESTAMP/`，再删除备份文件并核查残留为 0。删除后必须重新检查 manifest 行数、`sample_id` 唯一性、R1/R2 路径缺失数，以及 `snakemake -n`。

**Snakemake lock 处理**：Slurm 提交后若任务很快离队，需检查 `logs/slurm-*.out/.err` 和 `pipeline.latest.log`。若出现 `LockException: Directory cannot be locked`，不要直接反复提交；先用 Slurm 队列和 `pgrep -af 'snakemake|Snakefile|<job-name>'` 确认没有活跃 Snakemake/同目录 Slurm job，再在工作目录执行 `snakemake --unlock`（同一 micromamba 环境），随后重新 dry-run，通过后再提交。历史 Slurm accounting disabled 时，任务离队后只能靠 stdout/stderr 与结果文件判断。

**断点续传**：中断后重新运行 `snakemake -j {threads}`，自动跳过已完成步骤。

**提交后反馈**：智能体应向用户返回完整提交命令、日志位置（`pipeline.log`）以及查看方式。

---

## 2. 参数参考文档

生成代码时必须参考以下文档：

| 文档 | 内容 | 路径 |
|------|------|------|
| 01-fastp.md | fastp 参数说明、命令模板 | `scripts/references/01-fastp.md` |
| 02-hisat2.md | hisat2 + samtools 参数 | `scripts/references/02-hisat2.md` |
| 03-stringtie.md | stringtie 参数 | `scripts/references/03-stringtie.md` |
| 04-prepDE.md | prepDE.py3 参数 | `scripts/references/04-prepDE.md` |
| 05-deseq2.md | DESeq2 参数、R 代码 | `scripts/references/05-deseq2.md` |

---

## 3. 流程输出

```
任务目录/
├── 00-fastp/                    # 质控结果
│   ├── *_R{1,2}.fq.gz          # 质控后数据
│   ├── *.html/*.json           # fastp 报告
├── 01-hisat2/                   # 比对结果
│   └── *_hisat.sort.bam        # 排序 BAM + 索引
├── 02-stringtie/                # 定量结果
│   ├── *_stringtie/            # 每个样本的 GTF 和丰度
│   ├── gene_count_matrix.csv   # 基因 count 矩阵
│   └── transcript_count_matrix.csv
└── 03-deseq2/                   # 差异分析
    ├── deseq2_result.xls       # 差异分析结果
    ├── MA_plot.pdf
    ├── volcano_plot.pdf
    └── pca_plot.pdf
```

---

## 4. 内置脚本与模板

| 文件 | 说明 |
|------|------|
| `scripts/Snakefile` | Snakemake 流程模板 |
| `scripts/config.yaml` | 配置文件模板 |
| `scripts/run_deseq2.R` | DESeq2 脚本模板 |
| `scripts/prepDE.py3` | count 矩阵生成脚本 |

---

## 5. 智能体执行规范

### 必须
- ✅ 先收集参数再生成文件
- ✅ 检查软件依赖
- ✅ **优先基于 `scripts/Snakefile` 模板，并参考 `references/` 文档生成 Snakefile**
- ✅ 展示核心参数、关键代码和执行计划并等待用户确认
- ✅ 展示拟执行的完整命令
- ✅ `snakemake -j {threads}` 中的线程数必须与 `config.yaml` 一致
- ✅ 若 `snakemake -n` 报错，必须先修复流程文件后再提交运行
- ✅ 用户确认后，优先使用 `nohup + disown` 提交长任务
- ✅ 提交后告知日志位置和查看方法
- ✅ 在 readme.md 记录参数、步骤、提交命令和运行日志位置

### 禁止
- ❌ 未确认参数就生成文件
- ❌ 跳过依赖检查
- ❌ 未经用户确认直接运行 snakemake
- ❌ 在生成的任务文件中使用绝对路径硬编码技能目录

---

## 6. 升级日志

**v2.5 (2026-03-18)**
- 明确 Snakefile 应优先基于模板生成，减少自由生成带来的不稳定性
- 明确 `snakemake -j {threads}` 必须与 `config.yaml` 中线程数一致
- 明确 `snakemake -n` 报错时不得继续正式提交

**v2.4 (2026-03-18)**
- 更新正式运行提交方式为 `nohup ... &` + `disown`
- 要求展示完整命令，并在用户确认后提交运行
- 明确提交后需反馈日志位置与查看方式

**v2.3 (2026-03-18)**
- 调整执行规则：由“用户运行 Snakemake”改为“用户检查确认后由智能体提交运行”
- 在执行前确认步骤中新增“展示核心参数与关键代码”的要求
- 明确提交后需反馈运行命令、日志位置与查看方式

**v2.2 (2026-03-17)**
- 新增 `hisat2_index` 规则，支持自动构建索引
- 新增参数 `genome_fasta`、`hisat2_index_prefix`
- 简化 SKILL.md 文档：移除冗余代码示例，改为规则说明表
- 修正文档与实际 Snakefile 的不一致

**v2.1 (2026-03-16)**
- 添加 references/ 参数参考文档
- 在步骤 3 中明确 Snakefile 生成规则和参数参考
- 强调软件运行代码生成作为核心任务

**v2.0 (2026-03-16)**
- 从逐步执行升级为 Snakemake 流程
- 支持断点续传
- 增加可视化输出
