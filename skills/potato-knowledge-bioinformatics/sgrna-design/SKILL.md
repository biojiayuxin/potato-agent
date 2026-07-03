---
name: sgrna-design
description: 使用 CRISPOR 进行 CRISPR/Cas9 基因敲除 sgRNA 设计。运行时先检查 public_data/CRISPR_DB/<genome_name>/ 是否已有可复用 CRISPOR 数据库；若无，则告知用户需要构建且耗时较长，并通过 Slurm 后台任务构建数据库。
version: 2.4.0
metadata:
  hermes:
    tags: [CRISPR, gRNA, CRISPOR, sgRNA, knockout, design]
    related_skills: [slurm-for-long-running-tasks]
---

# sgRNA Design

为指定基因设计 CRISPR/Cas9 敲除 sgRNA。**默认只使用 CRISPOR**。CRISPOR 提供 guide/off-target 表、MIT/CFD 特异性评分、基因组注释定位、GrafEtAlStatus 以及多种 on-target 效率评分，和 `sgrna-design-diploid` 的等位基因/脱靶解释流程最匹配。

除非用户明确要求，或 CRISPOR 在当前环境不可用且用户接受替代方案，不要调用 FlashFry，也不要默认检查或创建 FlashFry 数据库。

## 核心原则

运行任何 sgRNA 设计前，先检查是否已有可复用的 CRISPOR 基因组数据库，避免重复建库：

```text
public_data/CRISPR_DB/<genome_name>/
```

如果存在对应数据库且基本验证通过，直接复用；如果不存在，先告诉用户需要创建数据库，通常可能耗时较长。实际创建数据库时，必须优先使用 Slurm 后台任务，不要在前台阻塞会话。

在开始新建数据库前，应提醒用户：数据库构建可能需要较长时间；如果 agent 页面响应中断，后续应要求 agent 继续该任务，agent 需要先检查 Slurm 状态和已有输出，再决定继续、复用或重建，不能盲目从头开始。

## 相关技能

建库或其它长时间任务时，加载并遵循：

```text
slurm-for-long-running-tasks
```

## 输入确认

执行前确认或推断：

1. `<genome_name>`：目标基因组/材料名称，用于查找 `public_data/CRISPR_DB/<genome_name>/`。
2. 目标基因 ID 或目标区域坐标。
3. 若需要新建数据库：
   - genome FASTA；
   - GFF3/GTF 注释文件，建议提供，且最好包含 `gene` 行；
   - CRISPOR genome ID。
4. PAM/enzyme：默认 SpCas9 NGG。
5. 输出目录。

## 可复用 CRISPOR 数据库约定

默认数据库根目录：

```bash
CRISPR_DB_ROOT=${CRISPR_DB_ROOT:-public_data/CRISPR_DB}
CRISPR_DB="$CRISPR_DB_ROOT/<genome_name>"
CRISPOR_GENOME_DIR="$CRISPR_DB/crispor"
CRISPOR_GENOME_ID="<crispor_genome_id>"
```

推荐布局：

```text
public_data/CRISPR_DB/<genome_name>/
└── crispor/
    ├── genomeInfo.all.tab
    └── <crispor_genome_id>/
        ├── <crispor_genome_id>.2bit
        ├── <crispor_genome_id>.fa.bwt
        ├── <crispor_genome_id>.gp
        └── ... other CRISPOR/BWA files ...
```

CRISPOR 要求 `--genomeDir` 指向包含 `genomeInfo.all.tab` 和 `<crispor_genome_id>/` 子目录的目录，即：

```bash
--genomeDir "$CRISPOR_GENOME_DIR"
```

## Step 1 — 先检查已有 CRISPOR 数据库

检查必要文件，而不是只检查目录名：

```bash
test -s "$CRISPOR_GENOME_DIR/genomeInfo.all.tab"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/genomeInfo.tab"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.2bit"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.fa.bwt"
```

