---
name: genome-sequence-extraction
description: Use when extracting sequences from plant/potato genomes by gene ID, transcript/protein ID, promoter definition, gene-relative window, or arbitrary genomic coordinates. Provides scripts for FASTA-record extraction and coordinate-based genome extraction, with 5-prime-to-3-prime orientation rules.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [genome, fasta, sequence-extraction, promoter, cds, protein, gff3, coordinates, potato]
    related_skills: [gffread-export-cds-pep]
---

# Genome Sequence Extraction

## Overview

本技能用于根据用户要求从基因组或其派生 FASTA 文件中提取序列。核心原则是：

1. **确定性强的序列**（CDS、蛋白、已存在的转录本/基因 FASTA 记录）优先直接从已有 `cds.fa`、`pep.fa` 或目标 FASTA 中按 ID 提取。
2. 如果缺少 `cds.fa` / `pep.fa`，先使用相关技能 **`gffread-export-cds-pep`** 从 `genome.fa + gff3/gtf` 生成 CDS 和蛋白 FASTA，再按 ID 提取。
3. **启动子、基因上下游片段、任意坐标片段**：先由智能体根据用户描述、基因号和 GFF/GTF/注释文件确定坐标参数，再用本技能脚本执行实际序列提取。
4. 凡涉及基因方向的结果，默认按该基因的 **5' -> 3' 方向**输出；任意基因组坐标若用户没有特别说明，默认按 **forward strand（+ 链）**输出。

## When to Use

使用本技能处理如下任务：

- “提取这些基因的 CDS / 蛋白序列”
- “从 cds.fa/pep.fa 中按基因号或转录本号提取序列”
- “提取某个基因上游 2 kb 启动子”
- “提取基因上游 2 kb 到下游 500 bp 的片段”
- “提取 chr01:10000-12000 的序列”
- “按坐标和链方向批量提取 FASTA”

不要把本技能用于：

- 重新注释基因结构或预测新 CDS；这属于注释流程。
- CRISPR gRNA 设计；应使用 `sgrna-design` 或 `sgrna-design-diploid`。
- 共线性、同源基因查找或功能注释；使用对应专门技能。

## Scripts

本技能包含两个主要脚本：

### 1. `scripts/extract_fasta_records.py`

用于从已有 FASTA 文件中按 ID 提取记录，适合 CDS、蛋白、转录本或任意已存在 FASTA 记录。

典型命令：

```bash
python3 scripts/extract_fasta_records.py \
  --fasta input.cds.fa \
  --ids gene_ids.txt \
  --output selected.cds.fa \
  --report selected.cds.report.tsv \
  --missing selected.cds.missing.txt \
  --match-mode smart
```

也可以直接传入多个 ID：

```bash
python3 scripts/extract_fasta_records.py \
  --fasta input.pep.fa \
  --id Soltu.DM.01G000100.1 --id Soltu.DM.01G000200.1 \
  --output selected.pep.fa \
  --report selected.pep.report.tsv
```

`--match-mode`：

- `primary`：只匹配 FASTA header 的第一个非空白 token。
- `full`：匹配完整 header。
- `contains`：header 中包含查询 ID 即认为匹配；可能产生假阳性，谨慎使用。
- `smart`（默认）：匹配 primary token、完整 header，以及 header 中常见 `key=value` / `key:value` 属性，如 `gene=...`、`transcript_id=...`、`ID=...`、`Parent=...`。

### 2. `scripts/extract_genome_window.py`

用于从基因组 FASTA 中按坐标提取序列。输入坐标为 1-based closed interval，即 `[start, end]`。可单条提取，也可用 TSV 批量提取。

反向互补规则已考虑基因组中的不确定碱基和 IUPAC ambiguity codes：`N/n` 会保持为 `N/n`，`R/Y`、`K/M`、`B/V`、`D/H` 等会按 IUPAC 规则互补，大小写尽量保持一致。若 FASTA 中存在非 IUPAC 字符，Python `str.translate` 会原样保留；实际任务中应在报告或最终回复中提示用户检查这些字符。

