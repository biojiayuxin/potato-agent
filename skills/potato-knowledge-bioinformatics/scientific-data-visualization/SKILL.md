---
name: scientific-data-visualization
description: "Use when turning scientific or experimental data into publication-ready vector PDF figures, including boxplots and violin plots with a two-sided Student's t-test, numeric-matrix heatmaps, and 2–4 set Venn diagrams. The final user-facing figure is PDF."
version: 2.0.0
author: Potato Agent
license: MIT
platforms: [linux]
prerequisites:
  commands: [python3, Rscript, pdfinfo, pdffonts, pdfimages, pdftoppm]
metadata:
  hermes:
    tags: [scientific-plotting, data-visualization, publication-figures, statistics, boxplot, violin-plot, heatmap, venn-diagram]
    category: potato-knowledge-bioinformatics
---

# 科研绘图（Scientific Data Visualization）

## 目标

将科研数据转换为可复现、适合论文或报告使用的图形。该技能是一个可持续扩展的科研绘图库：当前提供 Excel 箱线图和小提琴图、数值矩阵热图，以及支持 2–4 组数据的韦恩图脚本；后续可继续在 `scripts/` 中加入散点图、柱状图、折线图、火山图及其他绘图脚本。

## 目录约定

- `scripts/`：可直接运行的绘图脚本。
- `references/`：脱敏或合成的示例数据、配置文件和格式说明。

Hermes 加载本文件时会将 `${HERMES_SKILL_DIR}` 展开为当前技能的绝对目录。使用该变量定位脚本和示例输入；将生成的 PDF、TSV 和可选预览写入用户当前工作目录，不要写回技能目录。

## 运行依赖

- 箱线图和小提琴图：Python 3，以及 `matplotlib`、`numpy`、`pandas`、`scipy`、`openpyxl`。
- 热图：Python 3，以及 `matplotlib`、`numpy`、`pandas`。
- 韦恩图：R、R 包 `VennDiagram` 和 `futile.logger`，以及 R 的 Cairo 图形支持。
- PDF 验证和临时预览：Poppler 命令 `pdfinfo`、`pdffonts`、`pdfimages`、`pdftoppm`。

## 最终交付格式

- **最终返回给用户的科研图必须是 PDF 矢量图，不是 PNG 图。**
- PNG 只能作为内部视觉核对、快速预览或用户明确额外要求的辅助文件，不能用 PNG 替代最终 PDF。
- **除非用户明确要求，否则最终回复无需提供、附加或列出 PNG 图。**
- 导出 PDF 后应检查页数、页面尺寸、字体、裁切和可打开性；如需视觉核对，可临时渲染 PNG，但不将该预览图作为默认交付物。

## 绘图脚本

### 1. `scripts/boxplot_from_excel.py`

**用途**

从 Excel 工作簿读取一个或多个工作表，为每个工作表绘制箱线图面板，并组合为单个正式矢量 PDF。

**输入**

- Excel 工作簿：每个工作表对应一个面板，每列对应一个实验组；空值和非数值单元格会被忽略。
- JSON 配置：设置工作表、面板标题、Y 轴名称、颜色和布局。

**主要功能**

- 绘制箱线图并叠加固定随机种子的抖动散点。
- 固定对两个独立组执行双侧 Student's t-test（`equal_var=True`），与常见 Excel 等方差双样本 t 检验一致。
- 两组比较时绘制括号并标注脚本从当前输入实时计算的 P 值。
- 自动创建输出目录，并生成可追溯的统计摘要。

**输出**

- 正式矢量 PDF。
- 可选 PNG 预览。
- TSV 格式的描述统计与检验结果。

**限制**

- 统计标注仅支持每个工作表恰好两组，且每组至少两个有限数值。

**Hermes 用法**

```bash
mkdir -p outputs
python3 "${HERMES_SKILL_DIR}/scripts/boxplot_from_excel.py" \
  "${HERMES_SKILL_DIR}/references/example_boxplot_data.xlsx" \
  outputs/example_boxplots.pdf \
  --config "${HERMES_SKILL_DIR}/references/example_boxplot_config.json" \
  --stats-file outputs/example_boxplots_stats.tsv \
  --preview outputs/example_boxplots_preview.png
```

### 2. `scripts/violin_from_excel.py`

**用途**

从 Excel 工作簿读取一个或多个工作表，为每个工作表绘制小提琴图面板，并组合为单个正式矢量 PDF。该脚本复用箱线图脚本的参考数据和 JSON 配置。

**输入**

