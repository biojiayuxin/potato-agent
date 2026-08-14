---
name: potato-knowledge-search
description: 检索 Potato Knowledge Hub 马铃薯文献 RAG 索引并结合 PlantScience.ai 知识图谱，用于回答 potato / Solanum tuberosum 的基因、性状、病害、胁迫、组学和育种问题，并返回可追溯的文献片段、DOI 与图谱关系。快速查询一个明确马铃薯基因可能具有的功能时使用 potato-gene-function-prediction；基因 ID、symbol、坐标、注释或序列查找使用 potato-gene-search。
version: 1.4.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, knowledge-search, rag, literature, knowledge-graph, PlantScience.ai, retrieval, DOI, Potato-Knowledge-Hub]
    related_skills: [potato-gene-function-prediction, literature-review, potato-gene-search]
prerequisites:
  commands: [python3]
---

# Potato Knowledge Search

## 能力范围

使用两类来源检索马铃薯知识证据：

1. **Potato Knowledge Hub RAG**：检索马铃薯文献片段，返回 `rank`、`score`、`title`、`doi` 和 `text`。
2. **PlantScience.ai Knowledge Graph**：查询实体节点、邻居、边关系描述和 DOI，补充基因、性状、过程、物种、病原、蛋白或调控关系等结构化证据。

先分开解释两个来源，再进行综合。RAG 结果是检索片段；KG 结果是自动抽取或整理的图谱关系。不能把 KG 关系直接等同于原文强证据。

## 使用边界

使用本技能：

- 用户要求查询马铃薯知识库、Potato Knowledge Hub、RAG、文献证据、论文标题、DOI 或原文片段；
- 用户询问 potato / *Solanum tuberosum* 的基因、性状、病害、栽培、生理、组学、育种、品种或胁迫响应，并需要可追溯依据；
- 用户需要先检索马铃薯文献证据，再结合知识图谱关系归纳；
- 用户给出基因名、蛋白、性状、病原、物种或生物过程等明确实体，需要查看文献片段和 KG 关系。

优先或配合其他技能：

- 用户只想快速知道一个明确马铃薯基因可能有什么功能时，使用 `potato-gene-function-prediction`。
- 用户给出 symbol、reported ID、历史 ID 或不确定编号时，先用 `potato-gene-search` 解析到唯一 gene ID。
- 用户需要基因坐标、转录本、domain、表达、注释、文献列表或序列时，使用 `potato-gene-search`。
- 用户需要详细功能介绍、最新证据或进一步功能推测时，可将本技能与 `potato-gene-search`、`potato-gene-function-prediction` 的结果分来源整合。
- 用户要求系统性同源、序列或多步骤功能分析时，使用 `search-gene-function`。
- 完整系统综述、跨库大规模检索或最新网页事实使用 `literature-review` 或 web/research 工具。

## 默认工作流

1. 用用户原始问题或轻微改写后的检索句查询 RAG。默认使用 `--rag-top-k-retrieve 200 --rag-top-k-rerank 20`；除非用户明确指定，否则不要降低。
2. 从问题中抽取 1-5 个 KG 实体。优先选择基因名、蛋白、性状、病原、物种、代谢、发育或胁迫过程，不要把整句问题直接作为 KG 实体。
3. 为 KG 实体提供必要别名，例如 `StSP6A|SP6A|SELF-PRUNING 6A`。脚本会尝试大小写和常见 `St` 前缀变体。
4. 基于统一 JSON 分别核对 RAG 与 KG，再归纳一致、互补、冲突或缺失之处。
5. KG 未返回节点或边时，只说明“本次 KG 未返回可用结果”，不要推断实体或关系不存在。

## 查询脚本

使用 Hermes 加载技能时提供的绝对路径，不依赖当前工作目录：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" --help
```

面向下游处理时使用 JSON：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "potato tuber dormancy genes" \
  --kg-entity "StSP6A|SP6A|SELF-PRUNING 6A" \
  --kg-entity "tuber dormancy" \
  --format json
```

