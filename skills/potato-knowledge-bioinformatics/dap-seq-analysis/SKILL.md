---
name: dap-seq-analysis
description: DAP-Seq / ChIP-like paired-end Snakemake workflow for potato or plant TF binding data. Use when the user asks to perform DAP-Seq analysis; validates required references and samples, prepares config-driven Snakemake workflow, checks/installs software with micromamba/mamba/conda, and submits confirmed runs to Slurm.
version: 1.0.0
metadata:
  hermes:
    tags: [DAP-Seq, ChIP-seq, peak-calling, MACS2, ChIPseeker, HOMER, Snakemake, Slurm, potato, bioinformatics]
    related_skills: [slurm-for-long-running-tasks, ena-fastq-download]
---

# DAP-Seq Analysis Skill

## 1. 触发条件

当用户要求做 DAP-Seq、DAP-seq、DNA affinity purification sequencing、TF binding peak calling、DAP-Seq motif 分析，或要求把 DAP-Seq 数据从 FASTQ 分析到 peak 注释 / motif 富集时，使用本技能。

典型请求：

- “帮我做 DAP-Seq 分析”
- “这些 DAP-Seq 样品跑一下 peak calling 和 motif 分析”
- “根据实验组和 input 对照组做 DAP-Seq 流程”
- “把 DAP-Seq 数据整理成 Snakemake 流程并后台运行”

**默认语言**：用户用中文交流时，任务进度、参数确认、报错解释和最终汇报均使用中文。

---

## 2. 工作原则

1. **必须先确认必需输入**，缺任何必需项都不能擅自运行。
2. **必须使用 Snakemake 控制流程**，参数来自 `config/config.yaml`，样品关系来自 `config/samples.tsv`，流程规则来自 `Snakefile`。
3. **必须先 dry-run**：正式运行前执行 `snakemake -n --printshellcmds`。
4. **必须先向用户汇报并等待确认**：数据、参数、软件环境、输出位置、Slurm 资源确认无误后，才提交后台任务。
5. **长任务必须优先用 Slurm 后台运行**，不要在前台直接跑完整 BWA/MACS2/HOMER 流程。
6. 初始检查不要完整解压或逐行统计大 FASTQ；重度完整性检查只有在用户要求时才做，且应提交后台任务。

---

## 3. 必需输入检查清单

用户要求开始 DAP-Seq 分析时，先检查是否提供以下信息。

| 必需项 | 说明 | 缺失时处理 |
|---|---|---|
| 参考基因组 FASTA | 用于 BWA 比对和索引构建，如 `genome.fa` | 明确告诉用户这是必需项，请提供路径 |
| softmask 基因组 FASTA | 用于 HOMER motif 分析，如 `genome.softmasked.fa` | 明确告诉用户这是必需项，请提供路径 |
| GFF3/GTF 注释 | 用于 ChIPseeker peak 注释 | 明确告诉用户这是必需项，请提供路径 |
| 实验组 FASTQ | DAP/TF/treatment 样品，推荐 paired-end R1/R2 | 明确告诉用户需提供样品名、R1、R2、target/TF 名称、重复编号 |
| 对照组 FASTQ | input/control 样品，推荐 paired-end R1/R2 | 明确告诉用户每个 target 必须有 control_id |
| 结果保存位置 | workflow 工作目录和结果输出目录 | 明确告诉用户需指定；若用户未指定，可建议 `$HOME/work/DAP-Seq.<时间戳>` |
| 资源参数 | CPU、内存、运行时间；如果未给，可给建议值并等待确认 | 例如 16 CPU、64G、24-48 h，视数据量调整 |

### 缺失信息时的标准回复

如果缺少必需输入，不要猜测路径，不要运行。回复格式：

```text
要做 DAP-Seq 分析还缺少以下必需信息：
1. 参考基因组 FASTA：...
2. softmask 基因组 FASTA：...
3. GFF3/GTF 注释：...
4. 实验组 FASTQ：...
5. input/control FASTQ：...
6. 结果保存目录：...

这些信息是流程运行必需项。请补充后我再生成 config、samples.tsv 和 Snakemake 工作目录。
```

### 样品关系要求

使用 `config/samples.tsv` 表达样品关系，至少包含：

```text
sample_id	target_id	replicate	read1	read2	control_id	is_control
input	input	0	/path/input_R1.fq.gz	/path/input_R2.fq.gz	.	yes
ERF1_rep1	ERF1	1	/path/ERF1_rep1_R1.fq.gz	/path/ERF1_rep1_R2.fq.gz	input	no
ERF1_rep2	ERF1	2	/path/ERF1_rep2_R1.fq.gz	/path/ERF1_rep2_R2.fq.gz	input	no
```

规则：

