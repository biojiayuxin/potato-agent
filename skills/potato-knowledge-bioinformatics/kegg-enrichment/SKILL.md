---
name: kegg-enrichment
description: 针对马铃薯 DMv8.2、DMv8.1、DMv6.1 和 E4-63 基因组执行 KEGG pathway 富集分析；智能体读取输入前 10 个非空 ID 判断基因组版本，并调用 scripts/KEGG_enrichment.py 完成 ID 转代表转录本、KEGG 富集和图表输出。
version: 1.0.2
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, KEGG, enrichment, pathway, DMv8.2, DMv8.1, DMv6.1, E4-63, representative-transcript]
    related_skills: [go-enrichment, search-gene-function, transcriptome_analysis]
---

# KEGG 富集分析（DMv8.2 / DMv8.1 / DMv6.1 / E4-63）

## Overview

本技能用于对一组马铃薯 gene ID、代表转录本 ID 或 alternative transcript ID 执行 KEGG pathway enrichment。KEGG 背景 `term2gene.txt` 以**代表转录本 ID**为键，脚本会在内部把输入 ID 转换为代表转录本 ID 后再富集。

核心脚本：

```text
scripts/KEGG_enrichment.py
```

固定公共数据根目录：

```text
/mnt/data/public_data/GO_KEGG_data
```

输入文件和输出文件路径均由运行命令传入，不在技能中写死。

## When to Use

使用本技能当用户请求：

- KEGG 富集分析、KEGG enrichment、pathway enrichment、通路富集；
- 输入是马铃薯 gene ID、代表转录本 ID 或 alternative transcript ID；
- 目标基因组为 DMv8.2、DMv8.1、DMv6.1 或 E4-63。

不要用于 GO 富集；GO 使用 `go-enrichment`。

## 基因组版本判断

脚本不自动判断基因组版本。智能体在调用脚本前只读取输入文件**前 10 个非空 ID**，按前缀判断，并通过 `--genome` 显式传入。

| 基因组 | ID 前缀 | `--genome` | 数据子目录 |
|---|---|---|---|
| DMv8.2 | `DM8.2_` | `DMv8.2` | `DMv82` |
| DMv8.1 | `DM8C` | `DMv8.1` | `DMv81` |
| DMv6.1 | `Soltu.DM` | `DMv6.1` | `DMv61`、`DMv6.1`、`DMv6_1` |
| E4-63 | `St_E4-63` | `E4-63` | `E4-63`、`E4_63`、`St_E4-63` |

处理规则：

- 只匹配到一个基因组版本：继续运行；
- 匹配到多个版本：停止，提示用户拆分输入文件；
- 无法识别：停止，要求用户确认基因组版本或 ID 格式；
- 不要为了判断版本扫描完整输入文件。

## 必需数据文件

在公共数据根目录下需要：

```text
<genome_dir>/term2gene.txt             # KEGG_pathway_id<TAB>representative_transcript_id
shared_data/term2name.txt              # KEGG_pathway_id<TAB>pathway_name
```

ID 映射文件：

```text
<genome_dir>/GeneID_RepreID_AltID.tsv  # 若存在则优先使用
```

若没有映射文件，脚本会保守地使用 `term2gene.txt` 中的精确代表转录本 ID，并尝试唯一的去后缀 gene ID 映射；遇到歧义或未映射 ID 会报错，需用户提供明确的代表转录本 ID 或补充映射文件。E4-63 当前已有从 `E4-63.unified_ID.gff3` 生成的映射文件；该基因组每个 gene 只有一个 mRNA，但代表转录本后缀不一定是 `.1`。

## 运行流程

1. 准备一行一个 ID 的输入文件。
2. 根据前 10 个非空 ID 判断 `GENOME`。
3. 在本技能目录下运行脚本，或将 `scripts/KEGG_enrichment.py` 替换为本机解析到的脚本路径：

```bash
python3 scripts/KEGG_enrichment.py \
  --genome "$GENOME" \
  --input "$INPUT_IDS" \
  --output "$OUTPUT_TSV"
```

脚本会检查并在必要时尝试安装 Python 依赖：`pandas`、`matplotlib`、`numpy`、`scipy`、`statsmodels`。若自动安装失败，向用户报告缺失包和安装命令。

## 输出

若 `OUTPUT_TSV=results/input.KEGG_enrichment.tsv`，则输出：

```text
results/input.KEGG_enrichment.tsv
results/input.KEGG_enrichment.pdf
results/input.KEGG_enrichment.repreTransID.txt
```

TSV 主要列：`Pathway_ID`、`Description`、`FDR`、`p_value`、`Enrichment_Ratio`、`query_count (k)`、`query_total (n)`、`background_count (K)`、`background_total (N)`、`GeneRatio`、`BgRatio`、`genes`。

## 结果汇报

完成后检查 TSV、PDF 和 `.repreTransID.txt` 是否生成，并向用户汇报：

- 判断出的基因组版本；
- 输入 ID 数；
- 输出代表转录本 ID 数；
- KEGG 背景中有注释的 query ID 数；
- KEGG 结果行数；
- `FDR < 0.05` 的显著 pathway 数；
- 输出文件路径。

## Common Pitfalls

1. **把基因组判断交给脚本。** 必须由智能体先判断并传入 `--genome`。
2. **混合多个基因组版本。** 不同基因组的 KEGG 背景和 ID 映射不能混用。
3. **跳过 ID 转换。** KEGG 背景以代表转录本为键，gene ID 不能假定直接用于富集。
4. **误用 GO 文件。** KEGG 使用 `term2gene.txt` / `term2name.txt`，不要使用 `GO.txt` 或 `go-basic.obo`。
5. **假设代表转录本总是 `.1`。** 代表转录本以映射表或 KEGG 背景为准。
6. **只输出显著结果。** TSV 应保留所有有命中 pathway 的检验结果；PDF 展示显著项或无显著项提示。

## Verification Checklist

- [ ] 只读取前 10 个非空 ID 判断基因组版本。
- [ ] 脚本调用显式传入 `--genome`。
- [ ] 只调用 `scripts/KEGG_enrichment.py`。
- [ ] 必需公共数据文件存在。
- [ ] KEGG 富集 TSV、PDF、`.repreTransID.txt` 均生成。
- [ ] 汇报显著 pathway 数和输出路径。