面向人工快速查看时使用本地 summary 格式：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "potato late blight resistance" \
  --kg-entity "Phytophthora infestans" \
  --kg-entity "late blight" \
  --format summary
```

`--format summary` 仅在本地整理 RAG 与 KG 已返回的字段，不调用其他接口或模型。表格处理可使用 `--format tsv`。

关键参数：

```text
--rag-top-k-retrieve N
                    RAG 初始向量候选数量，默认 200。
--rag-top-k-rerank N
                    RAG 重排后返回数量，默认 20。
--kg-entity "primary|alias1|alias2"
                    指定 PlantScience.ai KG 实体；可重复使用。
--max-kg-entities N 最多查询多少个 KG 实体，默认 5。
--no-auto-kg-entities
                    关闭轻量自动实体抽取，只使用 --kg-entity。
--kg-edge-limit N   每个 KG 实体最多补全多少条边详情，默认 50。
--no-kg-edge-details
                    只查节点和邻居，不补全边关系详情。
--rag-only          只查 RAG；仅用于调试或用户明确要求。
--kg-only           只查 KG；仅用于调试或用户明确要求。
--format json|summary|tsv
                    默认 json。
```

服务地址可通过参数或环境变量覆盖：

```text
--rag-base-url URL              默认 https://www.potato-ai.top
POTATO_RAG_BASE_URL             RAG base URL 环境变量
--kg-base-url URL               默认 https://plantscience.ai/api
PLANT_SCIENCE_KG_BASE_URL       KG base URL 环境变量
```

## 输出结构

JSON 顶层结构：

```json
{
  "success": true,
  "query": "user question",
  "rag": {
    "success": true,
    "results": [
      {
        "rank": 1,
        "score": 0.9984,
        "doi": "10.xxxx/xxxxx",
        "title": "Paper title",
        "text": "Retrieved literature snippet"
      }
    ]
  },
  "kg": {
    "success": true,
    "entities": [
      {
        "entity": "SP6A",
        "entity_source": "user",
        "result": {
          "node": {},
          "neighbor": {},
          "edge_details": []
        }
      }
    ]
  },
  "warnings": []
}
```

`rag.results[].score` 是检索相关性分数，不等同于论文质量或证据强度。KG 的 `symbolSize`、邻居数量或边数量也不等同于证据强度。

## 回答规范

- 不要编造 JSON 中没有的 DOI、作者、期刊、年份或结论。
- DOI 缺失时写“未返回 DOI”；标题缺失时写“未返回标题”。
- 同一 DOI 或标题的多个 RAG 结果可以合并解释，但保留原始 rank 信息。
- 将 KG 关系标注为 PlantScience.ai KG 返回的自动抽取或整理证据。
- 综合结论写明“基于 RAG 检索片段和 PlantScience.ai KG 返回结果”，必要时建议核查原文。

## 故障处理

- RAG 失败但 KG 成功时，继续回答 KG 结果并明确 RAG 接口错误。
- KG 失败但 RAG 成功时，继续回答 RAG 结果并明确 KG 未返回可用节点或边。
- 没有明确 KG 实体时，脚本会做轻量自动抽取；自动抽取失败会跳过 KG 并给出 warning。调用方应尽量显式传入 `--kg-entity`。
- `502/503/504` 等 KG 临时错误可以重试或减少 `--kg-edge-limit` 做连通性测试；结论性检索不要随意降低边补全数量。
- RAG 与 KG 都失败时，报告接口错误，不要凭空生成文献或图谱关系。

## 验证命令

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" --help
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato late blight resistance" --kg-entity "Phytophthora infestans" --kg-entity "late blight" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format summary
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato tuber dormancy genes" --kg-entity "StSP6A|SP6A|SELF-PRUNING 6A" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format json
```
