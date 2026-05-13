---
name: expression-atlas-query
version: 1.0.0
description: 基于公共数据目录 /mnt/data/public_data/Expression_atlas 查询基因/转录本表达量；自动发现表达矩阵与样本-组织元数据，支持按 tissue、sample_name 汇总 TPM/FPKM/count 等表达值。适用于用户询问某个基因在不同组织、样本或品种中的表达量。
metadata:
  hermes:
    tags: [expression-atlas, gene-expression, TPM, RNA-seq, potato, local-data]
    related_skills: [potato-gene-search, transcriptome_analysis]
prerequisites:
  commands: [python3]
---

# Expression Atlas Query

基于公共数据目录中的本地表达矩阵查询基因/转录本表达量。默认数据根目录：

```text
/mnt/data/public_data/Expression_atlas
```

当前已知数据集：

```text
/mnt/data/public_data/Expression_atlas/DMv8.2/
├── transcript_tpm_matrix_merged.tsv   # transcript_id, gene_id, gene_name + sample columns
└── sample_tissue_list.tsv             # sample_column, sample_name, tissue
```

## 何时使用

- 用户询问“某基因表达量”“TPM”“在哪些组织表达较高/较低”。
- 用户给出马铃薯 DMv8 gene ID / transcript ID，如 `DM8.2_chr01G00010`、`DM8.2_chr01G00010.1`、`DM8C01G00010`。
- 用户给出 gene symbol、reported ID 或历史 ID 时，先用 `potato-gene-search` 解析到 DMv8 gene ID，再用本技能查表达。
- 用户询问公共数据目录 `Expression_atlas` 中有哪些表达数据集、组织、样本。

## 基本原则

1. **优先查本地数据**：不要用网络检索替代 `/mnt/data/public_data/Expression_atlas`。
2. **先解析 ID**：如果输入不是明确 DMv8 gene/transcript ID，应先用 `potato-gene-search` 找到候选 `gene_id`。
3. **默认按组织汇总**：查询单个基因时，默认给 tissue 层面的 mean/median/max/min/nonzero 汇总，并列出高表达组织。
4. **保留单位与数据集**：回答中必须说明数据集、矩阵文件和表达单位（如 TPM）。
5. **避免输出过长表格**：除非用户要求完整明细，默认只展示 top 10 左右的组织/样本汇总。
6. **未来数据兼容**：新增数据集可放在 `Expression_atlas/<dataset>/` 下，只要矩阵为 TSV/CSV，且前几列包含 `gene_id` 或 `transcript_id`，脚本会尝试自动发现。

## 推荐脚本

技能脚本路径：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/expression-atlas-query
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" --help
```

### 1. 查看可用数据集

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" list-datasets
```

### 2. 查看某数据集组织/样本概况

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" list-tissues --dataset DMv8.2
```

### 3. 查询单个基因，默认按组织汇总

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010 --dataset DMv8.2 --summary tissue --top 10
```

`DM8C01G00010` 会自动匹配到 `DM8.2_chr01G00010` 风格 ID。

### 4. 查询转录本

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010.1 --dataset DMv8.2 --summary tissue
```

### 5. 限定组织或品种/样本名

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010 --dataset DMv8.2 --tissue leaf --summary sample_name
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010 --dataset DMv8.2 --sample-name PG6359 --summary tissue
```

### 6. 导出长表明细

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010 \
  --dataset DMv8.2 \
  --summary tissue \
  --output-tsv /tmp/DM8.2_chr01G00010.expression.long.tsv
```

导出 TSV 字段通常包括：`dataset, matrix, unit, transcript_id, gene_id, gene_name, sample_column, sample_name, tissue, value`。

## 输出解读

脚本默认输出 JSON，关键字段：

- `query`：用户查询词。
- `datasets`：命中的数据集结果。
- `unit`：从矩阵文件名推断的表达单位，如 `TPM`、`FPKM`、`count`。
- `matched_features`：命中的转录本/基因行。
- `summary`：按 `tissue` 或 `sample_name` 汇总后的表达统计。
- `n_values`：用于汇总的样本数。
- `nonzero_count` / `nonzero_fraction`：非零表达样本数与比例。

## 面向用户的回答格式

中文简洁回答，建议结构：

```text
已在 Expression_atlas/DMv8.2 的 transcript_tpm_matrix_merged.tsv 中查询 <gene>（单位：TPM）。

按组织汇总，表达最高的组织为：
1. <tissue>: mean TPM=..., median=..., n=...
2. ...

说明：该矩阵为转录本 TPM 矩阵；若一个 gene_id 对应多个转录本，结果会按命中的转录本分别统计/必要时说明是否合并。
```

若无命中：

- 说明已查的数据集和矩阵；
- 如果输入像 symbol，建议先用 `potato-gene-search` 解析；
- 如果输入是 DMv8C 风格，说明脚本已尝试 DM8C 与 DM8.2_chr 风格互转。

## 注意事项与陷阱

- 当前矩阵是 **transcript-level TPM**，`gene_id` 相同但不同 `transcript_id` 可能有多行；不要误称为唯一 gene-level 表达，除非已明确聚合。
- `sample_tissue_list.tsv` 中的 `sample_column` 必须与矩阵样本列一致；如果新增数据集缺少该文件，脚本仍可按样本列查询，但无法按 tissue 汇总。
- `gene_name` 可能为空，不能依赖它做主要匹配。
- 大矩阵查询采用逐行扫描，适合少量基因查询；如果用户要求批量上千基因查询，应另行生成索引或写批量脚本。
- 回答必须说明表达量来自公共目录的本地表达矩阵，而不是实时重新比对/定量。

## 验证命令

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" list-datasets
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" list-tissues --dataset DMv8.2
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr01G00010 --dataset DMv8.2 --summary tissue --top 5
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8C01G00010 --dataset DMv8.2 --summary tissue --top 5
```
