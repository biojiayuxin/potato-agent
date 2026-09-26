---
name: potato-efp-expression
description: 查询 Potato Interface 的 Tissue Expression Map API，并导出马铃薯基因的组织表达模式图或 eFP 表达图为可编辑矢量 PDF。用户要求画某个基因在不同组织中的表达示意图、整株组织表达图或 eFP 图时使用；表达矩阵热图用 expression-atlas-query，细胞空间表达图用 potato-spatial-expression。
metadata:
  hermes:
    tags: [potato, efp, tissue-expression, gene-expression, vector-pdf]
    related_skills: [potato-gene-search, expression-atlas-query, potato-spatial-expression]
---

# Potato eFP Expression

通过 Interface 的只读 API 获取真实组织表达和矢量 PDF。技能仅发送 HTTP 请求；组织映射、色阶、布局和 PDF 绘制由服务端负责，与 `/efp` 页面共用代码。脚本只需 Python 3 标准库，无需登录、API key、浏览器或绘图库。

## 基因与尺度

- 使用准确的 DMv8.2 gene ID，例如 `DM8.2_chr05G25210`。输入为基因名、旧版本 ID 或转录本 ID 时，先用下面的 `search` 或 `potato-gene-search` 确认；多个候选无法唯一确定时再询问用户，不自动取第一条。
- 每张图一个基因；多个基因逐个调用。
- 默认 `log2_tpm`（log2(TPM+1)）；用户指定原始 TPM 时用 `tpm`，指定 Z-score 时用 `row_zscore`。
- 数据固定为 `scope=tissue`，即跨材料的组织平均表达。需要特定材料或表达热图时使用 `expression-atlas-query`。

## 调用

Hermes 会将 `${HERMES_SKILL_DIR}` 展开为技能安装目录。

获取图形和表达数据来源（每次绘图任务调用一次；回答来源问题时也使用）：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_efp.py" source
```

查找准确 ID（已知 ID 时可跳过）：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_efp.py" search DM8.2_chr05G25210
```

查询页面信息，用于说明实际表达模式：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_efp.py" query DM8.2_chr05G25210
```

导出 PDF：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_efp.py" plot DM8.2_chr05G25210 \
  --output "$PWD/efp_plots/DM8.2_chr05G25210_efp_log2_tpm.pdf"
```

查询和导出使用相同的 `--transform`。例如：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_efp.py" plot DM8.2_chr05G25210 \
  --transform row_zscore --output "$PWD/efp_plots/DM8.2_chr05G25210_efp_row_zscore.pdf"
```

未配置时默认站点为 `https://potato-agent.ynnu.edu.cn`。部署人员可在技能根目录放置
`api-base-url.txt`，其中只写实际站点根地址；安装了此配置后，上述命令会自动使用该地址。
也可用 `POTATO_EFP_BASE_URL` 或 `--base-url` 覆盖。优先级为：命令行、环境变量、
安装配置、默认站点。本机地址应以实际监听地址为准，不假设一定监听 `127.0.0.1`。
不读取服务器数据库。

## API 与结果

| 功能 | GET 接口 |
| --- | --- |
| 来源说明 | `/api/efp/source`，不需要基因参数 |
| 搜索 | `/api/efp/genes?q=...&limit=20` |
| 查询 | `/api/efp/expression?gene=...&transform=log2_tpm` |
| PDF | `/api/efp/export.pdf?gene=...&transform=log2_tpm` |

查询结果的 `tissueValues` 是页面显示的组织列表，包含 `rawTpm`、变换后的 `value` 和 `diagramIds`；`regions` 包含图中每个区域的颜色和缺失状态。`dataset`、`scope`、`transform` 说明数据来源和尺度。

PDF 包含整株图、基因 ID、色标、NA 说明和组织表达表，并内嵌相同的表达元数据。成功后返回脚本输出的 **PDF 完整绝对路径**，提供可下载的文件链接，并简要说明基因、组织平均表达和尺度。不要只返回网页链接或输出目录。

交付 PDF 时附上 `source` 返回的 `description`：图从 **Tissue Expression Map（eFP）** 页面导出，数据使用 **Gene Expression** 页面 **Tissue mean（组织平均表达）** 视图的平均组织表达水平。来源接口同时返回 `figure.page` 和 `data.page`，页面链接按本次 API 站点解析；`plot` 结果的 `source_url` 可直接查询这段说明。不要将变换后的配色值误称为原始平均 TPM。

- 交付时保留来源说明中的颜色含义：灰色表示“该图形区域缺少可映射的组织表达数据”，白色表示“示意图未着色部分或背景，不参与色阶比较。”
- 灰色为 NA；零表达是有效数值。未映射组织仍保留在表中。
- Z-score 是全图谱组织的 log2(TPM+1) 值经样本标准差标准化；色阶也覆盖全部图谱组织。不要仅对图上可映射组织重新计算。
- 遵循 API 的映射，包括块茎阶段、独立 stolon tip 的优先级，以及页面对 stamen 的省略；不自行合并或猜测组织。
- 表达富集只能描述组织表达模式，不能单独证明基因功能或调控因果关系。

HTTP 404 表示基因不在数据集中；400/422 表示参数错误；503 表示数据或绘图服务不可用（若提示 busy，可稍后重试一次）；504 表示导出超时。报告真实错误，不用模拟数据、截图或栅格 PDF 替代。脚本默认不覆盖已有文件，重跑时换一个输出文件名。
