---
name: arabidopsis-gene-search
description: 解析拟南芥 gene symbol、alias、AGI gene ID 或转录本 ID，并按 TAIR 确认的基因名称逐组检索 PlantConnectome 和 PubMed 功能证据。用于拟南芥基因身份消歧、基础注释、关系证据、PMID/DOI 文献检索和有来源约束的功能总结。
version: 2.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [Arabidopsis, Arabidopsis-thaliana, TAIR, PlantConnectome, PubMed, gene-search, PMID, DOI]
    related_skills: [literature-review]
prerequisites:
  commands: [python3]
---

# Arabidopsis Gene Search

查询拟南芥（*Arabidopsis thaliana*）基因身份、TAIR 注释、PlantConnectome 关系和 PubMed 文献证据。

## 固定流程

1. 先用 TAIR 将输入解析为标准 AGI gene ID。输入可以是 AGI ID、转录本 ID、symbol 或 alias。
2. TAIR 返回多个合理候选时，停止完整检索并让用户确认 gene ID；不要擅自合并候选。
3. 从已选 TAIR 记录的 `other_names` 取名称，清理空白后按 NFKC 和大小写无关规则稳定去重。不要用 LLM 筛选、改写或补充基因名。
4. `full` 模式默认保留全部去重名称，并按 TAIR 顺序对每个名称依次查询 PlantConnectome、再查询 PubMed。用 `--max-gene-names` 才能显式限制名称数。
5. PlantConnectome 的合法 `not_found` 不影响 TAIR 身份，也不阻止同组 PubMed 查询。网络、解析或结构校验错误则整次命令失败，避免把不完整来源当作成功。
6. 汇总时以 TAIR selected record 为身份基准，并保持每个 `gene_name` 的 PlantConnectome 和 PubMed 证据相互隔离。

脚本不调用任何 LLM。证据总结由加载本技能的 Hermes Agent 完成。

## 推荐命令

先检查 symbol 或 alias 是否歧义：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" tair STM --format summary
```

对明确 AGI ID 执行完整证据检索：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" full AT1G62360 \
  --max-entities 3 --max-edges 50 --pubmed-limit 20 --format json
```

若用户已从 TAIR 候选中确认 gene ID：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" full STM \
  --gene-id AT1G62360 --max-entities 3 --max-edges 50 \
  --pubmed-limit 20 --format json
```

名称很多时，可显式限制检索数量；截断顺序始终与 TAIR 一致：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" full AT1G62360 \
  --max-gene-names 3 --max-entities 1 --max-edges 20 \
  --pubmed-limit 20 --deadline 300 --format json
```

只诊断 PlantConnectome 原始查询时使用 `plant`。该模式绕过 TAIR，可以接收 AGI ID 或名称，因此不得用它自行确认基因身份：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" plant STM \
  --max-entities 1 --max-edges 20 --format summary