单条坐标：

```bash
python3 scripts/extract_genome_window.py \
  --genome genome.fa \
  --seqid chr01 \
  --start 10000 \
  --end 12000 \
  --strand + \
  --name chr01_10000_12000_plus \
  --output region.fa
```

批量 TSV：

```bash
python3 scripts/extract_genome_window.py \
  --genome genome.fa \
  --regions regions.tsv \
  --output regions.fa \
  --report regions.report.tsv
```

`regions.tsv` 至少包含列：

```text
name	seqid	start	end	strand
region1	chr01	10000	12000	+
region2	chr02	50000	52000	-
```

可选列：`note`。如果没有 `strand` 列，默认 `+`。

## Workflow 1: CDS / 蛋白等确定性序列提取

1. 确认用户需要的是 CDS、蛋白、转录本还是其他现有 FASTA 记录。
2. 查找是否已有对应的 `cds.fa`、`pep.fa`、`protein.fa`、`transcript.fa` 等文件。
3. 如果找得到，直接用 `extract_fasta_records.py` 按 ID 提取。
4. 如果找不到 CDS/蛋白 FASTA：
   - 加载并执行 `gffread-export-cds-pep`。
   - 用 `genome.fa + annotation.gff3/gtf` 生成 `cds.fa` 和 `pep.fa`。
   - 再用 `extract_fasta_records.py` 提取目标 ID。
5. 校验输出条数、缺失 ID、重复/多匹配情况。

注意：如果用户给的是 gene ID，但 `cds.fa/pep.fa` 是 transcript ID，可能一个 gene 对应多个 transcript。此时应如实报告多个匹配；如果用户要求“每个基因一条”，优先使用代表转录本 FASTA，或明确选择最长 CDS / 主转录本规则。

## Workflow 2: 启动子序列提取

默认定义：从基因的 **ATG 上游**开始计算启动子长度，并按基因方向输出 5' -> 3' 序列。

### 坐标确定规则

先由智能体读取 GFF/GTF 中目标基因的坐标、链方向和 CDS 坐标：

- 正链基因（`+`）：
  - ATG 位置通常为该基因 CDS 的最小 start。
  - 长度为 `L` 的启动子坐标：`start = ATG - L`, `end = ATG - 1`, `strand = +`。
- 负链基因（`-`）：
  - ATG 位置通常为该基因 CDS 的最大 end。
  - 长度为 `L` 的启动子坐标：`start = ATG + 1`, `end = ATG + L`, `strand = -`。
  - 脚本会反向互补，使输出为该基因的 5' -> 3' 方向。

边界处理：

- 如果计算得到 `start < 1`，用 `--clip` 截断到 1，并在 report 中记录实际提取长度。
- 如果超过染色体长度，脚本同样在 `--clip` 模式下截断。
- 若 GFF 没有 CDS，只能退而使用 gene/mRNA 的 5' 端作为 TSS-like 近似，应在结果中明确注明“不是真正按 ATG 计算”。

### 提取命令示例

```bash
python3 scripts/extract_genome_window.py \
  --genome genome.fa \
  --seqid chr01 \
  --start 98001 \
  --end 100000 \
  --strand + \
  --name GeneA_promoter_2kb \
  --clip \
  --output GeneA.promoter.2kb.fa \
  --report GeneA.promoter.2kb.report.tsv
```

## Workflow 3: 基因上游/下游窗口或基因某部分片段

适用于用户要求“从某基因上游 2 kb 到下游 500 bp”“提取基因体加上下游区域”等。

先由智能体读取 GFF/GTF，获得：`seqid`、`gene_start`、`gene_end`、`strand`。

设用户要求：上游 `U` bp，下游 `D` bp。

- 正链基因（`+`）：
  - `start = gene_start - U`
  - `end = gene_end + D`
  - `strand = +`
- 负链基因（`-`）：
  - `start = gene_start - D`
  - `end = gene_end + U`
  - `strand = -`
  - 脚本反向互补输出，保证结果为基因 5' -> 3' 方向。