- Excel 工作簿：格式与箱线图脚本相同，每个工作表对应一个面板，每列对应一个实验组；空值和非数值单元格会被忽略。
- JSON 配置：与箱线图脚本共用，用于设置工作表、面板标题、Y 轴名称、颜色和布局。

**主要功能**

- 绘制核密度小提琴图，在每个小提琴内部叠加窄箱线图，并可叠加固定随机种子的抖动散点。
- 直接复用箱线图配置中的组顺序和颜色，使两种图形的视觉编码一致。
- 固定对两个独立组执行双侧 Student's t-test（`equal_var=True`），并实时计算、绘制 P 值。
- 自动创建输出目录，并生成与箱线图脚本字段一致的统计摘要。

**输出**

- 正式矢量 PDF。
- 可选 PNG 预览。
- TSV 格式的描述统计与检验结果。

**限制**

- 小提琴核密度估计要求每组至少包含两个非恒定数值；不满足条件时脚本会报错。
- 统计标注仅支持每个工作表恰好两组。

**Hermes 用法**

```bash
mkdir -p outputs
python3 "${HERMES_SKILL_DIR}/scripts/violin_from_excel.py" \
  "${HERMES_SKILL_DIR}/references/example_boxplot_data.xlsx" \
  outputs/example_violinplots.pdf \
  --config "${HERMES_SKILL_DIR}/references/example_boxplot_config.json" \
  --stats-file outputs/example_violinplots_stats.tsv \
  --preview outputs/example_violinplots_preview.png
```

### 3. `scripts/venn_from_tsv.R`

**用途**

从长表或宽表 TSV 读取 2–4 组条目集合，绘制韦恩图，并生成正式矢量 PDF。

**输入**

- 宽表 TSV：每列对应一个集合，空单元格会被忽略。
- 长表 TSV：每行对应一个组名与条目 ID 的配对，可用 `--group-column` 和 `--item-column` 指定列名。
- `--groups`：指定并排序需要绘制的 2–4 个集合；组内重复条目会自动去重。

**主要功能**

- 区域数字表示互斥区域（exclusive region）的精确条目数。
- 默认采用柔和的红、绿、蓝、黄顺序配色：2 组使用红/绿，3 组增加蓝色，4 组再增加黄色。
- 2 组和 3 组图采用紧凑标题布局；4 组图使用四个长短轴完全一致的全等椭圆。
- 自动核对并集大小、集合大小和全部互斥区域计数。

**输出**

- 正式矢量 PDF。
- 可选 PNG 预览。
- TSV 格式的互斥区域计数、membership code 和条目 ID 清单。

**限制**

- 仅接受 2–4 个非空集合。
- 椭圆面积不表示集合大小，图形用于展示集合归属和精确区域计数。

**Hermes 用法**

```bash
mkdir -p outputs
Rscript "${HERMES_SKILL_DIR}/scripts/venn_from_tsv.R" \
  --input "${HERMES_SKILL_DIR}/references/example_venn_genes.tsv" \
  --output outputs/example_venn_4sets.pdf \
  --groups Leaf_DEGs,Root_DEGs,Tuber_DEGs,Stolon_DEGs \
  --title "Four-set synthetic gene overlap" \
  --summary outputs/example_venn_4sets_regions.tsv \
  --preview outputs/example_venn_4sets_preview.png
```

2 组和 3 组的示例参数见 `references/example_venn_data_notes.md`。

### 4. `scripts/heatmap_from_tsv.py`

**用途**

从首列为行标签、其余列为数值的 TSV 矩阵绘制出版级热图，并生成正式矢量 PDF。

**输入**

- TSV 数值矩阵：首行为列标签，首列为行标签，其余单元格必须为有限数值。
- 命令行参数：可设置标题、坐标轴名称、色标名称、预期行列数、数值精度、色阶中心，并可关闭单元格注释或选择其他 Matplotlib 色图。

**主要功能**

- 默认色图使用纯蓝 `#0000FF`—白色—正红 `#FF0000` 渐变：当前输入的全局最小值映射为纯蓝色，全局最大值映射为正红色。
- 支持检查预期行列数、设置科学上有意义的色阶中心、调整标题和轴标签、控制数值精度及关闭单元格注释。
- 根据矩阵行列数估算基础画布尺寸，并根据背景亮度自动选择黑色或白色注释文字。
- 主热图使用非栅格化 `pcolormesh`；同时显式关闭 Matplotlib 色标的自动栅格化，并消除部分 PDF 查看器中的色标细缝。

**输出**

