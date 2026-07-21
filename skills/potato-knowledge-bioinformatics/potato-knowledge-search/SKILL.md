---
name: potato-knowledge-search
description: 检索 Potato Knowledge Hub 的马铃薯文献 RAG 索引，并结合 PlantScience.ai 知识图谱查询相关实体、邻居、边关系描述和 DOI，用于查找 potato / Solanum tuberosum 相关性状、基因功能、病害、胁迫、组学、育种等问题的文献证据片段与结构化关系证据，并基于两类来源归纳。马铃薯基因名与基因号的对应关系查询优先使用 potato-gene-search，完整综述或最新网页事实用 literature-review / web。
version: 1.2.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, knowledge-search, rag, literature, knowledge-graph, PlantScience.ai, retrieval, doi, Potato-Knowledge-Hub]
    related_skills: [literature-review, potato-gene-search]
prerequisites:
  commands: [python3]
---

# Potato Knowledge Search

## 能力范围

本技能用于马铃薯知识证据检索。默认同时使用两类来源：

1. **Potato Knowledge Hub RAG**：检索马铃薯文献片段，返回 `rank`、`score`、`title`、`doi`、`text`。
2. **PlantScience.ai Knowledge Graph**：查询植物知识图谱中的实体节点、邻居、边关系描述和 DOI，补充基因、性状、过程、物种、病原、蛋白或调控关系等结构化证据。

两类来源必须分开解释。RAG 片段是马铃薯文献检索证据；KG 结果是自动抽取/整理的图谱关系证据，不能直接等同于原文强证据。

## 何时使用

使用本技能：

- 用户明确要求查询马铃薯知识库、Potato Knowledge Hub、RAG 数据库、文献证据、论文标题、DOI 或原文片段；
- 用户询问 potato / *Solanum tuberosum* 的基因功能、性状、病害、栽培、生理、组学、育种、品种或胁迫响应，并需要可追溯依据；
- 用户需要先检索马铃薯文献证据，再结合知识图谱关系进行归纳、证据整理或回答；
- 用户给出明确实体，例如基因名、蛋白、性状、病原、物种或生物过程，需要同时查看文献片段和 KG 关系。

优先使用其他工具的情况：

- 马铃薯基因名与基因号、reported ID、历史 ID、DMv8 ID 的对应关系查询，优先使用 `potato-gene-search`。
- 完整系统综述、跨库大规模文献检索或最新网页事实，使用 `literature-review` 或 web/research 工具。
- 非马铃薯问题不要把本技能作为唯一来源；如果使用 KG 结果，应说明它是 PlantScience.ai 的植物通用图谱证据。

## 默认工作流

1. 用用户原始问题或轻微改写后的检索句查询 RAG。
2. 从问题中抽取 1-5 个 KG 实体。优先选择基因名、蛋白、性状、病原、物种、代谢/发育/胁迫过程。不要把整句问题直接当作 KG 实体。
3. 对每个 KG 实体提供必要别名，例如 `StSP6A|SP6A|SELF-PRUNING 6A`。脚本会自动尝试大小写和常见 `St` 前缀变体。
4. 基于统一 JSON 输出整理答案，分别报告 RAG 与 KG 证据，再给出综合解释。
5. 如果 KG 没有返回节点或边，只能说“本次 KG 未返回可用结果”；不要推断实体不存在或关系不存在。

## 脚本

以下命令中的技能路径由 Hermes 在加载时展开为绝对路径；不要依赖当前工作目录。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" --help
```

### 推荐调用

面向下游处理时使用 JSON：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "potato tuber dormancy genes" \
  --kg-entity "StSP6A|SP6A|SELF-PRUNING 6A" \
  --kg-entity "tuber dormancy" \
  --format json
```

面向人工快速查看时使用 summary：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "potato late blight resistance" \
  --kg-entity "Phytophthora infestans" \
  --kg-entity "late blight" \
  --rag-top-k-retrieve 200 \
  --rag-top-k-rerank 20 \
  --kg-edge-limit 5 \
  --format summary
```

表格处理可使用 TSV：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "potato starch biosynthesis" \
  --kg-entity "starch biosynthesis" \
  --format tsv
```

### 关键参数

```text
--rag-top-k-retrieve N
                    RAG 初始向量候选数量，默认 200。
--rag-top-k-rerank N
                    RAG 重排后返回数量，默认 20；summary 同步展示这些结果，不再额外截断。
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

JSON 输出顶层结构：

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

`rag.results[].score` 是 RAG 相关性分数，不等同于论文质量或证据强度。KG 中的 `symbolSize`、邻居数量或边数量也不等同于证据强度。

## 回答规范

回答时必须遵守：

- 不要编造 JSON 中没有的 DOI、作者、期刊、年份或结论。
- DOI 缺失时写“未返回 DOI”；标题缺失时写“未返回标题”。
- 多个 RAG 结果来自同一 DOI/标题时可以合并解释，但不要丢失原始 rank 信息。
- KG 关系必须标注为 PlantScience.ai KG 返回的自动抽取/整理证据。
- 做强结论前应写明“基于检索片段和 KG 返回结果”，必要时建议进一步查原文或系统综述。

## 故障处理

- 如果 RAG 失败但 KG 成功，可以继续回答 KG 结果，同时明确 RAG 接口错误。
- 如果 KG 失败但 RAG 成功，可以继续回答 RAG 结果，同时明确 KG 未返回可用节点/边。
- 如果没有明确 KG 实体，脚本会做轻量自动抽取；自动抽取失败时会跳过 KG 并给出 warning。调用方应尽量显式传入 `--kg-entity`。
- `502/503/504` 等 KG 临时错误可以重试或减少 `--kg-edge-limit` 做连通性测试；结论性检索不要随意降低边补全数量。
- 如果两个来源都失败，应向用户报告接口错误，不要凭空生成文献或 KG 关系。

## 验证命令

安装或修改后可执行：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" --help
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato late blight resistance" --kg-entity "Phytophthora infestans" --kg-entity "late blight" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format summary
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato tuber dormancy genes" --kg-entity "StSP6A|SP6A|SELF-PRUNING 6A" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format json
```
