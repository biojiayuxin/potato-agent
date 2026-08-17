---
name: multiple-sequence-alignment
description: Use when the user provides two or more homologous nucleotide or protein sequences and asks for a multiple sequence alignment, MSA, MAFFT, MUSCLE 5, Clustal Omega, or FAMSA result. Default to reproducible MAFFT --auto execution; use another supported aligner only when the user explicitly requests it. Do not use for database similarity search, read-to-reference mapping, pairwise-only interpretation, or whole-genome alignment.
version: 1.0.0
author: Potato Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [bioinformatics, multiple-sequence-alignment, msa, mafft, muscle, clustal-omega, famsa]
    related_skills: [ncbi-blast, slurm-for-long-running-tasks]
---

# 多序列比对

## 概述

使用 `scripts/run_msa.py` 调用 MAFFT、MUSCLE 5、Clustal Omega 或 FAMSA，并对运行结果进行确定性验收。默认使用 MAFFT `--auto`；只有用户明确指定其它软件时才切换。

Hermes Agent 负责理解用户意图、整理输入、判断分子类型和选择软件。脚本只负责固定命令、捕获日志、检查程序退出状态、验证结果结构并记录可重复性元数据。不要要求脚本推断 DNA、RNA、蛋白、CDS、同源性或合适的软件。

## 使用边界

适用于：

- 两条或以上同源核酸或蛋白序列的多序列比对；
- 用户直接粘贴的序列或用户提供的 FASTA 文件；
- 使用 MAFFT 生成默认 MSA；
- 用户明确要求使用 MUSCLE 5、Clustal Omega 或 FAMSA；
- 为系统发育、保守位点、结构域或引物设计准备 MSA。

不适用于：

- 将查询序列搜索到数据库；使用 `ncbi-blast` 等相似性搜索技能；
- FASTQ reads 到参考基因组的比对；
- CDS 到基因组定位；
- 全基因组比对、共线性或结构变异分析；
- 未经用户确认的密码子感知 CDS 比对。第一版不包含 MACSE 或蛋白比对后回译。

## 输入由 Hermes 检查

运行脚本前检查以下内容：

1. 将用户粘贴的序列写成标准 FASTA；用户提供文件时直接使用该文件，不改写原件。
2. 确认至少有两条非空序列，FASTA ID 唯一且有意义。
3. 根据用户描述和序列内容判断全部序列属于核酸还是蛋白，不混合两种分子类型。
4. 确认序列是预期的同源序列，适合进行 MSA。不要把任意无关序列强行比对。
5. 检查明显的非法字符、意外空白、复制残缺和 FASTQ 内容。短序列仅含 A/C/G/T 时可能同时符合核酸和蛋白字母表，结合上下文判断；无法可靠判断时询问用户。
6. 输入已经含 gap 时，先确认用户是要重新比对还是保留已有比对。脚本不会解释已有 gap 的含义。

这些属于语义和输入质量判断。脚本只做执行所需的最低限度 FASTA 解析，并用输入记录数和 ID 验收输出。

## 选择软件

| 用户要求 | `--aligner` | 固定执行策略 |
|---|---|---|
| 未指定软件，或要求 MAFFT | `mafft` | `mafft --auto --thread N INPUT` |
| 明确要求 MUSCLE 或 MUSCLE 5 | `muscle` | `muscle -align INPUT -output OUTPUT -threads N` |
| 明确要求 Clustal Omega | `clustalo` | `clustalo -i INPUT -o OUTPUT --threads=N --outfmt=fasta` |
| 明确要求 FAMSA | `famsa` | `famsa -t N INPUT OUTPUT` |

不要因为默认软件缺失而静默切换。MAFFT 不可用时报告依赖错误；用户指定的软件不可用时同样报告。FAMSA 主要用于蛋白 MSA，只有用户明确要求且输入适合时使用。

第一版不开放任意额外参数。用户要求 `--localpair`、`--maxiterate`、`Super5` 或其它非默认策略时，说明当前脚本固定使用上表策略，不要绕过脚本直接运行一个未记录的命令。

## 运行

