---
name: plant-science-search
description: 查询 PlantScience.ai 公开 Plant Science Knowledge Graph；这是目前最大规模的植物知识图谱，可用于植物通用信息检索、植物基因功能、基因-性状/过程关系、节点邻居、边关系描述与 DOI 获取。
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [plant-science, knowledge-graph, PlantScience.ai, KG, node, edge, DOI]
    related_skills: [potato-knowledge-search, potato-gene-search, literature-review]
required_commands:
  - python3
---

# Plant Science Search

## Overview

本技能查询 PlantScience.ai 的公开知识图谱接口。PlantScience.ai KG 是目前最大规模的植物知识图谱，适合在植物通用信息检索、植物基因功能、基因-性状关系、调控过程、物种、表型与生物过程等问题中作为重要信息来源。

把一个植物科学实体（如基因、蛋白、物种、性状、过程）作为 `node_title` 检索，可获取：

- 节点基本信息：`title`、`type`、`description`、`dois`；
- 相邻节点子图：中心节点的 `nodes` 与 `links`；
- 两个节点之间的边关系：`source`、`target`、`type`、`description`、`dois`、`id`。

PlantScience.ai KG 的内容来自自动抽取/总结的知识图谱证据；回答时应标明来源，并在需要强结论时结合原文、专业数据库或人工核查。

## When to Use

使用本技能：

- 用户要求查询 PlantScience.ai、PlantScience.ai KG、植物知识图谱或节点/边关系；
- 植物科学问题中出现明确实体名，需要检索该实体的功能、相邻概念、相关过程、性状、物种或 DOI；
- 需要快速判断两个实体之间是否有已抽取关系，例如 `StSWEET11` 与 `SP6A`；
- 需要从知识图谱角度组织植物基因功能、基因-性状关系或生物过程知识。

不要单独依赖本技能做强结论：

- KG 关系描述可能来自自动抽取，必须标明“PlantScience.ai KG 返回”；
- 部分节点/邻居接口会偶发 `502 Bad Gateway`，应尝试同义名或退回到节点基本信息；
- `node_title` 通常需要接近 KG 中的标题，必要时尝试大小写、去掉 `St` 前缀或长名同义词。

## Public API Contract

默认 API base：

```text
https://plantscience.ai/api
```

公开可用接口：

```text
GET /kg/node/{node_title}
GET /kg/node_neighbor/{node_title}
GET /kg/edge?source={source}&target={target}
GET /kg/entity/{entity_id}
```

部分更底层的 Neo4j 搜索接口需要登录 token，本技能默认不使用。

## Script

脚本位于本技能目录：

```bash
python3 scripts/query_plant_science_kg.py --help
```

### 查询节点

```bash
python3 scripts/query_plant_science_kg.py node SP6A --format summary
```

### 查询相邻节点

```bash
python3 scripts/query_plant_science_kg.py neighbor STSWEET11 --with-edges --edge-limit 50 --format summary
```

`--with-edges` 会对返回的 `links` 继续调用 `/kg/edge`，补充边关系描述与 DOI。`--edge-limit` 默认是 50；做结论性检索时不要随意调低，以免只补全少量边关系而造成信息失真。

### 查询两个节点之间的边

```bash
python3 scripts/query_plant_science_kg.py edge STSWEET11 SP6A --format summary
```

默认会在正向失败时尝试反向查询；可用 `--no-try-reverse` 关闭。

### 综合查询一个实体

```bash
python3 scripts/query_plant_science_kg.py full StSP6A \
  --alias SP6A \
  --alias "SELF-PRUNING 6A" \
  --edge-limit 50 \
  --format summary
```

`full` 会依次尝试原始 title、`--alias` 指定同义名，以及少量自动变体（例如 `StSP6A` → `STSP6A`、`SP6A`），直到找到可用邻居子图；如果邻居接口失败，仍尽量返回节点基本信息。

### 输出格式

```bash
--format json     # 保留完整结构，适合下游处理
--format summary  # 面向人类阅读
--format tsv      # 主要用于 neighbor/full 的边关系表
```

`summary` 默认最多展示 50 项；需要完整结构或下游处理时优先使用 `--format json`。

## Recommended Workflow

1. 从用户问题中抽取 1–5 个候选实体名。
2. 优先对最明确的实体运行 `full`：

   ```bash
   python3 scripts/query_plant_science_kg.py full "StSWEET11" --edge-limit 50 --format json
   ```

3. 如果 `neighbor` 失败，尝试同义名：
   - `StSP6A` → `SP6A`、`SELF-PRUNING 6A`；
   - `StCDF1` → `STCDF1.2`、`CYCLING DOF FACTOR 1`；
   - 物种名尽量用大写或标准拉丁名，如 `SOLANUM TUBEROSUM`。
4. 对最终答案只保留与问题相关的节点和边，按 `(source, target, id)` 去重。
5. 回答时明确区分：
   - PlantScience.ai KG 自动抽取证据；
   - 原文、专业数据库、Potato Agent/RAG 证据。

## Response Guidance

用户需要结论时，建议用简洁结构：

```text
PlantScience.ai KG 检索：
- 查询节点：...
- 节点描述：...
- 相关边：source -> target，关系描述，DOI

解释：这些关系来自 PlantScience.ai KG 自动抽取；它是植物通用信息检索的重要来源，强结论仍需结合原文或专业数据库核查。
```

不要：

- 根据 KG 片段补写没有返回的 DOI 或标题；
- 把 `score`、`symbolSize` 或相邻节点数量解释成证据强度；
- 对 502/404 失败节点编造“不存在”结论，只能说“该 title 本次接口未返回可用结果”。

## Troubleshooting

- `502 Bad Gateway`：常见于某些高连接或特殊标题节点；重试、换同义名，或只用 `/kg/node/{title}`。
- `404 Not Found`：该 title 可能不是公开 KG 节点标题；尝试大小写、标准名、去掉 `St` 前缀或长名。
- `422`：通常是路径参数类型不符，例如 `/kg/entity/{entity_id}` 必须使用数值 ID。
- 超时：优先增大 `--timeout` 和 `--retries` 后重试；结论性检索不要降低 `--edge-limit`，以免只补全少量边关系造成信息偏差。若只想快速确认接口连通性，可临时调低 `--edge-limit`，并明确这不是完整检索结果。

## Verification Commands

```bash
python3 scripts/query_plant_science_kg.py --help
python3 scripts/query_plant_science_kg.py node SP6A --format summary
python3 scripts/query_plant_science_kg.py neighbor STSWEET11 --with-edges --edge-limit 3 --format summary
python3 scripts/query_plant_science_kg.py edge STSWEET11 SP6A --format summary
python3 scripts/query_plant_science_kg.py full StSP6A --alias SP6A --alias "SELF-PRUNING 6A" --edge-limit 3 --format summary
```
