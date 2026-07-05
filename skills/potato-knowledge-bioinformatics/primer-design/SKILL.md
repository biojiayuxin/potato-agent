---
name: primer-design
description: 根据用户提供的基因名，基因号，转录本号，染色体位置，基因组名称等信息，设计PCR，qPCR的引物智能设计。
version: 0.3.1
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, bioinformatics, primer-design, pcr, qpcr, rt-qpcr, primer3, mfeprimer, specificity, primer-db, slurm]
    related_skills: [genome-sequence-extraction, potato-gene-search, slurm-for-long-running-tasks]
---

# Primer Design

## Overview

本技能用于根据用户提供的 **序列、基因名、基因号、转录本号、染色体位置、基因组名称** 等信息，完成 PCR / qPCR / RT-qPCR 引物智能设计。核心目标不是只给出 primer3 的局部候选，而是生成经过基础热力学过滤和全基因组特异性筛查的可实验验证候选引物。

默认运行原则：

1. **用户直接输入目标序列 / FASTA**：不再额外检索基因信息，直接进入 primer3-py 设计、thermo 过滤和 MFEprimer 特异性检查。
2. **用户输入基因名、基因号、转录本号、染色体位置等非序列信息**：先调用或遵循 `potato-gene-search`、`genome-sequence-extraction` 等技能解析并提取目标序列，再进入引物设计核心流程。
3. **primer3-py 负责候选设计和热力学检查**；它只知道输入模板，不知道全基因组背景。
4. **MFEprimer 负责 pair-aware in-silico PCR 特异性筛查**；最终推荐必须参考 MFEprimer 对用户目标 genome FASTA 的结果。
5. 最终结果要包含推荐引物、淘汰原因、特异性判断和湿实验验证提醒。

本技能必须保持多用户通用性：不要写死个人项目目录、一次性任务路径或单个用户的输入文件。可写死当前系统共享工具路径 `/opt/micromamba/envs/primer-tools/bin/python`，因为这是部署层面的 primer3-py 运行环境；输入、输出、基因组和注释路径仍应由用户提供或由公共资源解析得到。

## When to Use

当用户要求以下任务时使用本技能：

- 根据基因名、基因号、转录本号、reported ID 或坐标设计 PCR/qPCR 引物；
- 根据一段 DNA/FASTA 序列直接设计引物；
- 为马铃薯或其他植物基因组中的目标区域设计普通 PCR、qPCR、RT-qPCR、Sanger 验证或基因分型引物；
- 检查已有引物是否存在热力学风险或全基因组非特异扩增风险；
- 需要基于本地/用户指定 genome FASTA，而不是只依赖 NCBI Primer-BLAST 或普通 BLAST。

不要把本技能用于：

- CRISPR sgRNA 设计；使用 `sgrna-design` 或 `sgrna-design-diploid`。
- 只提取序列而不设计引物；使用 `genome-sequence-extraction`。
- 只查询马铃薯 DMv8 基因信息；使用 `potato-gene-search`。
- 替代湿实验验证；最终仍需凝胶、熔解曲线、扩增效率或测序确认。

## Runtime Tools

当前系统的默认工具：

```bash
PRIMER_PYTHON=/opt/micromamba/envs/primer-tools/bin/python
$PRIMER_PYTHON -c "import primer3; print(primer3.__version__)"

mfeprimer version
mfeprimer index --help
mfeprimer spec --help
```

已知可用版本示例：

```text
primer3-py 2.3.0
MFEprimer v4.2.4
```

执行任务前应重新检查工具可用性；如果版本或参数不同，以本机 `mfeprimer spec --help` 输出为准。

## Input Routing Logic

根据用户输入类型选择入口。

