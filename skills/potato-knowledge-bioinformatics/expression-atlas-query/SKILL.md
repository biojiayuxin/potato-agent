---
name: expression-atlas-query
description: 通过 Potato Agent Bulk RNA-Seq API 查询和绘制马铃薯 DMv8.2 基因表达，支持按材料-组织、组织、材料或原始 run 分组，读取 TPM、log2(TPM+1) 或行 z-score，并生成与 Bulk RNA-Seq 页面一致的可编辑矢量 PDF 热图。适用于查询、比较、导出或绘制一个或多个 DMv8.2 基因的 bulk RNA-Seq 表达。
version: 2.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [expression-atlas, bulk-rnaseq, gene-expression, TPM, API, heatmap, vector-PDF, potato]
    related_skills: [potato-gene-search, potato-spatial-expression, transcriptome_analysis]
prerequisites:
  commands: [python3]
---

# Expression Atlas Query

通过 Potato Agent 的只读 Bulk RNA-Seq API 查询已导入数据库的 DMv8.2 基因表达量。不要直接扫描本地表达矩阵，也不要把网页 HTML 当作表达数据源。

- 页面：`https://potato-agent.ynnu.edu.cn/bulk-rnaseq`
- API 根地址：`https://potato-agent.ynnu.edu.cn/api/bulk-rnaseq`
- 可用环境变量：`POTATO_BULK_RNASEQ_BASE_URL`

## 何时使用

- 查询一个或多个 DMv8.2 基因在 bulk RNA-Seq 数据中的表达量。
- 比较组织、材料、材料-组织组合或原始 runs 的表达。
- 读取与 Bulk RNA-Seq 页面一致的 scale，或导出完整表达表。
- 输入是 symbol、reported ID 或历史 ID 时，先用 `potato-gene-search` 得到准确 DMv8.2 gene ID。

不要用本技能处理原始 FASTQ、重新比对或定量；这些任务使用 `transcriptome_analysis`。空间转录组的 cluster/tissue dotplot 使用 `potato-spatial-expression`。

## 基本原则

1. 优先使用内置脚本，不要自行拼接或解析网页。
2. 表达接口参数必须使用准确的 **DMv8.2 gene ID**，格式如 `DM8.2_chr06G22780`。不要把转录本 ID、DMv8.1 `DM8C*` 或 DMv6.1 `Soltu.DM.*` 直接作为正式查询参数；先用 `potato-gene-search` 解析到 DMv8.2 gene ID，或用本技能的 `search` 命令确认。
3. 查询结果必须说明数据集、grouping 和 scale。区分变换后的 `value` 与原始/分组平均 `mean_tpm`。
4. 默认仅展示每个基因表达最高的 10 行；用户要求完整结果时使用 `--output-tsv`，不要在对话中打印数百行。
5. API 不可用、返回 404 或字段异常时，报告实际错误；不要回退到旧的本地矩阵，也不要猜测表达量。
6. 用户要求绘图时使用 `plot_expression_atlas.py` 生成 `.pdf`；不要改为 PNG/JPEG，也不要在 PDF 中嵌入栅格图。

## 查询参数

| 参数 | 要求 |
|---|---|
| `genes` | 一个或多个准确 DMv8.2 gene IDs；标准示例：`DM8.2_chr06G22780`；最多 50 个 |
| `scope` / `grouping` | `sample_tissue`、`tissue`、`sample_name` 或 `sample` |
| `transform` / `scale` | `log2_tpm`、`row_zscore` 或 `tpm` |

后续命令示例统一使用 `DM8.2_chr06G22780`，供智能体识别正确的 DMv8.2 ID 形式。

## Grouping 与 Scale

| 页面选项 | API `scope` | 含义 |
|---|---|---|
| Material by tissue | `sample_tissue` | 同一材料和组织的 runs 取平均，默认 |
| Tissue mean | `tissue` | 同一组织的 runs 取平均 |
| Material mean | `sample_name` | 同一材料的 runs 取平均 |
| All runs | `sample` | 保留每个原始 run |

| 页面选项 | API `transform` | 含义 |
|---|---|---|
| log2(TPM + 1) | `log2_tpm` | 对分组平均 TPM 做 `log2(TPM + 1)`，默认 |
| Row z-score | `row_zscore` | 先做 `log2(TPM + 1)`，再对每个基因跨当前列计算样本标准差 z-score |
| TPM | `tpm` | 未变换的 TPM 或分组平均 TPM |

脚本同时接受 `--grouping` 作为 `--scope` 的别名，以及 `--scale` 作为 `--transform` 的别名。

