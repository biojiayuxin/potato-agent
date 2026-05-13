---
name: arabidopsis-gene-search
description: 查询拟南芥基因功能和文献证据；先用 TAIR 将 gene symbol、alias 或 AGI ID 解析为标准 geneID，再用该 geneID 调用 PlantConnectome 查询知识图谱关系和 PMID。适用于拟南芥基因功能、别名、AGI ID 消歧和证据总结。
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [Arabidopsis, TAIR, PlantConnectome, gene-function, PMID, knowledge-graph]
    related_skills: [literature-review]
prerequisites:
  commands: [python3]
---

# Arabidopsis Gene Search

查询拟南芥（*Arabidopsis thaliana*）基因功能、别名、AGI ID 和文献证据。

## 固定流程

1. 先运行 TAIR 查询，把用户输入解析为标准 AGI geneID。
2. 如果 TAIR 返回多个合理候选，先让用户选择 geneID；不要直接用 symbol 查 PlantConnectome。
3. 确认 geneID 后，用 `full` 模式查询 PlantConnectome 关系和 PMID。
4. PlantConnectome 是自动抽取知识图谱，回答时要把 TAIR 注释和 PlantConnectome 证据分开说明。

## 命令

优先使用一步式查询：

```bash
python3 scripts/query_arabidopsis_gene_search.py full AT1G62360 --max-entities 1 --max-edges 50 --format summary
```

查询 symbol 或 alias 时，先看 TAIR 是否歧义：

```bash
python3 scripts/query_arabidopsis_gene_search.py tair STM --format summary
```

如果用户已确认 geneID，用 `--gene-id` 消歧：

```bash
python3 scripts/query_arabidopsis_gene_search.py full STM --gene-id AT1G62360 --max-entities 1 --max-edges 50 --format summary
```

当 geneID 结果很少或明显噪声较多时，再用 TAIR 确认的长别名/全名辅助查询：

```bash
python3 scripts/query_arabidopsis_gene_search.py full AT1G62360 --include-aliases --max-alias-queries 1 --max-entities 1 --max-edges 50 --format summary
```

`plant` 模式是低层接口，只在 geneID 已确认时使用；它不做 TAIR 消歧：

```bash
python3 scripts/query_arabidopsis_gene_search.py plant AT1G62360 --max-entities 1 --max-edges 50 --format summary
```

## 输出使用规则

- TAIR 字段 `gene_name` 是标准 AGI geneID，`gene_model_ids` 是转录本/模型 ID，`other_names` 是 symbol/alias。
- `status: ambiguous` 表示必须让用户确认 geneID 后再继续。
- 总结功能时，优先用 TAIR 描述确定基因身份和基础功能；PlantConnectome 只作为关系和 PMID 证据。
- PlantConnectome 证据优先压缩为 `source/id + relation + target + PMID`。脚本 summary 会展示少量 `basis` 供核查，但最终回答不必展开所有 `basis`。
- 不要仅凭 PlantConnectome 关系频次下结论；重要结论应对应到 PMID 或 TAIR 注释。

## 常用参数

```text
--format json|summary       输出格式，默认 json
--max-candidates N          TAIR 候选返回数量，默认 10
--gene-id AGI               用户已确认的 AGI ID，用于跳过歧义选择
--max-entities N            PlantConnectome 预览实体数量，默认 3
--max-edges N               每个 PlantConnectome 实体最多保留 KG 边数，默认 50
--snippets N                为前 N 个 p_source 拉取文献片段，默认 0
--include-aliases           full 模式下额外用 TAIR 确认的长别名/全名查 PlantConnectome
--max-alias-queries N       最多辅助查询几个长别名/全名，默认 2
--timeout SECONDS           HTTP 超时，默认 60
```

## 回答格式

回答用户时保持简洁：

1. TAIR 确认的 geneID、gene model、主要别名和描述。
2. 归纳的功能结论，每条结论标注 TAIR 或 PMID 证据。
3. PlantConnectome 自动抽取结果的限制说明。

## 验证

```bash
python3 scripts/query_arabidopsis_gene_search.py tair AT1G62360 --format summary
python3 scripts/query_arabidopsis_gene_search.py tair STM --format summary
python3 scripts/query_arabidopsis_gene_search.py full AT1G62360 --max-entities 1 --max-edges 50 --format summary
```

预期：`AT1G62360` 唯一解析为 SHOOT MERISTEMLESS / STM；`STM` 返回多个精确 alias 候选，需要用户确认。

## 注意

- TAIR 详情接口可能需要登录；本技能只依赖不登录可用的基础检索接口。
- 脚本固定使用浏览器 User-Agent，避免 TAIR 对默认命令行请求返回 403。
- PlantConnectome 解析依赖网页内嵌数据结构；如果网站前端改版，脚本可能需要更新。
- 对 ICE1/AT3G26744 等高连接实体，详情页可能很大；必要时使用 `--timeout 300` 和 `--max-entities 1`。
