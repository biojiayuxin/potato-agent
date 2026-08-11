---
name: wgcna-network-query
description: 通过线上 Potato Agent WGCNA API 查询马铃薯 DMv8.2 基因的网络归属、模块、kME、TOM 排名共表达邻居、跨网络模块重叠和共享共表达边，并生成与 Interface WGCNA 查看器视觉语义一致的 SVG 或 PDF 矢量网络图。用于回答 leaf、stem、root、reproductive 或 tuberization 网络中的 WGCNA、共表达、候选 hub gene、模块成员、跨组织网络保守性和网络绘图问题。
version: 1.2.1
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, WGCNA, coexpression, TOM, network, module, kME, DMv8.2, API, SVG, PDF, vector]
    related_skills: [potato-gene-search, expression-atlas-query]
prerequisites:
  commands: [python3]
---

# WGCNA Network Query

## 能力范围

通过查询脚本访问部署在 `https://potato-agent.ynnu.edu.cn/api/wgcna` 的公开只读 API，再按需把保存的查询结果交给独立绘图脚本。脚本不调用任何 LLM；结果解读由加载本技能的 Hermes Agent 完成。

本技能只使用线上 API。不要读取本地 WGCNA 数据库，不要导入 `interface.wgcna_viewer`，也不要把 localhost 或 `/srv` 数据当作查询来源。

## 何时使用

- 查询一个或多个 DMv8.2 基因在五个 WGCNA 网络中的模块归属、kME 或表达方差；
- 查询指定网络内按 TOM 排名的共表达邻居；
- 查找模块内高绝对值 kME 的候选 hub genes；
- 比较同一基因或共表达边在不同组织网络中的出现情况；
- 查询跨网络模块的基因集重叠、Jaccard、p 值和 q 值。
- 按 Interface WGCNA 页面配色和分面布局生成可编辑的 SVG 或 PDF 矢量网络图。

不要用本技能查询原始 TPM 或绘制表达热图；这些任务使用 `expression-atlas-query`。不要用 WGCNA 关联替代调控关系、因果关系或直接实验验证。

## 默认工作流

1. 要求准确的 DMv8.2 gene ID，例如 `DM8.2_chr09G24280`。输入是 symbol、reported ID 或历史 ID 时，先用 `potato-gene-search` 解析；`search` 只用于确认 WGCNA 中已有的 ID。
2. 需要确认服务状态或网络元数据时先运行 `status`。
3. 运行 `gene` 查看该基因在五个网络中的模块归属和 kME。
4. 运行 `coexpression` 查询按 TOM 排名的邻居。用户未指定过滤条件时，保留 Interface 页面默认参数，不要主动缩小网络或 `top_n`。
5. 用户要求图像、网络图或可视化但未指定格式时，默认生成 PDF 矢量图。必须先用查询脚本的 `--output-json` 保存结果，再把该 JSON 文件传给 `plot_wgcna_network.py`；不要让绘图脚本承担查询。
6. 绘图成功后，从绘图脚本输出 JSON 的 `output` 字段读取文件绝对路径，并在最终回答中把这个完整路径明确发给用户。
7. 回答中保留 API URL 和 warnings，并说明所选网络、过滤参数及返回边数。

Hermes 加载技能时会把 `${HERMES_SKILL_DIR}` 展开为当前技能的绝对目录。使用：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" --help
```

脚本只依赖 Python 标准库。生产 API 根地址固定在代码中，不提供 localhost、本地数据库或环境变量降级。

## 推荐命令

检查线上状态和五个网络的数据规模：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" status
```

按 gene ID 或名称片段搜索线上 WGCNA gene catalog：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" search DM8.2_chr09G2428 --limit 10
```

查看基因在各网络中的模块、模块大小、表达方差和 kME：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" gene DM8.2_chr09G24280
```

查看模块中绝对值 kME 最高的 50 个基因及跨网络模块重叠：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" module root red
```

查询指定网络内的直接 TOM 邻居：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" coexpression \
  DM8.2_chr09G24280 \
  --network root --top-n 25 --tom-min 0.02 \
  --no-neighbor-edges --no-cross-network
```