示例：负链基因上游 2000 bp 至下游 500 bp，若 gene 坐标为 `chr03:10000-15000:-`：

```text
start = 10000 - 500 = 9500
end   = 15000 + 2000 = 17000
strand = -
```

然后执行：

```bash
python3 scripts/extract_genome_window.py \
  --genome genome.fa \
  --seqid chr03 \
  --start 9500 \
  --end 17000 \
  --strand - \
  --name GeneB_up2k_down500 \
  --clip \
  --output GeneB.up2k_down500.fa \
  --report GeneB.up2k_down500.report.tsv
```

## Workflow 4: 任意基因组位置序列提取

如果用户指定染色体、起止位置或长度，直接构造坐标并用 `extract_genome_window.py` 提取。

- 用户未指定链方向时，默认 `strand = +`，即 forward strand。
- 用户指定反向链、reverse strand、minus strand 或希望反向互补时，使用 `strand = -`。
- 坐标采用 1-based closed interval；如果用户给的是 BED，需转换为 1-based closed：`start = bed_start + 1`, `end = bed_end`。

示例：

```bash
python3 scripts/extract_genome_window.py \
  --genome genome.fa \
  --seqid chr05 \
  --start 123456 \
  --end 124456 \
  --strand + \
  --name chr05_123456_124456_forward \
  --output chr05_123456_124456.fa
```

## Output and Reporting

每次任务建议至少生成：

- `*.fa`：提取出的 FASTA 序列。
- `*.report.tsv`：实际匹配/提取报告。
- `*.missing.txt`：按 ID 提取时未找到的 ID（如有）。

最终回复用户时应说明：

- 输入文件路径。
- 采用的提取规则（CDS/pep 直接提取、ATG 上游、gene 上下游、任意坐标等）。
- 输出文件路径。
- 成功提取的序列条数。
- 缺失、边界截断、多转录本/多匹配等情况。

## References

- `references/01-58-mads12-window-extraction.md`: worked example for extracting MADS12 alleles from the diploid 01-58 genome, including the negative-strand upstream/downstream coordinate rule and the per-haplotype FASTA extraction pattern when hap1/hap2 are stored separately.

## Common Pitfalls

1. **gene ID 与 transcript ID 混用**：CDS/pep FASTA header 常常是转录本 ID，而用户给的是 gene ID。遇到一个 gene 多个 transcript 时不要静默只选第一条，除非用户指定规则。
2. **启动子按 TSS 还是 ATG 计算**：本技能默认按 ATG 上游。若用户明确要求 TSS 上游，应改用 gene/mRNA 的 5' 端，并在报告中说明。
3. **负链方向错误**：负链启动子或上下游窗口必须反向互补后返回，才能保证 5' -> 3'。
4. **坐标体系混淆**：本技能脚本使用 1-based closed interval；BED 是 0-based half-open，必须转换。
5. **染色体名称不一致**：GFF 的 `seqid` 必须与 genome FASTA header 的 primary token 一致。
6. **越界坐标**：靠近染色体两端的启动子/上下游区域可能超出边界。默认建议使用 `--clip`，并在 report 中说明实际长度。
7. **缺少 CDS 时的 ATG 推断**：没有 CDS 信息就不能严格按 ATG 上游提取启动子；必须向用户说明退化规则或请求提供更完整注释。

## Verification Checklist

- [ ] 已确认输入 FASTA/GFF 路径存在且非空。
- [ ] 已确认提取类型：CDS/pep、启动子、基因相对窗口、任意坐标。
- [ ] 对 CDS/pep 任务，已优先使用已有 `cds.fa` / `pep.fa`；若无，已调用 `gffread-export-cds-pep` 生成。
- [ ] 对启动子任务，已说明默认按 ATG 上游计算；若缺 CDS，已说明 fallback。
- [ ] 对基因相对窗口，已按正负链分别计算坐标。
- [ ] 对负链结果，已通过脚本反向互补并按 5' -> 3' 输出。
- [ ] 已检查输出 FASTA 记录数、报告文件、缺失 ID 或边界截断情况。
