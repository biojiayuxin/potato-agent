---
name: potato-knowledge-search
description: 检索 Potato Knowledge Hub 马铃薯文献 RAG 索引并结合 PlantScience.ai 知识图谱，用于回答 potato / Solanum tuberosum 的基因功能、性状、病害、胁迫、组学和育种问题。仅当用户要快速、初步查询某个明确马铃薯基因的推测或预测功能时，额外读取 Potato Agent gene Summary、Reliability 和证据，并默认整合 RAG、Summary、PlantScience.ai 三类结果。简单基因 ID、symbol、坐标或序列查找使用 potato-gene-search；系统性功能预测使用 search-gene-function。
version: 1.3.0
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

本技能用于马铃薯知识证据检索。通常同时使用两类来源：

1. **Potato Knowledge Hub RAG**：检索马铃薯文献片段，返回 `rank`、`score`、`title`、`doi`、`text`。
2. **PlantScience.ai Knowledge Graph**：查询植物知识图谱中的实体节点、邻居、边关系描述和 DOI，补充基因、性状、过程、物种、病原、蛋白或调控关系等结构化证据。

满足“快速查询某个明确基因的推测功能”前提时，增加第三类来源：

3. **Potato Agent Gene Summary**：返回 `predictedFunction`、`reliabilityGrade`、`gradeReason` 和生成预测所用的直接马铃薯、同源基因及表达证据。

各来源必须先分开解释。RAG 片段是马铃薯文献检索证据；Gene Summary 是带可靠性等级的功能预测；KG 结果是自动抽取/整理的图谱关系证据。不能把预测或 KG 关系直接等同于原文强证据。

## 何时使用

使用本技能：

- 用户明确要求查询马铃薯知识库、Potato Knowledge Hub、RAG 数据库、文献证据、论文标题、DOI 或原文片段；
- 用户询问 potato / *Solanum tuberosum* 的基因功能、性状、病害、栽培、生理、组学、育种、品种或胁迫响应，并需要可追溯依据；
- 用户需要先检索马铃薯文献证据，再结合知识图谱关系进行归纳、证据整理或回答；
- 用户给出明确实体，例如基因名、蛋白、性状、病原、物种或生物过程，需要同时查看文献片段和 KG 关系。

只有同时满足以下前提，才调用 Gene Summary：

1. 用户明确要“快速查询”“简单看一下”“初步判断”某基因的推测、预测、可能或潜在功能；
2. 查询对象是一个具体马铃薯基因，并且已通过 `potato-gene-search` 获得唯一、以 `DM8.2` 开头的 DMv8.2 gene ID；
3. 用户没有要求系统性同源分析、结构域分析、序列比对或完整功能预测流程。

只有 symbol、reported ID、历史 ID 或其他格式的基因号时，先用 `potato-gene-search` 解析到唯一、以 `DM8.2` 开头的 DMv8.2 gene ID；候选不唯一时不要调用 Summary，先向用户说明歧义。

优先使用其他工具的情况：

- 马铃薯基因名与基因号、reported ID、历史 ID、DMv8 ID 的对应关系查询，优先使用 `potato-gene-search`。
- 仅查基因坐标、转录本、domain、表达、文献列表或序列时，使用 `potato-gene-search`，不要调用 Gene Summary。
- 需要系统性预测一个基因的功能并执行跨物种同源、序列或多步骤分析时，使用 `search-gene-function`，不要把快速 Summary 当作替代。
- 完整系统综述、跨库大规模文献检索或最新网页事实，使用 `literature-review` 或 web/research 工具。
- 非马铃薯问题不要把本技能作为唯一来源；如果使用 KG 结果，应说明它是 PlantScience.ai 的植物通用图谱证据。

## 默认工作流