- `sample_id` 必须唯一，用于所有中间文件名。
- `target_id` 用于 MACS2 合并同一个 TF/target 的重复。
- `control_id` 必须指向 `is_control=yes` 的样品。
- 同一 `target_id` 默认应只有一个 control；如果多个 control，必须先询问用户如何处理。
- 多个 technical runs 不要复用同一个 `sample_id`；应使用唯一 ID，必要时下游再合并。
- 当前模板面向 paired-end DAP-Seq；如果用户提供 single-end 数据，需先说明模板需要改造，不能直接按 PE 参数运行。

---

## 4. 软件环境检查与安装

### 4.1 必需命令 / 软件

运行前检查：

```bash
snakemake --version
bwa 2>&1 | head -5
samtools --version
macs2 --version
python3 --version
python3 -c "import yaml; print('PyYAML OK')"
Rscript --version
Rscript -e "suppressPackageStartupMessages({library(ChIPseeker); library(clusterProfiler); library(GenomicFeatures)}); cat('R packages OK\n')"
findMotifsGenome.pl 2>&1 | head -5
```

必需组件：

- Snakemake
- BWA
- samtools
- MACS2
- Python 3 + PyYAML
- Rscript
- R/Bioconductor 包：`ChIPseeker`、`clusterProfiler`、`GenomicFeatures`
- HOMER：`findMotifsGenome.pl`

### 4.2 安装优先级

如果软件缺失，优先使用系统已有环境管理器：

1. `micromamba`
2. `mamba`
3. `conda`

在本服务器，若 `command -v micromamba` 为空，也应检查常见路径：

```bash
/opt/micromamba/bin/micromamba --version
source /etc/profile.d/micromamba.sh
```

### 4.3 推荐环境创建命令

推荐在任务目录或用户指定位置创建独立环境。MACS2 优先通过 `pip` 在环境内源码安装，避免预编译包兼容性问题：

```bash
/opt/micromamba/bin/micromamba create -y \
  -p /path/to/DAP-Seq.<job_id>/envs/dapseq \
  -c conda-forge -c bioconda \
  python=3.11 pip pyyaml snakemake bwa samtools \
  r-base bioconductor-chipseeker bioconductor-clusterprofiler bioconductor-genomicfeatures bioconductor-txdbmaker \
  homer

/opt/micromamba/bin/micromamba run -p /path/to/DAP-Seq.<job_id>/envs/dapseq \
  pip install --no-cache-dir macs2
```

运行时优先使用：

```bash
/opt/micromamba/bin/micromamba run -p /path/to/env snakemake --version
/opt/micromamba/bin/micromamba run -p /path/to/env Rscript --version
```

如果 `homer` 在 conda 环境中安装失败，需明确说明并单独安装/配置 HOMER；不要在没有 `findMotifsGenome.pl` 的情况下提交正式流程。

---

## 5. 工作目录生成

### 5.1 推荐目录结构

```text
DAP-Seq.<job_id>/
├── Snakefile
├── README.md
├── config/
│   ├── config.yaml
│   └── samples.tsv
├── scripts/
│   ├── 00_check_inputs.py
│   ├── 00_bwa_index.sh
│   ├── 01_map_sample.sh
│   ├── 02_call_peaks.sh
│   ├── 03_chipseeker_annotate.R
│   ├── 04_prepare_homer_bed.py
│   ├── 05_homer_motif.sh
│   ├── 06_sum_chipseeker.py
│   ├── run_chipseeker_summary.sh
│   └── run_all.sh
├── envs/
└── results/
```

### 5.2 模板来源

本技能包含可复制模板：

- `templates/Snakefile`
- `templates/config.yaml`
- `templates/samples.tsv`
- `scripts/00_check_inputs.py`
- `scripts/00_bwa_index.sh`
- `scripts/01_map_sample.sh`
- `scripts/02_call_peaks.sh`
- `scripts/03_chipseeker_annotate.R`
- `scripts/04_prepare_homer_bed.py`
- `scripts/05_homer_motif.sh`
- `scripts/06_sum_chipseeker.py`
- `scripts/run_chipseeker_summary.sh`
- `scripts/run_all.sh`

生成新任务时：

1. 创建任务目录。
2. 复制 `templates/Snakefile` 到任务目录 `Snakefile`。
3. 复制 `templates/config.yaml` 到 `config/config.yaml` 并替换参数。
4. 根据用户样品生成 `config/samples.tsv`。
5. 复制 `scripts/` 下所有脚本到任务目录 `scripts/`。
6. 写入任务 `README.md`，记录参数、运行命令、Slurm 提交方式和日志位置。

---

## 6. Snakemake 流程规则