| 用户输入 | 入口逻辑 | 需要调用的相关技能 |
|---|---|---|
| 直接 DNA 序列或 FASTA | 清理序列后直接进入 **Core Primer Design Pipeline** | 通常不需要 |
| `chr:start-end` 坐标 | 先从 genome FASTA 提取目标窗口，再设计引物 | `genome-sequence-extraction` |
| gene ID / transcript ID | 先解析注释并提取 gene/transcript/CDS/exon/窗口序列 | `genome-sequence-extraction` |
| 马铃薯基因名、symbol、reported ID | 先解析到标准 gene ID、代表转录本和坐标，再提取序列 | `potato-gene-search` + `genome-sequence-extraction` |
| 已有引物表 | 跳过 primer3 设计，只做 thermo + MFEprimer 检查 | 本技能核心后半段 |

### Direct sequence path

如果用户直接给出序列：

1. 将序列中的空白、数字和 FASTA header 分离；保留 IUPAC DNA 碱基。
2. 输出 `target.fa`。
3. 如果未提供 genome FASTA，仍可运行 primer3-py 设计和 thermo 检查，但必须说明无法完成全基因组特异性判定。
4. 如果提供了 genome FASTA，直接进入 MFEprimer specificity check。

### Gene/name/coordinate path

如果用户输入的是基因名、基因号、转录本号、染色体位置等信息：

1. 马铃薯 DMv8 基因名或 reported ID：先使用 `potato-gene-search` 解析候选基因、代表转录本和坐标。
2. 基因号/转录本号/坐标：使用 `genome-sequence-extraction` 的规则从指定 genome FASTA 和 annotation 中提取目标序列。
3. 一个 gene 对应多个 transcript 时，不要静默取第一条；默认使用代表转录本或最长 CDS/最长 transcript，并在报告中说明。若选择会影响实验目标，应请用户指定。
4. 坐标采用 1-based closed interval；BED 输入需转换。负链目标按实验设计需要输出反向互补并说明方向。
5. 序列提取成功后再进入 **Core Primer Design Pipeline**。

## Required Inputs and Defaults

| 参数 | 说明 | 默认/要求 |
|---|---|---|
| target | FASTA、DNA 序列、gene ID、transcript ID、坐标或已有 primer table | 必需 |
| genome | genome FASTA 路径或基因组名称 | MFEprimer 特异性检查必需 |
| annotation | GFF3/GTF 路径 | gene/transcript 输入时必需，除非已能从 API 得到序列 |
| assay_type | `pcr`、`qpcr`、`rt-qpcr`、`sanger`、`genotyping` | 未提供时默认 `pcr` |
| product_size | 扩增片段范围 | 按 assay_type 设置 |
| num_candidates | primer3 返回候选数 | 默认 20 |
| output_dir | 结果目录 | 默认 `primer_design_<target>` |

默认扩增片段范围：

| assay_type | product_size 默认值 | 备注 |
|---|---:|---|
| `pcr` | 100-1000 bp | 普通验证 PCR |
| `qpcr` | 70-200 bp，优先 80-150 bp | SYBR qPCR 对 off-target 更严格 |
| `rt-qpcr` | 70-200 bp，优先 80-150 bp | 可优先跨 exon junction，但仍必须查 genome |
| `sanger` | 300-900 bp | 目标位点两侧留测序读长缓冲 |
| `genotyping` | 100-500 bp | 注意 SNP/indel 与等位特异设计 |

## Core Primer Design Pipeline

### Stage 0: 创建结果目录和运行记录

建议每次任务创建独立目录：

```text
primer_design_<target>/
├── target.fa
├── primer3_candidates.tsv
├── primer3_thermo.tsv
├── candidates.mfeprimer.tsv
├── mfeprimer_specificity.json 或 mfeprimer_specificity.tsv
├── recommended_primers.tsv
├── rejected_primers.tsv
├── report.md
└── run_metadata.json
```

`run_metadata.json` 至少记录：target、genome FASTA、annotation、assay_type、product_size、primer3 版本、MFEprimer 版本、命令行、创建时间。