```

## 输出结构与状态

`full --format json` 的关键字段：

- `tair.selected`：身份基准，包括 `gene_id`、gene models、`other_names`、描述、关键词和表型。
- `candidate_gene_names`：TAIR `other_names` 的完整确定性去重结果。
- `retrieval_gene_names`：实际逐名检索的名称；只受 `--max-gene-names` 或 `--no-name-searches` 影响。
- `plantconnectome`：AGI ID 的兼容性直接查询；仅作补充诊断。
- `plantconnectome_searches`：按 `gene_name` 分组的关系证据。每组状态可为 `ok` 或合法的 `not_found`。
- `pubmed`：按同一 `gene_name` 分组的检索式和论文；论文包含 PMID、DOI、题名、年份及截断摘要。
- `database_evidence`：TAIR selected record 和四字段关系 `entity_1 / relationship / entity_2 / citation` 的紧凑视图。

需要基于题名和摘要综合功能时使用 JSON。`summary` 只展示每组前 10 篇论文及最多 350 字符的摘要预览，适合人工检查和冒烟测试。

状态码：

- `status: ok`，退出码 `0`：TAIR 身份已确认；某些 PlantConnectome 组可以没有结果。
- `status: ambiguous`，退出码 `2`：必须让用户确认 AGI ID。
- `status: not_found`，退出码 `3`：TAIR 或低层 `plant` 模式未找到目标。
- 其它错误退出码 `1`：HTTP、超时、响应过大或上游结构变化；不要据此生成成功结论。

## 证据使用规则

- TAIR 决定基因身份和基础描述。`gene_name` 是标准 AGI gene ID；`gene_model_ids` 是转录本/模型 ID；`other_names` 是检索名称。
- PlantConnectome 是自动抽取知识图谱，可能混入同名实体、相似家族成员或其它物种。只采用关系本身能明确关联目标 AGI ID 或当前名称组的记录。
- 不要把一个名称组的关系或论文无依据地转移到另一组，也不要凭关系频次推断功能。
- PubMed 题名或摘要必须明确涉及当前名称、目标 AGI ID，或提供两者等价关系；仅命中相似名称或家族成员时不得归因。
- 关键结论优先引用 TAIR、PMID 或 DOI。只能使用输出中实际出现的引用，不得补造实验、功能或文献。
- 没有基因特异证据时，明确说明未检出；不要把宽泛注释扩写成已验证功能。

## 常用参数

```text
--format json|summary       输出格式，默认 json
--gene-id AGI               为歧义输入指定用户确认的 AGI ID
--max-candidates N          TAIR 候选上限，默认 10
--max-gene-names N          最多检索前 N 个确定性去重名称；默认全部
--no-name-searches          跳过逐名 PlantConnectome 和 PubMed，仅保留 TAIR/AGI 查询
--max-entities N            每个 PlantConnectome 名称最多解析的实体数，默认 3
--max-edges N               每个实体最多保留的关系数，默认 50
--snippets N                为前 N 个 PlantConnectome p_source 拉取片段，默认 0
--pubmed-limit N            每个名称最多返回的 PubMed 论文数，默认 20
--pubmed-base-url URL       NCBI E-utilities 基础 URL
--timeout SECONDS           单次 socket 操作超时，默认 60
--retries N                 首次失败后的额外重试次数，范围 0-10，默认 3
--retry-backoff SECONDS     指数退避初始秒数，默认 1
--deadline SECONDS          显式设置整个命令共享的墙钟时间上限；Linux CLI 另用进程定时器强制中断
--max-response-bytes N      压缩前及解压后响应上限，默认 16 MiB
```

`--include-aliases` 和 `--max-alias-queries` 作为旧命令兼容别名保留；新任务使用默认行为和 `--max-gene-names`。

## 验证

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" tair AT1G62360 --format summary
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" tair STM --format summary
python3 "${HERMES_SKILL_DIR}/scripts/query_arabidopsis_gene_search.py" full AT1G62360 \
  --max-gene-names 1 --max-entities 1 --max-edges 5 \
  --pubmed-limit 1 --deadline 180 --format summary
```

预期：`AT1G62360` 唯一解析为 SHOOT MERISTEMLESS / STM；`STM` 返回多个精确 alias 候选并要求确认；完整查询同时出现按名称分组的 PlantConnectome 和 PubMed 部分。

## 限制

- TAIR 详情接口可能需要登录；脚本只使用无需登录的基础检索接口，并设置浏览器 User-Agent。
- PlantConnectome 数据来自网页内嵌结构；前端改版会触发明确解析错误。内嵌载荷限制为 8 MiB、结构项 500,000、嵌套 200 层；Python literal 在构建 AST 前另限制为 500,000 个词法令牌，构建后再限制为 500,000 个 AST 节点。
- 所有 HTTP 响应都受压缩体和流式解压后大小限制。瞬态 408/425/429/5xx、超时和临时连接错误会指数退避重试；普通 4xx、解析错误及合法 `not_found` 不重试。
- 多个名称会增加串行网络请求数。优先保留默认完整流程；只有在调用预算明确受限时才使用 `--max-gene-names`，并在回答中说明截断。