如果这些文件存在，再做一个小区域 smoke test 或直接用目标序列运行 CRISPOR。验证通过后直接复用：

```bash
crispor --genomeDir "$CRISPOR_GENOME_DIR" "$CRISPOR_GENOME_ID" \
  targets.fa guides.tsv -o offtargets.tsv -p NGG --mm 4
```

## Step 2 — 如果数据库不存在，先告知用户并用 Slurm 建库

如果 `public_data/CRISPR_DB/<genome_name>/crispor/` 下没有可复用数据库，先明确告诉用户：

```text
没有找到可复用的 CRISPOR database，需要先创建数据库。该步骤可能耗时较长；我会提交 Slurm 后台任务执行。如果页面响应中断，请后续要求我继续该任务，我会先检查 Slurm 状态和已有输出，而不是直接重建。
```

然后加载 `slurm-for-long-running-tasks`，写一个可复现的建库脚本，再通过 Slurm 提交。

### CRISPOR 建库脚本示例

建库脚本应写入任务目录，例如：

```bash
mkdir -p scripts logs "$CRISPOR_GENOME_DIR"
cat > scripts/build_crispor_db.sh <<'BASH'
#!/usr/bin/env bash
set -euo pipefail

# 用户/任务环境中应提前设置：
# GENOME_FA, GENOME_GFF, CRISPOR_GENOME_DIR, CRISPOR_GENOME_ID

mkdir -p "$CRISPOR_GENOME_DIR"
crispor-add-genome --baseDir "$CRISPOR_GENOME_DIR" fasta "$GENOME_FA" \
  --desc "$CRISPOR_GENOME_ID|Scientific name|Common name|Version" \
  --gff "$GENOME_GFF"

# Minimal verification
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.2bit"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.fa.bwt"
test -s "$CRISPOR_GENOME_DIR/genomeInfo.all.tab"
BASH
chmod +x scripts/build_crispor_db.sh
```

用 Slurm 提交，不要前台执行：

```bash
# 先加载 slurm-for-long-running-tasks，并解析其 SKILL_DIR
bash "$SLURM_SKILL_DIR/scripts/submit-job.sh" \
  --job-name "crispor-db-<genome_name>" \
  --cpus 8 \
  --time 02:00:00 \
  --mem-gb 20 \
  --workdir "$PWD" \
  --output logs/crispor-db-%j.out \
  --error logs/crispor-db-%j.err \
  --script scripts/build_crispor_db.sh
```

提交后记录 job ID、脚本路径、日志路径、数据库路径。后续继续任务时，先检查 Slurm job 状态和输出文件，再决定是否运行设计步骤。

## Step 3 — 准备 targets.fa

CRISPOR 需要目标序列。根据用户输入选择来源：

```bash
# 目标区域已有坐标
samtools faidx genome.fa "chr:start-end" > targets.fa

# 或者从 CRISPOR .2bit 中提取，适用于原始 FASTA 已删除但 CRISPOR DB 存在的情况
twoBitToFa "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.2bit:chr:start-end" targets.fa
```

FASTA header 建议使用英文 gene ID 或 region ID，避免输出文件编码问题。

## Step 4 — 运行 CRISPOR 设计

默认参数：SpCas9 NGG，最多 4 mismatch。

```bash
crispor --genomeDir "$CRISPOR_GENOME_DIR" "$CRISPOR_GENOME_ID" \
  targets.fa guides.tsv -o offtargets.tsv -p NGG --mm 4
```

关键参数：

- `--genomeDir`：必须与建库 `crispor-add-genome --baseDir` 一致。
- genome ID：必须与建库时生成的 `<crispor_genome_id>` 一致。
- `-p NGG`：SpCas9 PAM。
- `--mm 4`：最多统计 4 mismatch 脱靶。
- `--guideLen 20`：默认 20 nt guide。

如果效率评分依赖报错，可临时加 `--noEffScores`，但需要在结果中说明效率评分缺失。