### Stage 1: primer3-py 设计候选引物

使用 `/opt/micromamba/envs/primer-tools/bin/python` 运行 primer3-py。不要使用系统默认 `python`，当前环境可能没有该命令。

最小设计参数：

```python
import primer3

seq_args = {
    "SEQUENCE_ID": target_id,
    "SEQUENCE_TEMPLATE": target_sequence,
}

global_args = {
    "PRIMER_PICK_LEFT_PRIMER": 1,
    "PRIMER_PICK_RIGHT_PRIMER": 1,
    "PRIMER_NUM_RETURN": 20,
    "PRIMER_OPT_SIZE": 20,
    "PRIMER_MIN_SIZE": 18,
    "PRIMER_MAX_SIZE": 25,
    "PRIMER_OPT_TM": 60.0,
    "PRIMER_MIN_TM": 58.0,
    "PRIMER_MAX_TM": 62.0,
    "PRIMER_PAIR_MAX_DIFF_TM": 2.0,
    "PRIMER_MIN_GC": 40.0,
    "PRIMER_MAX_GC": 60.0,
    "PRIMER_GC_CLAMP": 1,
    "PRIMER_MAX_POLY_X": 4,
    "PRIMER_PRODUCT_SIZE_RANGE": [[100, 300]],
    "PRIMER_EXPLAIN_FLAG": 1,
}

result = primer3.design_primers(seq_args, global_args)
```

根据用户实验类型替换 `PRIMER_PRODUCT_SIZE_RANGE`。若用户指定目标 SNP/indel/功能位点，应使用 `SEQUENCE_TARGET` 或 `SEQUENCE_INCLUDED_REGION` 让扩增片段覆盖目标；需要避开的低复杂度、重复或变异区域可加入 `SEQUENCE_EXCLUDED_REGION`。

关键要求：

- 不要只返回 `PRIMER_PAIR_0`；至少保留 20 对候选进入后续过滤。
- 如果 primer3 返回候选不足，应记录 `PRIMER_*_EXPLAIN` 并放宽条件或提示用户目标序列过短/GC 极端/重复太多。
- `primer3_candidates.tsv` 应保存所有候选，而不是只保存最终推荐。

### Stage 2: primer3-py 热力学检查

对每对候选运行：

```python
primer3.calc_hairpin(seq, **conditions)
primer3.calc_homodimer(seq, **conditions)
primer3.calc_heterodimer(fwd, rev, **conditions)
primer3.calc_end_stability(fwd, rev, **conditions)
primer3.calc_end_stability(rev, fwd, **conditions)
```

推荐 `conditions` 在用户未提供实验体系时使用 primer3-py 默认；若用户给出盐浓度、Mg2+、dNTP、引物浓度，应显式传入。

`primer3_thermo.tsv` 建议字段：

```text
target_id	pair_id	forward_seq	reverse_seq	forward_tm	reverse_tm	tm_diff	forward_gc	reverse_gc	amplicon_size	primer3_penalty	forward_hairpin_tm	forward_hairpin_dg_kcal	reverse_hairpin_tm	reverse_hairpin_dg_kcal	forward_homodimer_tm	forward_homodimer_dg_kcal	reverse_homodimer_tm	reverse_homodimer_dg_kcal	heterodimer_tm	heterodimer_dg_kcal	end_stability_fwd_rev_dg_kcal	end_stability_rev_fwd_dg_kcal	thermo_flag	thermo_reason
```

注意：primer3-py 的 `.dg` 常见单位是 cal/mol；报告 kcal/mol 时应除以 1000。3' 端二聚体风险比全局 dG 更关键。

### Stage 3: 准备 MFEprimer 输入

MFEprimer 4.x 可读取 TSV：

```text
name<TAB>fp<TAB>rp
pair_001<TAB>ATGCGT...<TAB>CGTACG...
pair_002<TAB>...
```