1. 用用户原始问题或轻微改写后的检索句查询 RAG。默认固定使用 `--rag-top-k-retrieve 200 --rag-top-k-rerank 20`；除非用户明确指定，否则不要降低这两个值。
2. 从问题中抽取 1-5 个 KG 实体。优先选择基因名、蛋白、性状、病原、物种、代谢/发育/胁迫过程。不要把整句问题直接当作 KG 实体。
3. 对每个 KG 实体提供必要别名，例如 `StSP6A|SP6A|SELF-PRUNING 6A`。脚本会自动尝试大小写和常见 `St` 前缀变体。
4. 仅在满足快速基因推测功能前提时传入 `--gene-summary`。脚本会从 Summary 的 `potato_gene_names` 自动补充 KG 实体候选。
5. 快速基因功能查询默认同时运行 RAG、Gene Summary 和 KG；不要在正常回答中配合 `--rag-only` 或 `--kg-only`。
6. 基于统一 JSON 整理答案：先分别核对三类证据，再生成综合结论，指出一致、互补或冲突之处。
7. 如果 KG 没有返回节点或边，只能说“本次 KG 未返回可用结果”；不要推断实体不存在或关系不存在。

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

快速查询一个明确基因的推测功能时，显式增加 Gene Summary，并保留默认 RAG 与 KG 查询：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" \
  "GAME9 potato predicted function" \
  --gene-summary "DM8.2_chr01G26640" \
  --kg-entity "GAME9|ERF1" \
  --format json
```

不要仅因问题中出现基因名就添加 `--gene-summary`。必须满足上面的快速、单基因、推测功能三个条件。

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
--gene-summary GENE_ID
                    显式增加一个以 DM8.2 开头的 DMv8.2 基因号的 Summary；仅用于快速查询明确基因的推测功能。
--gene-summary-timeout N
                    Summary API 超时秒数，默认 60。
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
  "gene_summary": {
    "success": true,
    "geneId": "DM8.2_chr01G26640",
    "transcriptId": "DM8.2_chr01G26640.1",
    "predictedFunction": "...",
    "reliabilityGrade": "F1",
    "gradeReason": "...",
    "evidence": {}
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

未传 `--gene-summary` 时，`gene_summary.skipped` 为 `true`。`rag.results[].score` 是 RAG 相关性分数，不等同于论文质量或证据强度。`reliabilityGrade` 是 Summary 自身的预测可靠性等级。KG 中的 `symbolSize`、邻居数量或边数量也不等同于证据强度。

## 回答规范

回答时必须遵守：

- 不要编造 JSON 中没有的 DOI、作者、期刊、年份或结论。
- DOI 缺失时写“未返回 DOI”；标题缺失时写“未返回标题”。
- 多个 RAG 结果来自同一 DOI/标题时可以合并解释，但不要丢失原始 rank 信息。
- KG 关系必须标注为 PlantScience.ai KG 返回的自动抽取/整理证据。
- Gene Summary 必须称为功能预测或推测，不要因等级较高就改写成实验事实；使用 `gradeReason` 解释等级。
- 快速基因功能查询必须默认整合 RAG、Gene Summary 和 PlantScience.ai：报告三者的一致信息、补充信息和冲突/缺失，不要只复述 `predictedFunction`。
- 综合结论应写明“基于 RAG 检索片段、Potato Agent 功能预测和 PlantScience.ai KG 返回结果”，必要时建议进一步查原文或执行系统功能分析。

## 故障处理

- 如果 RAG 失败但 KG 成功，可以继续回答 KG 结果，同时明确 RAG 接口错误。
- 如果 KG 失败但 RAG 成功，可以继续回答 RAG 结果，同时明确 KG 未返回可用节点/边。
- 如果 Gene Summary 失败但 RAG 或 KG 成功，可以继续综合可用来源，同时明确 Summary 接口错误；不要自行补造 Reliability。
- 如果没有明确 KG 实体，脚本会做轻量自动抽取；自动抽取失败时会跳过 KG 并给出 warning。调用方应尽量显式传入 `--kg-entity`。
- `502/503/504` 等 KG 临时错误可以重试或减少 `--kg-edge-limit` 做连通性测试；结论性检索不要随意降低边补全数量。
- 如果两个来源都失败，应向用户报告接口错误，不要凭空生成文献或 KG 关系。

## 验证命令

安装或修改后可执行：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" --help
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato late blight resistance" --kg-entity "Phytophthora infestans" --kg-entity "late blight" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format summary
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "potato tuber dormancy genes" --kg-entity "StSP6A|SP6A|SELF-PRUNING 6A" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format json
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_knowledge.py" "GAME9 potato predicted function" --gene-summary "DM8.2_chr01G26640" --kg-entity "GAME9|ERF1" --rag-top-k-retrieve 200 --rag-top-k-rerank 20 --kg-edge-limit 1 --format summary
```
