---
name: potato-spatial-expression
description: Query production Potato Agent spatial transcriptomics data and reproduce gene spatial-expression maps plus Interface cluster dotplots as PDF by default or optional PNG. Use for exact Soltu.DM.* gene IDs, spatial expression in stolon, stem, or early swelling tuber, cluster expression summaries, and requests to plot or compare spatial expression locations.
---

# Potato Spatial Expression

使用本技能查询马铃薯空间转录组，并默认生成 PDF 格式的两类结果：

- 基于真实细胞轮廓和逐细胞表达值的空间表达位置图。
- 与 Spatial Expression Interface 使用同一响应字段、色阶和点径规则的 Seurat cluster dotplot。

这是供 Hermes agent 使用的技能。权威数据源固定为生产站点 `https://potato-agent.ynnu.edu.cn/`；正常任务不要把 `--base-url` 改成本地 Interface、镜像或其他站点。脚本中的 `--base-url` 仅用于维护测试。

## Gene And Dataset Selection

要求使用空间数据集中的精确基因 ID，例如 `Soltu.DM.03G024100`。不要在本技能中猜测或自动把 `DM8C*` 转成 `Soltu.DM.*`；如果用户提供的不是空间基因 ID，先用其他可靠来源解析，无法唯一解析时再询问用户。

生产站点当前数据集的生物学含义：

- `s1_s2`：stolon (`S1`) 和 early swelling tuber (`S2`)。
- `s1_stem`：stolon (`S1`) 和 stem (`Stem`)。

按以下规则选择：

- 匍匐茎、stolon：优先 `--dataset s1_stem --sample S1`。
- 茎、stem：使用 `--dataset s1_stem --sample Stem`。
- 块茎、薯块、early swelling tuber、块茎发育早期：使用 `--dataset s1_s2 --sample S2`。
- 比较匍匐茎与早期膨大块茎：使用 `s1_s2`，同时指定 `--sample S1 --sample S2`。
- 比较匍匐茎与茎：使用 `s1_stem`，同时指定 `--sample S1 --sample Stem`。
- 用户明确给出 dataset/sample 时遵循用户选择。若需要核对线上目录，先运行 `datasets`。

## Commands

Hermes 加载技能时会将 `${HERMES_SKILL_DIR}` 展开为当前用户安装的技能绝对目录。使用内置脚本：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_spatial.py" datasets
```

查询聚合表达统计，不绘图：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_spatial.py" expression \
  Soltu.DM.03G024100 --dataset s1_s2
```

生成空间表达位置图和 dotplot：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_spatial.py" plot \
  Soltu.DM.03G024100 --dataset s1_s2 --sample S2 \
  --outdir "$PWD/spatial_plots"
```

省略 `--sample` 会为数据集中的每个样本分别生成空间图。可重复 `--sample` 生成比较所需的多个样本图。PDF 是默认格式；只有用户明确要求 PNG 时才添加 `--format png`。输出过密时可以用 `--width` 增大渲染宽度；默认宽度为 2000 px。

可选 PNG 输出：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_spatial.py" plot \
  Soltu.DM.03G024100 --dataset s1_s2 --sample S2 \
  --format png --outdir "$PWD/spatial_plots"
```

仅生成 Interface cluster dotplot：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_spatial.py" dotplot \
  Soltu.DM.03G024100 --dataset s1_s2 \
  --outdir "$PWD/spatial_plots"
```

## Interface Reproduction Contract

`plot` 直接读取生产 Interface 使用的数据：

- `/api/spatial/gene`：每个样本的稀疏逐细胞表达值和跨样本统一的 `vmin/vmax`。
- `/api/spatial/dotplot`：`avgExprScaled`、`pctExpr`、cluster 顺序和名称。
- `/api/spatial/replicates` 与 contour manifests/tiles：真实细胞轮廓、replicate 分面和页面布局。

空间图使用 Interface 的九级 Reds 色阶。表达表中缺失的已分配细胞按 0 表达着色；所有样本共享 `/api/spatial/gene` 返回的全数据集表达范围，因此样本间颜色可直接比较。每个 replicate 按 Interface 的 bbox 归一化、列数和间距布局。

Dotplot 与页面一样：颜色使用各 cluster 的 `avgExprScaled` 范围，点半径使用当前 cluster 中 `pctExpr` 的最小值到最大值缩放。Dotplot 是 dataset-level 结果，不随 `S1`、`S2` 或 `Stem` 样本切换而改变；不要把 agent 聚合接口的 sample 行画成额外的 dotplot 轴。

## Outputs

`plot` 输出：

```text
<gene>_<dataset>_<sample>_spatial_expression.pdf
<gene>_<dataset>_cluster_dotplot.pdf
<gene>_<dataset>_cluster_dotplot.tsv
<gene>_<dataset>_cluster_dotplot.json
<gene>_<dataset>_interface_data.json
```

指定 `--format png` 时，两个绘图文件扩展名改为 `.png`，其余数据文件不变。默认 PDF 是原生矢量图：细胞轮廓、dotplot 圆点、色标和文字均直接写成 PDF path/text 对象，不嵌入 PNG 或其他栅格图片，可在 Illustrator、Inkscape 等软件中无损缩放和继续编辑。PNG 只用于用户明确要求的栅格图片场景。

不要先生成 PNG 再转换或插入 PDF。正常 PDF 输出不依赖 Pillow；只有 `--format png` 才使用 Pillow。

`interface_data.json` 保存本次生产查询的 dataset 元数据、逐细胞稀疏表达响应、dotplot 响应、来源地址和 UTC 获取时间，用于审计图像。TSV 包含 cluster 名称、细胞数、表达细胞数、`pct_expr`、`avg_expr` 和 `avg_expr_scaled`。

绘图成功后，向用户返回每个 PDF（或明确请求的 PNG）、TSV 和 provenance JSON 的完整绝对路径，并说明使用的 `dataset / sample`。不要只给目录或相对路径。若用户只要求图，可重点返回绘图文件，但仍保留并指出数据文件。

## Interpretation And Errors

汇总表达时报告 `cellCount`、`expressingCount`、`pctExpr` 和 `avgExpr`。说明稀疏表达表中不存在的已分配细胞计入分母并按 0 表达处理。

不要把空间位置或 cluster 富集表述为调控、谱系或因果证据。若生产 API 返回 `404`，说明 dataset 或精确 gene 不存在；若连接或 contour 下载失败，报告具体错误，不要用本地数据、其他数据集或推测结果替代。