写出 `candidates.mfeprimer.tsv`，其中 `name` 应包含 target ID 与 pair index，便于回溯：

```text
DM8C01G00010_pair_001	FORWARDSEQ	REVERSESEQ
```

MFEprimer 检查的是引物对能否在数据库中形成扩增产物；普通单引物 BLAST 不能替代这一判断。

### Stage 4: MFEprimer 数据库检查、建库和特异性检查

MFEprimer 需要自己的 index，不兼容 `makeblastdb`、`bwa index`、`bowtie2-build` 或 `samtools faidx`。

#### 4.1 优先复用公共 Primer_DB

系统已经为常用基因组准备了 MFEprimer index，默认保存在：

```text
/mnt/data/public_data/Primer_DB
```

兼容路径：

```text
/mnt/data/potato_agent/public_data/Primer_DB
```

运行 MFEprimer 前，必须先动态检查 `Primer_DB` 中是否已有对应基因组目录；不要把某次查看到的基因组列表写成固定知识，也不要重复为已有 index 的常用基因组建库。`Primer_DB` 内容会变化，智能体每次运行时都应现场检查。

判断可复用的最低要求：候选目录下存在可作为 `mfeprimer spec -d` 输入的 genome FASTA，并且同目录存在 MFEprimer 生成的 index 文件，例如与 FASTA 同前缀的 `.primerqc`、`.primerqc.fai` 和 `.json` 等文件。

若用户只提供基因组名称，应先在 `Primer_DB` 中做名称规范化/模糊匹配，再把匹配到的 genome FASTA 作为 `mfeprimer spec -d` 的数据库输入。如果用户提供的是自定义 `genome.fa`，但它与 `Primer_DB/<genome>/` 中已有 FASTA 是同一个基因组，应优先使用 `Primer_DB` 中已建好 index 的 FASTA；同时在报告中记录实际用于 specificity check 的路径。

#### 4.2 Primer_DB 中没有对应基因组时再建 index

只有在 `Primer_DB` 中没有对应基因组，或用户明确要求使用一个新的自定义 genome FASTA 时，才运行：

```bash
mfeprimer index -i genome.fa -k 9 -c 16
```

建库前要提醒用户：MFEprimer index 创建时间可能较长，大基因组可能需要较长等待；如果智能体响应中断，用户可以提醒智能体“继续运行/检查上次 primer index 任务”，智能体应优先检查 Slurm 队列、日志和目标 index 文件，而不是盲目重跑。

#### 4.3 新建 index 必须使用 Slurm 后台任务

当需要为新 genome FASTA 创建 MFEprimer index 时，不要在前台长时间运行。应加载并遵循 `slurm-for-long-running-tasks` 技能，将建库任务提交到 Slurm 后台。

推荐做法：

1. 在当前任务结果目录或用户指定的数据库目录中准备建库运行脚本；只需作为本次任务的运行脚本，不要把示例脚本或固定模板文件额外写入技能目录。
2. 脚本中记录 genome FASTA、kvalue、CPU、日志路径和开始/结束时间。
3. 通过 `slurm-for-long-running-tasks` 的 `scripts/submit-job.sh` 提交，并记录 job ID。
4. 如果任务完成后再继续引物设计，先检查 index 文件是否生成且非空。

提交时使用 `slurm-for-long-running-tasks` 技能中的 wrapper；根据基因组大小选择资源，提交前按该技能要求确认内存。不要把一次任务的脚本、日志或示例文件加入本技能目录。

注意：如果只是使用 `Primer_DB` 中已有 index，则不需要提交 Slurm 建库任务。

#### 4.4 运行 specificity check

特异性检查命令：

```bash
mfeprimer spec \
  -i candidates.mfeprimer.tsv \
  -d genome.fa \
  -o mfeprimer_specificity.json \
  -s 50 \
  -S 1000 \
  -c 8 \
  --json
```