## CRISPOR 输出列

**guides.tsv：**

| 列 | 含义 |
|---|---|
| seqId | 输入 target ID |
| guideId | guide 编号+方向 |
| targetSeq | gRNA 序列（通常含 PAM） |
| mitSpecScore | MIT 特异性（0-100，高=好） |
| cfdSpecScore | CFD 特异性（0-100，高=好） |
| offtargetCount | 脱靶数，注意结合任务目标解释 |
| targetGenomeGeneLocus | exon / intron / intergenic |
| Doench '16-Score | on-target 效率（0-100） |
| Moreno-Mateos-Score | CRISPRscan（0-100） |
| Doench-RuleSet3-Score | RS3（-200~+200） |
| Out-of-Frame-Score | 移码概率（0-100） |
| Lindel-Score | indel 预测（0-100） |
| GrafEtAlStatus | `tt`=polyT 终止信号需排除，`GrafOK`=通过 |

**offtargets.tsv：** 每条 guide 的脱靶位点，包括 chrom、坐标、mismatch、MIT/CFD 风险分、基因注释等。

## 筛选排序

默认排除：

- `GrafEtAlStatus='tt'`；
- 明显不在目标功能区域的 guide；
- 低特异性或高风险 exonic off-target guide。

默认排序：

```text
无高风险 exonic off-target
→ cfdSpecScore 高
→ offtargetCount 少
→ mitSpecScore 高
→ Doench '16 / RS3 / 其它效率评分较高
→ 位于合适 CDS/exon 区域
```

每个基因默认输出 Top 5，除非用户要求更多或要求多 sgRNA 构建。

## FlashFry 备选说明

默认不要使用 FlashFry。只有在以下情况才考虑：

1. 用户明确要求使用 FlashFry；或
2. CRISPOR 在当前环境不可用，且用户接受 FlashFry 作为替代方案。

若使用 FlashFry，必须在结果中说明其 off-target 计数和评分语义与 CRISPOR 不完全等价，不能直接套用 CRISPOR 的 `offtargetCount` 解释。

## 输出建议

保存：

```text
targets.fa
guides.tsv
offtargets.tsv
recommended_sgrnas.tsv
summary.txt
```

用户回复中给出精简表格：

```text
Rank  guide+PAM  locus  MIT  CFD  offtargets  exonic_offtargets  efficiency  note
```

## 常见问题

1. **重复建库。** 运行前必须先查 `public_data/CRISPR_DB/<genome_name>/crispor/`。
2. **前台建库阻塞会话。** CRISPOR 建库要用 Slurm 后台任务。
3. **中断后盲目重跑。** 继续任务时先查 Slurm 状态、日志和数据库文件。
4. **`--genomeDir` 用错。** CRISPOR 运行目录必须是包含 `genomeInfo.all.tab` 和 `<crispor_genome_id>/` 的目录。
5. **CRISPOR 依赖缺失。** 确保 `crispor`、`crispor-add-genome`、`bwa`、kentUtils 可用。
6. **默认误用 FlashFry。** FlashFry 只作为显式请求或 CRISPOR 不可用时的备选，不作为默认流程。

## 验证清单

- [ ] 已先检查 `public_data/CRISPR_DB/<genome_name>/crispor/`。
- [ ] 若 CRISPOR 数据库存在，已通过必要文件检查或 smoke test。
- [ ] 若 CRISPOR 数据库不存在，已告知用户需要建库、耗时较长和中断后继续方式。
- [ ] 新建 CRISPOR 数据库已通过 Slurm 后台任务提交，而不是前台执行。
- [ ] 已记录 Slurm job ID、日志路径和数据库路径。
- [ ] sgRNA 设计使用了正确的 `--genomeDir` 和 CRISPOR genome ID。
- [ ] 已保存 guides/offtargets/recommended 结果文件。
- [ ] 最终推荐排除了 polyT 和高风险脱靶 guide。
