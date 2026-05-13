---
name: sgrna-design
description: 使用 CRISPOR 或 FlashFry 进行 CRISPR/Cas9 基因敲除 gRNA 靶点设计。默认用 CRISPOR。
version: 2.2.0
metadata:
  hermes:
    tags: [CRISPR, gRNA, CRISPOR, FlashFry, sgRNA, knockout, design]
---

# sgRNA Design

为指定基因设计 CRISPR/Cas9 敲除 gRNA。**默认使用 CRISPOR**（评分全面、自动基因注释），批量场景可选 FlashFry。

假定 `crispor`、`crispor-add-genome`、`flashfry`、`samtools` 在 PATH 中（系统环境可执行文件通常位于 `/usr/local/bin/`）。

## 输入确认

执行前向用户确认：

1. 基因组 FASTA 路径
2. GFF 注释路径（需含 `gene` 行）
3. 目标基因 ID（每行一个）

---

# CRISPOR

## 前置：基因组入库（一次性）

```bash
crispor-add-genome --baseDir /path/to/crispor_genomes fasta genome.fa \
  --desc "genomeId|Scientific name|Common name|Version" \
  --gff annotation.gff3
```

`--baseDir` 可把自定义基因组索引存放到独立目录。后续运行 CRISPOR 时必须把同一目录传给 `crispor --genomeDir /path/to/crispor_genomes`，否则只会读取 CRISPOR 安装目录下的默认 `genomes/`。

## 运行

```bash
# 提取基因序列
samtools faidx genome.fa
samtools faidx genome.fa "chr01:start-end" >> targets.fa  # header: >geneID

# 设计
crispor --genomeDir /path/to/crispor_genomes genomeId targets.fa guides.tsv -o offs.tsv -p NGG --mm 4
```

关键参数：`--genomeDir`（与 `crispor-add-genome --baseDir` 保持一致），`-p NGG`（PAM），`--mm 4`（最大 mismatch），`--guideLen 20`。

## 输出列

**guides.tsv：**

| 列 | 含义 |
|---|---|
| seqId | 基因 ID |
| guideId | guide 编号+方向 |
| targetSeq | gRNA 序列（含 PAM） |
| mitSpecScore | MIT 特异性（0-100，高=好） |
| cfdSpecScore | CFD 特异性（0-100，高=好） |
| offtargetCount | **真实脱靶数（不含自身，0=完全唯一）** |
| targetGenomeGeneLocus | exon / intron / intergenic |
| Doench '16-Score | on-target 效率（0-100） |
| Moreno-Mateos-Score | CRISPRscan（0-100） |
| Doench-RuleSet3-Score | RS3（-200~+200） |
| Out-of-Frame-Score | 移码概率（0-100） |
| Lindel-Score | indel 预测（0-100） |
| GrafEtAlStatus | `tt`=polyT终止信号需排除，`GrafOK`=通过 |

**offs.tsv：** 每条 guide 的脱靶位点（chrom、坐标、mismatch、MIT/CFD 风险分、基因注释）。

## 筛选排序

排除 `GrafEtAlStatus='tt'`，按 **cfdSpecScore ↓ → offtargetCount ↑ → Doench '16-Score ↓** 排序，每基因输出 Top 5。

输出格式：

```
Gene: DM8.2_chr06G18390 (170 candidates)
 Rank  gRNA_seq                     MIT  CFD  ot  Doench16  RS3  Locus
    1  GGTGGCGGCGCTACCACTATGCTGG   100  100  0   51        15   exon
    2  ACATACCGGCTCCCGCCATGTGG     100  100  0   59        62   exon
```

## 注意事项

- `GrafEtAlStatus='tt'` 的 guide 应排除（含 polyT 终止信号）。
- 脱靶落在 `exon` 中风险高，`intergenic` 中风险低。
- on-target 评分依赖 CRISPOR 的 Python 环境和 Azimuth/RS3 依赖；当前系统环境 `/opt/crispor_py39`（Python 3.9，sklearn 1.0.2）已通过完整评分自测。如报错，可先加 `--noEffScores` 跳过效率评分，仅保留 MIT/CFD 脱靶评分。

---

# FlashFry

## 运行

```bash
# 建库（一次性，可复用）
flashfry index --reference genome.fa --database genome.db --tmpLocation /tmp --enzyme spcas9ngg

# 提取基因序列（同上）
samtools faidx genome.fa "chr01:start-end" >> targets.fa

# 发现 + 评分
flashfry discover --fasta targets.fa --database genome.db --output candidates.bed --maxMismatch 3 --flankingSequence 30
flashfry score --input candidates.bed --database genome.db --output scored.bed --scoringMetrics doench2014ontarget,doench2016cfd,dangerous,hsu2013
```

## 筛选排序

排除 `context=NONE`，**0-mismatch ≤ 1**（FlashFry 含自身），按 **CFD_specificityscore ↓ → otCount ↑ → Doench2014OnTarget ↓**。

> FlashFry 的 `otCount` 含 on-target 自身，`otCount=1` 才等价于 CRISPOR 的 `offtargetCount=0`。