其中 `-d genome.fa` 应优先指向 `Primer_DB/<genome>/genome.fa`，如果该数据库存在且匹配用户目标基因组。

参数按实验类型调整：

| assay_type | `-s` | `-S` | 判定口径 |
|---|---:|---:|---|
| pcr | 50 或用户最小值 | 用户最大值，常 1000 | 目标产物唯一或可解释 |
| qpcr | 50/70 | 200 | 任何可信 off-target 都应严格淘汰 |
| rt-qpcr | 50/70 | 200 | 查 genome，不能只查 transcriptome |
| sanger | 100 | 900/1200 | 注意目标位点与读长缓冲 |
| genotyping | 50 | 500 | 注意等位特异和非目标变异 |

运行前检查版本和帮助：

```bash
mfeprimer version
mfeprimer spec --help
```

在报告中说明实际使用的是已有 `Primer_DB` index，还是新建 index；若新建 index 任务仍在运行，应返回 Slurm job ID、日志路径和后续恢复检查方式。

### Stage 5: 解析 MFEprimer 结果

对每个 primer pair 统计：

- 总预测 amplicon 数；
- 目标区域 amplicon 是否存在；
- 目标产物坐标和大小；
- off-target 数量、位置、大小、Tm；
- 是否存在与目标大小接近的 off-target；
- 是否涉及 paralog、repeat、unplaced/alt contig 或多个 haplotype。

如果用户是直接输入序列且未提供目标 genomic coordinate，则目标产物位置可能无法自动判定；此时应按“是否只有一个 in-range amplicon”作为主要特异性依据，并在报告中说明目标定位限制。

### Stage 6: PASS / WARNING / FAIL 判定

推荐判定规则：

- **PASS**：只有一个可信 in-range 目标扩增产物；产物大小符合要求；热力学检查无高风险；qPCR 无可信 off-target。
- **WARNING**：有目标产物，但存在低 Tm/边界 off-target、单倍型/旁系同源解释不清、重复区域或 unplaced contig 风险；普通 PCR 可作为备选，但 qPCR 通常不推荐。
- **FAIL**：无目标产物；多个可信 in-range 产物；qPCR 出现可信 off-target；同大小/近似大小 off-target；3' 端非特异结合或二聚体风险高。

排序优先级：

1. PASS > WARNING > FAIL；
2. off-target 数越少越好；
3. 目标产物大小越接近用户要求越好；
4. Tm 越接近目标温度且正反向 ΔTm 越小越好；
5. GC、长度、poly-X、3' clamp 更合理者优先；
6. hairpin/dimer/end-stability 风险更低者优先；
7. qPCR 优先短扩增子且单一产物。

## Output Tables

`recommended_primers.tsv` 至少包含：

```text
target_id	assay_type	pair_id	forward_seq	reverse_seq	amplicon_size	forward_tm	reverse_tm	tm_diff	forward_gc	reverse_gc	primer3_penalty	hairpin_flag	homodimer_flag	heterodimer_flag	end_stability_flag	mfeprimer_amplicon_count	target_amplicon_location	offtarget_count	offtarget_summary	verdict	reason
```

`rejected_primers.tsv` 应包含失败候选和明确淘汰原因，避免用户只看到少量推荐而无法复查筛选过程。

`report.md` 应简明说明：

- 输入目标、目标解析方式、genome/annotation 来源；
- primer3-py 和 MFEprimer 版本；
- 使用的设计参数和 specificity 参数；
- MFEprimer 数据库来源：复用 `/mnt/data/public_data/Primer_DB` 中本次动态匹配到的 genome FASTA，或新建 index 的 Slurm job ID / 日志路径；
- 推荐前 3-5 对引物；
- off-target、多拷贝、单倍型或旁系同源风险；
- in-silico 结果局限和后续湿实验验证建议。

## Plant/Potato-Specific Notes

