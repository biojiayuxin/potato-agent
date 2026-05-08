---
name: bsa-seq-analysis
description: 复用现成的 BSA-Seq Snakemake 模板开展混池测序分析。适用于用户提供或需要生成 BSA-Seq 分析项目、希望基于模板 config.yaml、Snakefile 和 scripts 生成可执行工作目录，并在确认关键参数后运行 Snakemake 的场景。
---

# BSA-Seq Analysis

使用这个技能时，默认**复用技能目录中的 `config.yaml`、`Snakefile` 和 `scripts/`**，不要自行重写流程逻辑。

## 你要做的事

1. 先读取技能目录中的：
   - `config.yaml`
   - `Snakefile`
   - `scripts/` 下脚本
2. 优先自动补全可由智能体自行确定的参数，不要把这类问题抛给用户。
3. 在 `/work` 下创建任务目录，命名为 `job_name_YYYYMMDDHH`。
4. 将 `config.yaml`、`Snakefile`、`scripts/` 复制到任务目录。
5. 按用户提供的信息和自动探测结果修改任务目录中的 `config.yaml`。
6. 在任务目录中创建 `readme.md`，写明：
   - 任务目的
   - 最终参数
   - 自动推断结果
   - 将执行的 Snakemake 命令
7. 把以下内容展示给用户确认：
   - 最终 `config.yaml` 关键参数
   - 哪些参数是自动推断的
   - 是否复用原始 `Snakefile`
   - 将执行的命令
8. **只有在用户明确许可后**，才能开始运行 Snakemake。

## 参数处理规则

### 运行前依赖检查

在确认运行环境时，不仅要检查可执行程序，还要检查脚本实际依赖的 R 包。

#### 必须检查的可执行程序

- `snakemake`
- `bwa`
- `samtools`
- `bcftools`
- `fastp`
- `gatk`
- `python3`
- `Rscript`
- `seqkit`


必须检查的R包:

- `ggplot2`
- `gridExtra`
- `tidyr`

```bash
Rscript -e "library(ggplot2); library(gridExtra); library(tidyr); library(grid)"
```

若 Rscript 存在但缺少必需 R 包，不应直接开跑，应先向用户说明缺失包，并提出安装方案。

## 需要向用户确认的参数

至少确认这些字段：

- `genome_fa`
- `fastq_dir`
- `sample_A`
- `sample_B`
- `chr_num`
- `threads`

### 需要自动推断的参数

#### `chr_prefix`
根据参考基因组 FASTA 自动推断。

```bash
grep '^>' {genome_fa} | head -1
```

根据第一条序列名判断染色体前缀。
例如：

- `>RH_chr01` → `chr_prefix: RH_chr`
- `>chr01` → `chr_prefix: chr`
- `>Chr01` → `chr_prefix: Chr`

如果命名无法可靠拆分，再向用户确认。

以下参数如用户未指定，可沿用模板默认值，但仍应向用户展示：

- `qual_threshold`
- `window_size`
- `step_size`
- `delta_threshold`

## 执行原则

- 默认优先复用模板，不重写 `Snakefile`。
- 不要擅自修改 `scripts/` 中脚本逻辑。
- 对能自动推断的参数，先自动推断，不要直接打扰用户。
- 运行环境由提交任务时拼接 `PATH` 提供，不在 `config.yaml` 中设置环境参数。
- 不要在未确认参数前直接运行。
- 不要在未获许可前直接运行 Snakemake。
- 若用户明确要求修改流程，再改 `Snakefile` 或脚本。

## 生成任务目录时的要求

任务目录中应包含：

- `config.yaml`
- `Snakefile`
- `scripts/`
- `readme.md`

`readme.md` 至少写明：

- 创建时间
- 输入数据路径
- 参考基因组路径
- 两个混池样本名称及含义
- 关键分析参数
- 自动推断得到的 `chr_prefix`
- 提交任务时拼接的 `PATH`
- 执行命令

## 提交任务的硬性要求

不要以前台方式直接运行 `snakemake`，避免因为 OpenClaw 的 `exec` 超时或会话结束导致流程被杀掉。

必须在提交任务时直接拼接好运行所需的 `PATH`，让 `Snakefile` 中各 rule 继承该环境。

应优先按**后台提交**方式启动任务，并显式设置 Snakemake 缓存目录：

```bash
cd /work/{job_ID}
nohup setsid bash -lc "
cd /work/{job_ID}
export PATH={DIR_TO_PATH}:\$PATH
export XDG_CACHE_HOME=/tmp/snakemake_cache
mkdir -p \"\$XDG_CACHE_HOME\"
snakemake -s Snakefile -j {threads} >> pipeline.log 2>&1
" </dev/null >/dev/null 2>&1 &
echo $! > snakemake_launcher.pid
```

其中：

- `{job_ID}` 是任务目录名
- `{DIR_TO_PATH}` 是自动探测后汇总得到的 `bin` 目录列表，多个目录用冒号拼接
- `{threads}` 来自最终确认的线程数

## 提交原则

- 必须使用后台提交，避免 `exec` 超时影响 Snakemake 主进程。
- 必须设置 `XDG_CACHE_HOME=/tmp/snakemake_cache`。
- 标准输出和标准错误都追加到 `pipeline.log`。
- 提交前应先拼接好完整 `PATH`，确保关键软件和脚本依赖都可用。
- 提交后将 launcher PID 写入 `snakemake_launcher.pid`。
- 启动完成后，向用户汇报任务目录、提交命令、日志文件位置。

## 输出风格

回复用户时保持简洁，按以下顺序组织：

1. 需要用户提供哪些参数
2. 哪些参数可以自动确定
3. 还缺哪些参数
4. 准备生成哪些文件
5. 最终拟执行什么命令
6. 请求用户确认