从技能加载结果取得 `skill_dir`，不要硬编码技能安装目录。所选 aligner 必须位于调用进程的 `PATH`；脚本不会安装软件或修改环境。

为每次运行使用新的结果目录，并明确传入线程数：

```bash
python3 "${SKILL_DIR}/scripts/run_msa.py" \
  --input /path/to/input.fasta \
  --aligner mafft \
  --threads 8 \
  --output-dir /path/to/msa-result
```

`--aligner` 缺省为 `mafft`，`--threads` 缺省为 `1`。在 Potato 共享软件环境完成 MSA 软件发布后，通过包含这些软件的 `potato-bio-run` 环境执行同一脚本。不要假设当前 `comparative-core` 已包含 MSA 软件；先验证实际可执行文件和环境 manifest。

大型数据集预计运行较久时，使用 `slurm-for-long-running-tasks` 提交相同命令。不要把尚未结束的 Slurm 作业报告为已完成。

## 脚本输出与验收

结果目录包含：

```text
msa-result/
  alignment.fasta
  aligner.log
  run.json
```

脚本仅在以下条件全部满足后发布 `alignment.fasta`：

1. aligner 退出状态为 `0`；
2. 临时输出存在且非空；
3. 输出可以解析为 FASTA；
4. 输入和输出记录数一致；
5. 输入和输出的 FASTA ID 集合一致；
6. 所有输出序列具有相同的比对长度。

正式结果通过临时文件原子替换产生。运行失败或结果验收失败时保留 `aligner.log` 和 `run.json`，但不产生正式 `alignment.fasta`。

`run.json` 记录脚本版本、aligner 版本探测结果、固定命令、线程数、输入与输出 SHA-256、序列数量、比对长度、时间和退出状态。以 `status` 为准：只有 `success` 表示正式结果可交付。

退出码：

| 退出码 | 含义 |
|---|---|
| `0` | 比对和结果验收成功 |
| `2` | 参数、输入、输出目录或软件依赖错误 |
| `3` | aligner 启动失败或返回非零状态 |
| `4` | aligner 返回成功，但输出未通过结构验收 |
| `130` | 运行被中断 |

## 向用户呈现结果

先读取 `run.json`，确认 `status` 为 `success`，再检查 `alignment.fasta`。不要仅因文件存在就声称成功。

最终回复包括：

- 使用的软件、版本和固定策略，例如 `MAFFT --auto`；
- 输入序列数、输出序列数和比对长度；
- 完整 `alignment.fasta`、`run.json` 和日志的绝对路径；
- 对结果的简要说明，以及用户明确要求的保守位点或差异解释。

小型结果可以在回复中展示完整或有限宽度的对齐内容。大型结果只展示有界预览，不要把完整 FASTA 读入对话上下文。不要把 gap 比例、保守位点或一致性分数说成脚本已经计算的指标；第一版脚本不计算这些生物学统计。

## 常见错误

1. **让脚本自动判断序列类型。** 先由 Hermes 根据用户上下文和输入完成判断。
2. **未指定软件却自动选择 FAMSA 或 MUSCLE。** 默认始终为 MAFFT `--auto`。
3. **软件缺失时静默回退。** 停止并明确报告缺失的可执行文件。
4. **绕过脚本直接调用 aligner。** 这会丢失版本、哈希、日志和结果验收记录。
5. **重复使用已有结果目录。** 脚本拒绝覆盖它管理的三个输出文件；为每次运行创建新目录。
6. **把脚本的结构检查描述成生物学质量评估。** 相同列长不等于比对质量可靠，结果仍需结合生物学问题解释。

## 验证清单

- [ ] 已由 Hermes 检查输入格式、分子类型、ID 和任务适用性。
- [ ] 未指定软件时使用了 `--aligner mafft`。
- [ ] 只有用户明确指定时才使用 MUSCLE 5、Clustal Omega 或 FAMSA。
- [ ] 使用新的输出目录并明确记录线程数。
- [ ] `run.json.status` 为 `success`。
- [ ] aligner 退出状态为 `0`，输入输出记录数与 ID 一致。
- [ ] 已返回完整结果路径并提供有界结果预览或摘要。