| Rule | 功能 | 主要输入 | 主要输出 |
|---|---|---|---|
| `check_inputs` | 检查 config、samples、FASTQ、genome、GFF、softmask genome | `config.yaml`, `samples.tsv` | `results/00-qc/input_check.ok` |
| `bwa_index` | 构建 BWA index | `genome_fasta` | `<bwa_index_prefix>.{amb,ann,bwt,pac,sa}` |
| `map_sample` | 每个样品 BWA 比对、排序、去重复、过滤 unmapped | R1/R2 FASTQ + BWA index | `results/01-mapping/<sample_id>.flt.bam(.bai)` |
| `call_peaks` | 每个 target 使用 MACS2 call peaks | treatment BAMs + control BAM | `results/02-callpeaks/<target>/<target>_peaks.narrowPeak`, `*_summits.bed` |
| `chipseeker_annotate` | ChIPseeker 注释 peak，默认 TSS 上游 5 kb | narrowPeak + GFF3/GTF | `results/03-chipseeker/<target>.anno.with_intergenic.txt` |
| `sum_chipseeker` | ChIPseeker 后处理：去除 Distal Intergenic、按 transcriptId 合并注释类型、统计 peak feature 分布 | `*.anno.with_intergenic.txt`, `config.yaml` | `*.anno.no_distal_intergenic.txt`, `*.anno.no_distal_intergenic.merge_by_transcript.tsv`, `<target>.peaks_sum.txt` |
| `prepare_homer_bed` | 从 summit 生成 summit ±100 bp BED | `*_summits.bed` | `results/04-homer/<target>_peaks.bed` |
| `homer_motif` | HOMER motif enrichment | summit BED + softmask genome | `results/04-homer/<target>/homerResults.html` |

核心流程逻辑：

```text
FASTQ
  -> bwa index / bwa mem
  -> samtools sort
  -> samtools rmdup 或后续 markdup 替代
  -> samtools view -F 4
  -> macs2 callpeak -f BAMPE --call-summits -B
  -> ChIPseeker annotatePeak(tssRegion=c(-5000, 0))
  -> MACS2 summit +/- 100 bp BED
  -> HOMER findMotifsGenome.pl
```

---

## 7. 参数配置参考

`config/config.yaml` 使用以下字段：

```yaml
samples_tsv: "config/samples.tsv"
config_file: "config/config.yaml"
script_dir: "scripts"
genome_fasta: "/path/to/genome.fa"
bwa_index_prefix: "results/reference/bwa/genome"
gff_file: "/path/to/annotation.gff3"
annotation_table: ""
outdir: "results"
threads: 15
macs2:
  genome_size: "8e+8"
  format: "BAMPE"
  call_summits: true
  bdg: true
  extra: ""
chipseeker:
  tss_upstream: 5000
  tss_downstream: 0
  rscript: "Rscript"
  drop_annotation: "Distal Intergenic"
  keep_count: false
homer:
  summit_flank: 100
  genome_for_homer: "/path/to/genome.softmasked.fa"
  extra: "-mask"
dedup_method: "rmdup"
```

说明：

- `genome_fasta`：BWA 比对使用的参考基因组。
- `homer.genome_for_homer`：HOMER motif 使用的 softmask 基因组；用户必须提供。
- `gff_file`：ChIPseeker 注释使用的 GFF3/GTF。
- `macs2.genome_size`：马铃薯示例中可用 `8e+8`，其它物种需按基因组大小调整。
- `chipseeker.tss_upstream: 5000` 与 `tss_downstream: 0` 对应 promoter 上游 5 kb。
- `dedup_method: rmdup` 兼容原始示例，但 `samtools rmdup` 已较旧；若不可用，应改为 `fixmate/markdup -r` 并记录。实测 conda-forge 的 samtools 1.23.1 中 `rmdup` 仍可用（deprecated 但功能正常），但仍建议先验证。
- **⚠️ 注意 `outdir` 与 `bwa_index_prefix` 的耦合**：当修改 `outdir` 时，必须同步更新 `bwa_index_prefix` 中的路径前缀（如 `results/` → `res.202605091230/`），否则 BWA 索引会写入错误目录。

---

## 8. 执行前验证

在任务目录中执行：

```bash
python3 -m py_compile scripts/00_check_inputs.py scripts/04_prepare_homer_bed.py scripts/06_sum_chipseeker.py
bash -n scripts/00_bwa_index.sh
bash -n scripts/01_map_sample.sh
bash -n scripts/02_call_peaks.sh
bash -n scripts/05_homer_motif.sh
bash -n scripts/run_chipseeker_summary.sh
bash -n scripts/run_all.sh
snakemake -s Snakefile --configfile config/config.yaml -n --printshellcmds
```

如果使用 micromamba 环境：

```bash
/opt/micromamba/bin/micromamba run -p envs/dapseq \
  snakemake -s Snakefile --configfile config/config.yaml -n --printshellcmds
```

**如果 dry-run 报错，必须先修复，不得提交 Slurm。**

---

## 9. 执行前向用户确认