- 正式矢量 PDF。
- 标准输出中的矩阵行列数、实际极值和所用色图摘要。
- 如需 PNG，只将正式 PDF 临时渲染为内部视觉核对预览，不以 PNG 替代最终交付物。

**限制**

- `references/example_heatmap_data.tsv` 的 8 × 4 矩阵只是紧凑的参考示例，不是固定输入规格。
- 面对用户的实际数据，必须先检查矩阵规模和科学含义，再按需微调预期维度、画布尺寸、标签旋转、注释开关与精度、色阶中心、标准化方式以及行列排序或聚类；不得把 8 × 4 示例参数机械套用于其他数据。
- 默认脚本不擅自执行标准化、变换、聚类或重排行列；这些步骤必须依据用户数据和研究目的明确决定。

**Hermes 用法**

```bash
mkdir -p outputs
python3 "${HERMES_SKILL_DIR}/scripts/heatmap_from_tsv.py" \
  "${HERMES_SKILL_DIR}/references/example_heatmap_data.tsv" \
  outputs/example_heatmap.pdf \
  --expected-rows 8 \
  --expected-cols 4 \
  --title "Synthetic 8 × 4 Heatmap" \
  --xlabel Conditions \
  --ylabel Samples
```

该命令仅用于运行 8 × 4 合成示例；处理用户数据时应根据当前数据检查结果调整参数或微调脚本。示例数据、实现要点和完全矢量 PDF 的验证方法见 `references/example_heatmap_data_notes.md`。

## 示例数据

### 1. 箱线图与小提琴图示例

- 共用数据：`references/example_boxplot_data.xlsx`
- 共用配置：`references/example_boxplot_config.json`
- 说明：`references/example_boxplot_data_notes.md`

箱线图和小提琴图复用同一工作簿与配置，以保持面板、组顺序、配色和双侧 Student's t-test 一致。示例材料均为合成、脱敏数据，仅用于演示输入结构、统计流程和绘图命令，不应解释为真实生物学结果。

### 2. 韦恩图示例

- 数据：`references/example_venn_genes.tsv`
- 说明：`references/example_venn_data_notes.md`

示例表包含四组合成基因号，所有 ID 均以 `SYN_` 开头，并为四组集合的 15 个非空 membership 区域提供可确定核对的条目。

### 3. 热图示例

- 数据：`references/example_heatmap_data.tsv`
- 说明：`references/example_heatmap_data_notes.md`

示例表为 8 × 4 合成数值矩阵，仅用于演示输入结构、纯蓝 `#0000FF` 最低值、正红 `#FF0000` 最高值、单元格注释和矢量 PDF 验证，不代表真实实验结果，也不构成实际热图的固定尺寸或版式模板。实际绘图必须根据用户矩阵的规模、标签长度和分析目的微调参数或脚本。

## 输出验证

在用户工作目录检查正式 PDF：

```bash
pdfinfo outputs/example_boxplots.pdf
pdfinfo outputs/example_violinplots.pdf
pdfinfo outputs/example_venn_4sets.pdf
pdfinfo outputs/example_heatmap.pdf
pdffonts outputs/example_boxplots.pdf
pdfimages -list outputs/example_violinplots.pdf
pdfimages -list outputs/example_heatmap.pdf
```

需要进行视觉核对时，可将正式 PDF 临时渲染为 PNG：

```bash
pdftoppm -png -singlefile -r 180 \
  outputs/example_boxplots.pdf \
  outputs/example_boxplots_rendered

pdftoppm -png -singlefile -r 180 \
  outputs/example_violinplots.pdf \
  outputs/example_violinplots_rendered

pdftoppm -png -singlefile -r 180 \
  outputs/example_venn_4sets.pdf \
  outputs/example_venn_4sets_rendered

pdftoppm -png -singlefile -r 180 \
  outputs/example_heatmap.pdf \
  outputs/example_heatmap_rendered
```

## 应用于新数据

### 1. 箱线图与小提琴图

1. 检查 Excel 工作表名、列名、缺失值、样本量和数值类型；小提琴图还要求每组至少两个非恒定数值。
2. 确认实验单位与重复结构：独立、配对、区组、批次或重复测量。
3. 以 `${HERMES_SKILL_DIR}/references/example_boxplot_config.json` 为参考，在用户工作目录创建配置，使 `sheet` 与实际工作表名一致，并设置准确的 `title` 和 `y_label`。
4. 脚本固定使用双侧 Student's t-test（`equal_var=True`）。运行前确认两组独立且等方差假设可接受；不满足时说明本脚本的统计限制，不要静默更换检验。
5. 使用 `${HERMES_SKILL_DIR}` 定位绘图脚本，并在用户工作目录保留 PDF、统计 TSV 和配置。
6. 打开或渲染正式 PDF，核对标签、P 值、配色、密度轮廓、裁切、字体和版式。