## 推荐脚本

```bash
SKILL_DIR="${SKILL_DIR:?set SKILL_DIR to the expression-atlas-query skill directory}"
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" --help
```

### 1. 检查 API 与当前数据集

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" status
```

读取 `configured`、`dataset`、基因数、run 数和各 grouping 的列数。状态接口成功后再查询表达量。

### 2. 搜索基因

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" search DM8.2_chr06G22780 --limit 10
```

`search` 是包含匹配，仅用于确认 API 中的准确 gene ID。表达接口使用准确 ID。

### 3. 查询表达量

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr06G22780

python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr06G22780 \
  --scope tissue --transform tpm --top 15

python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query \
  DM8.2_chr06G22780 DM8.2_chr01G00010 \
  --grouping sample_name --scale row_zscore
```

一次最多查询 50 个 DMv8.2 gene IDs。脚本为兼容旧输入会尝试规范化 `DM8C*` 或去除末尾转录本后缀，但智能体调用时仍必须优先提供已经确认的标准 DMv8.2 gene ID。

### 4. 限定组织或材料

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr06G22780 \
  --scope sample_tissue --transform tpm --tissue root

python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr06G22780 \
  --scope sample_tissue --sample-name PG6359
```

`--tissue` 和 `--sample-name` 在 API 返回后按名称精确过滤，可重复指定。只对包含相应元数据的列使用这些过滤条件。

### 5. 导出完整表达表

```bash
python3 "$SKILL_DIR/scripts/query_expression_atlas.py" query DM8.2_chr06G22780 \
  --scope sample --transform tpm \
  --output-tsv /tmp/DM8.2_chr06G22780.bulk_expression.tsv
```

TSV 包含 gene、列/分组、材料、组织、变换值、平均 TPM、SD 和重复数。JSON 中的 `rows` 受 `--top` 限制，但 TSV 始终包含过滤后的全部行。

### 6. 绘制与页面一致的矢量 PDF

```bash
python3 "$SKILL_DIR/scripts/plot_expression_atlas.py" DM8.2_chr06G22780 \
  --scope sample_tissue --transform log2_tpm \
  --output /tmp/DM8.2_chr06G22780.sample_tissue.log2_tpm.pdf

python3 "$SKILL_DIR/scripts/plot_expression_atlas.py" \
  DM8.2_chr06G22780 DM8.2_chr01G00010 \
  --scope tissue --transform row_zscore \
  --output /tmp/two_genes.tissue.row_zscore.pdf
```

绘图脚本直接读取同一表达 API，并复刻 Interface 页面逻辑：

- 单基因且 `scope=sample_tissue`：绘制“材料 × 组织”透视热图；
- 其他 grouping 或多基因：绘制“基因 × grouping 列”矩阵热图；
- `log2_tpm` / `tpm`：使用 `#f8fafc → #ff0000`；
- `row_zscore`：使用对称范围的 `#0000ff → #f8fafc → #ff0000`；
- 保留页面的标题、grouping/scale summary、标签、网格、缺失格和图例布局。

输出必须是 `.pdf`。脚本以 PDF 矢量矩形、线条和文本构图，不生成 PNG/JPEG，也不在 PDF 中嵌入栅格图片。默认宽度为 1280 pt；需要调整画布时使用 `--width` 和 `--stage-height`，不要修改颜色和数值范围算法。

## 输出解读

- `value`：用户选择 scale 后用于页面热图的值。
- `mean_tpm`：该列的未变换 TPM；分组模式下为组内平均 TPM。
- `sd_tpm`：组内 TPM 标准差；`sample` 模式为 0。
- `n`：参与该列统计的 runs 数；`sample` 模式为 1。
- `apiSummary`：API 对全部返回列的统计。
- `selectedSummary`：应用客户端组织/材料过滤后的统计。

数据库保存 gene-level 表达。若同一个 gene ID 对应多个转录本，API 返回 `transcriptCount`，表达向量是导入数据库时聚合后的 gene-level TPM；不要把它描述成某一个转录本的独立表达。

## 故障处理

- `HTTP 404`：基因不在当前数据集；先运行 `search` 检查准确 ID。
- `HTTP 400`：检查 gene 数量、`scope` 和 `transform`。
- 连接失败或 `HTTP 5xx`：说明线上 API 当前不可用，并保留错误信息。
- 需要切换部署地址时，在命令后使用 `--base-url`，可传站点根地址、`/bulk-rnaseq` 页面地址或 `/api/bulk-rnaseq` API 根地址。