dry-run 和环境检查通过后，必须向用户汇报并等待确认。汇报内容至少包括：

```text
DAP-Seq 流程已准备好，请确认以下信息：

工作目录：...
结果目录：...
参考基因组：...
softmask 基因组：...
GFF3/GTF 注释：...
样品数：...
实验组/target：...
对照组/input：...
MACS2 genome size：...
ChIPseeker TSS 窗口：-5000..0
HOMER summit flank：100 bp
软件环境：...
Snakemake dry-run：已通过
计划 Slurm 资源：CPU=..., MEM=..., TIME=...
日志文件：...

确认无误后，我将使用 Slurm 在后台提交运行。是否确认提交？
```

只有用户明确确认后才能提交。

---

## 10. Slurm 后台提交

用户确认后，使用 `slurm-for-long-running-tasks` 技能中的 wrapper 脚本提交。优先写一个运行脚本，再提交该脚本，避免复杂命令 quoting 问题。

### 10.1 运行脚本模板

在任务目录写入 `run_snakemake.slurm.payload.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /path/to/DAP-Seq.<job_id>
export XDG_CACHE_HOME="$PWD/.cache"
mkdir -p "$XDG_CACHE_HOME" logs

/opt/micromamba/bin/micromamba run -p envs/dapseq \
  snakemake -s Snakefile \
    --configfile config/config.yaml \
    --cores <CPUS> \
    --rerun-incomplete \
    --printshellcmds \
    --show-failed-logs \
    > logs/pipeline.log 2>&1
```

如果不使用 micromamba 环境，则将命令替换为环境中可用的 `snakemake`。

### 10.2 提交命令

使用 slurm 技能的 `submit-job.sh`，例如：

```bash
bash /mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks/scripts/submit-job.sh \
  --job-name dapseq_<job_id> \
  --cpus <CPUS> \
  --mem-gb <MEM_GB> \
  --time <HH:MM:SS> \
  --workdir /path/to/DAP-Seq.<job_id> \
  --output logs/slurm-%j.out \
  --error logs/slurm-%j.err \
  --script /path/to/DAP-Seq.<job_id>/run_snakemake.slurm.payload.sh
```

**⚠️ Slurm 提交注意事项**（实操验证过的坑）：
- `submit-job.sh` 的标志是 `--output` 和 `--error`（**不是** `--stdout` / `--stderr`），写错会报 "unknown argument"
- `--script` 必须传**绝对路径**（`submit-job.sh` 内部用 `abs_path` 解析相对路径，不以 `--workdir` 为基准），相对路径会报 "script does not exist"

提交后立即检查：

```bash
bash /mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks/scripts/list-jobs.sh
bash /mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/slurm-for-long-running-tasks/scripts/job-status.sh <JOBID>
```

向用户返回：

- Slurm Job ID
- 工作目录
- `logs/pipeline.log`
- `logs/slurm-<JOBID>.out`
- `logs/slurm-<JOBID>.err`
- 查看状态命令

---

## 11. 常见问题与处理

### 缺少 Rscript 或 ChIPseeker 包

不要跳过 `chipseeker_annotate`。安装 R 与 Bioconductor 包后再运行：

```bash
micromamba install -p envs/dapseq -c conda-forge -c bioconda \
  r-base bioconductor-chipseeker bioconductor-clusterprofiler bioconductor-genomicfeatures
```

### 缺少 HOMER

不要提交 `homer_motif` 规则。先安装或配置 `findMotifsGenome.pl`：

```bash
micromamba install -p envs/dapseq -c conda-forge -c bioconda homer
```

### `samtools rmdup` 不可用

`rmdup` 是原始示例逻辑，但在新版本 samtools 中可能不可用。应改为 `fixmate`/`markdup -r` 流程，并更新 `01_map_sample.sh` 与 README，不能忽略去重复步骤。

### 用户只要求写流程/技能

只生成模板、做语法检查和 Snakemake DAG dry-run。不要运行 BWA/MACS2/ChIPseeker/HOMER。

### 用户未提供 softmask genome

HOMER motif 分析需要 softmask genome 或 HOMER 支持的 genome key。本技能要求用户提供 softmask 基因组；缺失时必须询问，不要用普通 genome 自动替代，除非用户明确同意。

---

## 12. 当前参考实现

本技能的模板来自以下工作目录的整理版本：

```text
/mnt/data/potato_agent/work/DAP-Seq.202605091116
```

该参考实现已完成：

- Snakemake 化流程控制
- config/samples 参数化
- ChIPseeker Python+R 双脚本合并为单个 `03_chipseeker_annotate.R`
- 轻量语法检查与 Snakemake dry-run DAG 解析

后续生成新 DAP-Seq 工作目录时，应优先复制本技能内置模板，而不是直接依赖该临时工作目录。