### 2. 韦恩图

1. 检查集合数量、列名或组名、空值、重复 ID 和各组条目数；仅使用 2–4 个非空集合。
2. 根据数据结构选择宽表或长表输入；需要固定组顺序时使用 `--groups`。
3. 使用 `${HERMES_SKILL_DIR}` 定位 `scripts/venn_from_tsv.R`，并在用户工作目录保留 PDF、互斥区域 TSV 和输入数据。
4. 核对每组大小、并集大小和全部互斥区域计数；四组图应包含 15 个 membership 区域。
5. 打开或渲染正式 PDF，核对颜色顺序、标题间距、区域数字、椭圆边界和文字裁切。

### 3. 热图

1. 检查数值矩阵的行列数、标签唯一性、缺失值、非有限值及数值类型；需要固定维度时使用 `--expected-rows` 和 `--expected-cols`。
2. 明确色阶语义：默认让当前输入的最小值为纯蓝 `#0000FF`、最大值为正红 `#FF0000`；只有存在明确科学基准时才使用 `--center`，不要无说明地强制对称范围。
3. 将 8 × 4 示例仅作为输入格式和基础版式参考。对当前用户数据重新评估画布宽高、标签旋转、字体、网格线、色标、数值精度和注释密度；必要时微调 `scripts/heatmap_from_tsv.py`，而不是机械复用示例参数。
4. 小矩阵可保留数值注释；矩阵过大或文字拥挤时使用 `--no-annotate`。标准化、数据变换、聚类或重排行列必须基于研究目的明确执行并记录，不能由绘图脚本静默决定。
5. 正式交付 PDF 后，用 `pdfinfo` 检查页面，用 `pdfimages -list` 确认完全矢量要求下没有嵌入图像，再渲染临时 PNG 检查颜色方向、标签裁切、注释对比度和色标。
6. 从当前输入实际计算矩阵维度和极值，不得只凭图面目测或沿用旧结果。

## 图形与统计规范

- 图中必须写明统计检验名称及单双侧。
- P 值由脚本从当前输入数据计算，不得手工填写或复制旧结果。
- Y 轴标签应包含测量对象、实验单位和物理单位。
- 每个点的独立性必须与实验设计一致，避免把技术重复当作生物学重复。
- PDF 优先使用矢量文字和线条；PNG 仅用于预览或期刊明确要求的位图输出。
- 随机抖动必须使用固定种子，以保证重复运行版式一致。
- 不因追求显著性而在查看结果后更换单双侧检验或删除异常值。

## 扩展本技能

新增图形类型时：

1. 在 `scripts/` 中加入独立、带命令行帮助的脚本。
2. 在 `references/` 中加入脱敏或合成示例数据及必要配置。
3. 在本文件中新增脚本用途、输入格式、Hermes 路径示例和验证方法。
4. 让新脚本自动创建输出目录，并输出可追溯的统计或数据摘要。
5. 使用示例数据真实运行脚本，验证生成物后再报告完成。

## 验证清单

- [ ] 技能内脚本和参考数据使用 `${HERMES_SKILL_DIR}` 定位，输出写入用户工作目录
- [ ] 输入工作表、列和数值数量经过检查
- [ ] 箱线图与小提琴图复用示例数据、配置和颜色，检验固定为双侧 Student's t-test
- [ ] 韦恩图限定为 2–4 个非空集合，组内 ID 已去重，并核对集合大小、并集和全部互斥区域计数
- [ ] 默认配色顺序为柔和的红、绿、蓝、黄；四组图的四个椭圆长短轴完全一致
- [ ] 热图已按当前数据检查行列数、极值、标签和注释密度；默认最低值为 `#0000FF`、最高值为 `#FF0000`，且未把 8 × 4 示例当作固定模板
- [ ] 统计检验与实验设计和数据类型一致
- [ ] PDF 可打开且页数、尺寸符合预期
- [ ] 最终向用户返回的是 PDF 矢量图，而不是 PNG 预览图
- [ ] P 值、标题、坐标轴和图例准确
- [ ] 无文字裁切、点线重叠或面板错位
- [ ] 脱敏示例中不包含原始样品、基因、处理或项目标识