使用与 Interface 页面完全一致的默认参数查询并保存可绘图数据：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" coexpression \
  DM8.2_chr09G24280 \
  --output-json /tmp/DM8.2_chr09G24280.wgcna.json
```

传入多个基因或重复使用 `--network` 查询多个网络：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" coexpression \
  DM8.2_chr09G24280 DM8.2_chr07G08200 \
  --network root --network tuberization --top-n 10
```

`coexpression` 默认参数与 Interface 页面一致：查询全部五个网络、`top_n=50`、不设置 TOM 下限、只保留同模块邻居、返回邻居之间的边、返回跨网络同基因显示连接、不返回模块重叠、返回共享边注释，并设置 `max_total_edges=3000`。只有用户明确要求时才覆盖这些参数。可使用：

- `--all-modules`：允许查询基因模块之外的邻居。
- `--no-neighbor-edges`：只返回查询基因到邻居的 TOM 边。
- `--no-cross-network`：不返回跨网络同基因显示连接。
- `--include-module-overlaps`：额外返回模块重叠统计；Interface 默认不返回。
- `--no-shared-edges`：不返回跨网络重复边注释。
- `--output-json PATH`：在标准输出完整结果的同时写入 JSON 文件。

不指定 `--network` 时查询全部五个网络：`leaf`、`stem`、`root`、`reproductive` 和 `tuberization`。API 最多接受 20 个查询基因，`top_n` 上限为 500，总边数上限为 10,000；脚本在发出请求前验证这些限制。

## 矢量网络图

绘图脚本只读取查询脚本 `coexpression --output-json` 写出的 `{api_url, data}` JSON 文件，不导入查询脚本、不接受查询参数，也不发起 HTTP 请求。它会校验 `api_url` 是生产 `/api/wgcna/coexpression` 端点，然后使用文件中的数据绘图。输出扩展名决定格式，只接受 `.svg` 或 `.pdf`。

生成 SVG：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" coexpression \
  DM8.2_chr09G24280 --network root --top-n 25 \
  --no-neighbor-edges --no-cross-network \
  --output-json /tmp/DM8.2_chr09G24280.root.wgcna.json
python3 "${HERMES_SKILL_DIR}/scripts/plot_wgcna_network.py" \
  /tmp/DM8.2_chr09G24280.root.wgcna.json \
  --output /tmp/DM8.2_chr09G24280.root.wgcna.svg
```

生成 PDF：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_wgcna_network.py" coexpression \
  DM8.2_chr09G24280 --network root --network tuberization \
  --top-n 25 --tom-min 0.02 \
  --output-json /tmp/DM8.2_chr09G24280.wgcna.json
python3 "${HERMES_SKILL_DIR}/scripts/plot_wgcna_network.py" \
  /tmp/DM8.2_chr09G24280.wgcna.json \
  --output /tmp/DM8.2_chr09G24280.wgcna.pdf
```

绘图脚本的第一个位置参数是查询结果 JSON，另有：

- `--width`：画布宽度，默认 1400。
- `--stage-height`：网络绘图区高度，默认 760。
- `--labels all|query|none`：基因标签模式，默认 `all`；节点很多时使用 `query`。
- `--output PATH`：必填，使用绝对路径，扩展名必须是 `.svg` 或 `.pdf`。脚本输出的 `output` 字段始终是规范化后的绝对路径。

图中复刻 Interface 的固定视觉语义：network 分面环形布局；查询基因为深边框菱形；邻居为浅色圆形；leaf、stem、root、reproductive、tuberization 使用页面原色；TOM 边线宽随 rank 变化；共享边加宽；跨网络 `same_gene` 边为灰色虚线。静态图额外显示网络图例、查询参数、warnings 和线上来源。

SVG 和 PDF 都由矢量图元直接构建，不嵌入 PNG、JPEG 或其他栅格图。查询范围完全由输入 JSON 决定；绘图阶段不得增加 `top_n`、移除 TOM 阈值或重新查询。

## 图像交付规则