马铃薯和其他植物基因组常见多倍体、杂合单倍型、旁系同源基因、重复序列和 unplaced/alt contig。设计前必须明确判定口径：

- 是只扩增一个特定位点，还是允许扩增所有等位/单倍型拷贝？
- 是否排除旁系同源基因？
- 是否纳入 unplaced/alt contigs？
- 是否需要避开已知 SNP/indel 或重复区域？

若用户没有说明，默认目标是：优先推荐目标区域唯一或最接近唯一的引物；对多拷贝/多单倍型结果给出 WARNING，不要把复杂结果写成“唯一特异”。

RT-qPCR 特别注意：跨 exon junction 设计不能替代 genome specificity check；仍需检查 gDNA、假基因和旁系同源位点，并建议 no-RT control、熔解曲线和扩增效率验证。

## Common Pitfalls

1. **用户直接给序列时还去做不必要的基因检索**：直接序列应直接进入核心设计流程。
2. **基因名/坐标未提取序列就开始设计**：非序列输入必须先通过相关技能或注释文件得到目标模板。
3. **把 primer3 top hit 当最终答案**：primer3 只检查输入模板，不检查全基因组。
4. **用普通 BLAST 代替 MFEprimer**：单引物 hit 不能证明引物对形成合格扩增产物。
5. **忽略 gene/transcript 歧义**：一个 gene 多个 transcript 时必须说明选择规则。
6. **坐标体系混淆**：明确 1-based closed interval；BED 需转换。
7. **负链方向错误**：负链目标应按实验设计需要反向互补并说明。
8. **qPCR 过滤过宽**：SYBR qPCR 对可信 off-target 应严格淘汰。
9. **未动态检查公共 Primer_DB 就重复建库**：常用基因组 index 已放在 `/mnt/data/public_data/Primer_DB`，但目录内容会变化，运行前必须现场检查并优先复用。
10. **MFEprimer index 与 genome FASTA 不匹配**：必须确认 index 对应同一个 FASTA，并在报告中记录实际 `-d` 路径。
11. **在前台创建大型 MFEprimer index**：新基因组建库可能耗时很长，必须通过 Slurm 后台运行；如果响应中断，先检查 job/log/index 文件再继续。
12. **过度承诺**：in-silico PASS 只能表示计算筛选通过，不能保证实验一定成功。

## Verification Checklist

- [ ] 已判断输入类型：直接序列、坐标、gene/transcript ID、基因名或已有引物表。
- [ ] 直接序列输入已直接进入核心设计流程。
- [ ] 非序列输入已使用 `potato-gene-search` / `genome-sequence-extraction` 或等价方法提取目标序列。
- [ ] 已确认或说明 genome FASTA、annotation、assay_type 和 product_size。
- [ ] 已用 `/opt/micromamba/envs/primer-tools/bin/python` 运行 primer3-py。
- [ ] primer3-py 生成了多对候选，而不是只用第一对。
- [ ] 已完成 thermo 检查，并记录 hairpin/dimer/end-stability 风险。
- [ ] 已运行或明确说明无法运行 MFEprimer specificity check。
- [ ] 运行 MFEprimer 前已优先检查 `/mnt/data/public_data/Primer_DB` 是否存在匹配基因组 index。
- [ ] 若复用 Primer_DB，已记录实际使用的 `Primer_DB/<genome>/genome.fa` 路径。
- [ ] 若 Primer_DB 中没有对应 index，已提醒用户建库耗时较长，并使用 Slurm 后台提交 `mfeprimer index`。
- [ ] 若 Slurm 建库仍在运行，已记录 job ID、日志路径，并提醒中断后可要求智能体继续检查。
- [ ] MFEprimer 使用的 index 与 genome FASTA 匹配。
- [ ] 推荐表包含序列、Tm、GC、产物长度、特异性结果、verdict 和 reason。
- [ ] 最终报告说明了多拷贝/单倍型/旁系同源风险和湿实验验证建议。