- 用户只说“画图”“生成网络图”“可视化”或“返回图像”而没有指定格式时，生成并返回 PDF 矢量图。
- 只有用户明确要求 SVG 时才改为 `.svg`；用户明确指定其他受支持格式时遵循用户要求。
- `--output` 使用绝对路径。绘图完成后检查脚本返回状态为成功，并读取其 JSON 中的 `output` 字段。
- 最终回答必须包含该字段给出的完整绝对路径，例如：`PDF 矢量图：/tmp/DM8.2_chr09G24280.wgcna.pdf`。
- 不要只说“图已生成”，不要只给文件名，也不要只给所在目录。

## 结果解读

- `module` 是 WGCNA 模块归属。`grey` 表示未分配，不是具有一致生物学意义的模块。
- `kme_own_module` 是模块成员度。筛选模块内候选 hub 时比较其绝对值；不能只凭 kME 宣称 hub，必须同时说明网络和模块。
- `tom` 是指定网络内的拓扑重叠相似度；`rank` 是该邻居在查询基因所存 top-edge 表中的排名。
- `shared_coexpression=true` 表示该基因对在多个网络的已存共表达边中重复出现；同时检查 `shared_networks` 和 `shared_tom_by_network`。
- `module_overlaps` 描述不同网络模块的基因集重叠。显著性使用经过多重检验校正的 `q_value`，同时报告 overlap genes 或 Jaccard。
- `same_gene` 边只连接同一基因在不同网络中的节点，不是 TOM 共表达边。
- WGCNA 关联是描述性的无向关系。不能从 TOM、模块归属或重叠中推断调控方向或因果关系。

脚本输出包含 `api_url` 和原始 API `data` 的 JSON 对象。不得静默丢弃 `data.warnings`，包括基因未出现在所选网络中的提示。HTTP、连接或响应结构失败时，报告真实错误，不要猜测或回退到本地文件。

## 常见错误

1. **直接提交 symbol、DM8C 或 Soltu.DM ID。** 先用 `potato-gene-search` 解析出唯一的标准 DMv8.2 gene ID。
2. **在用户未指定时擅自偏离页面默认值。** 默认查询全部网络并使用 `top_n=50`；只有用户明确要求时才缩小网络或邻居数量。
3. **把 grey 当成普通模块。** grey 表示未分配，不应据此讨论共同模块功能。
4. **把 `same_gene` 当成共表达。** 它只是跨网络显示连接；只有 `tom_edge` 携带 TOM 共表达关系。
5. **忽略 API warnings。** 缺失基因或缺失网络必须在回答中明确说明。
6. **改查本地 Interface 或数据库。** 线上 API 失败时报告错误，不要切换本地来源。
7. **把密集图强行保留全部标签。** 节点很多时使用 `--labels query`，但不要删除网络节点或边。
8. **用截图代替矢量图。** 用户要求图像时优先运行绘图脚本并返回 `.svg` 或 `.pdf`。
9. **让绘图脚本直接查询。** 必须先用 `query_wgcna_network.py coexpression --output-json` 保存结果，再把同一个文件传给绘图脚本。
10. **没有把完整路径发给用户。** 默认交付 PDF，并在最终回答中逐字返回绘图脚本 `output` 字段中的绝对路径。

## 验证清单

- [ ] 命令路径使用 Hermes 展开的 `${HERMES_SKILL_DIR}`。
- [ ] `api_url` 的主机是 `potato-agent.ynnu.edu.cn`，路径以 `/api/wgcna/` 开头。
- [ ] 输入是唯一、准确的 DMv8.2 gene ID。
- [ ] 回答说明所选网络、`top_n`、TOM 阈值和同模块限制。
- [ ] 回答区分 TOM 边、跨网络同基因连接和模块重叠。
- [ ] 回答保留 warnings，并避免调控或因果推断。
- [ ] 绘图输入是本次查询脚本保存的 `{api_url, data}` JSON 文件，查询与绘图分两步执行。
- [ ] 用户未指定格式时生成 PDF；最终回答包含绘图脚本 `output` 字段给出的完整绝对路径。
- [ ] 图像文件是 SVG 或 PDF，且没有嵌入栅格图像。
