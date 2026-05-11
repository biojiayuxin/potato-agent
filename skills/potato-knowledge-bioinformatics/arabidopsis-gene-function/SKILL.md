---
name: arabidopsis-gene-function
description: 先用 TAIR 基础检索将拟南芥 gene symbol / alias / AGI ID 解析为标准 geneID，并以 TAIR geneID 为准调用 PlantConnectome 查询知识图谱关系和 PMID，用于总结拟南芥基因功能与文献证据。
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

# Arabidopsis Gene Function

用于查询拟南芥（*Arabidopsis thaliana*）基因功能和文献证据。核心原则：

1. **先用 TAIR 确认标准 geneID / AGI locus ID**；TAIR 结果优先级高于 PlantConnectome 的实体名。
2. 如果用户输入的是 gene symbol / alias（如 `STM`），且 TAIR 返回多个合理 geneID，**必须先向用户确认以哪个 ID 为准**，不要直接拿 symbol 去 PlantConnectome 查。
3. 确定 geneID 后，先用该 geneID 在 PlantConnectome 查询知识图谱关系、PMID 和关系依据。
4. 如果 geneID 查询结果很少、明显噪声较多，或 PlantConnectome 把 TAIR 别名串聚合成可疑实体，可再用 **TAIR 已确认属于该 geneID 的长别名/全名** 做辅助查询；最终身份仍以 TAIR geneID 为准。
5. TAIR 详情接口可能需要登录；本技能只依赖 TAIR 不登录可用的基础检索接口。

## 何时使用

- 用户询问拟南芥基因功能，例如“拟南芥 STM 基因有什么功能”。
- 用户给出 AGI ID，如 `AT1G62360`、`AT1G62360.1`，想查功能、别名、描述或证据。
- 用户给出 gene symbol / alias，如 `STM`、`WUS`、`CLV1`，需要先映射到 AGI ID。
- 用户希望结合 TAIR 与 PlantConnectome 的知识图谱/PMID 证据进行总结。

## 不适用范围

- 不是 TAIR 付费/登录详情页替代品；`/api/detail/locus` 和 `/api/detail/gene` 可能返回 401。
- PlantConnectome KG 是自动抽取结果，可能有实体混淆或关系误抽取；最终结论必须以 TAIR geneID 和 PMID 可核验证据为准。
- 不要只用 symbol 直接查 PlantConnectome；symbol 重名会导致错配。

## 推荐工作流

### 1. TAIR 解析 geneID

优先运行脚本：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/arabidopsis-gene-function
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" tair "STM"
```

TAIR 基础接口：

```text
POST https://www.arabidopsis.org/api/search/gene
Content-Type: application/json
{"searchText": "STM"}
```

常见字段：

- `gene_name`: AGI locus ID，例如 `AT1G62360`
- `gene_model_ids`: gene model，例如 `AT1G62360.1`
- `other_names`: symbols / aliases
- `description`: TAIR 基础功能描述
- `keywords`, `keyword_types`: GO/PO/表达部位等基础注释
- `phenotypes`: 表型描述
- `locus_tairObjectId`, `gene_tairObjectId`
- `has_publications`, `is_obselete`

### 2. 多 geneID 时必须确认

如果 TAIR 对一个 symbol 返回多个精确 alias 匹配，例如 `STM` 可能同时匹配：

- `AT1G62360`：SHOOT MERISTEMLESS
- `AT4G37930`：SERINE HYDROXYMETHYLTRANSFERASE 1，别名也含 `STM`

此时回答用户：

```text
TAIR 中 STM 对应多个候选 geneID：
1. AT1G62360 — SHOOT MERISTEMLESS ...
2. AT4G37930 — SERINE HYDROXYMETHYLTRANSFERASE 1 ...
请确认要以哪个 geneID 为准继续查 PlantConnectome。
```

不要擅自选择，除非用户已经明确给出 AGI ID 或完整基因名能唯一确定。

### 3. 以 geneID 查询 PlantConnectome

确定 geneID 后运行：

```bash
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" plant AT1G62360 --max-entities 3
```

PlantConnectome 页面接口（无正式公开 OpenAPI，但可程序化解析）：

```text
GET https://plant.connectome.tools/normal/<geneID>
GET https://plant.connectome.tools/normal/<entity>/results/<entity_type>?uid=<unique_id>
```

脚本会解析：

- 预览页中的 `unique_id`
- 预览页中的 `allRowsData`
- 详情页中的 `const g = ...` 知识图谱边列表

KG 边常见字段：

- `id`, `idtype`
- `target`, `targettype`
- `inter_type`, `edge_disamb`
- `publication`（PMID）
- `p_source`（如 `12070095_abstract`）
- `species`
- `basis`
- `source_extracted_definition`, `target_extracted_definition`

用于总结基因功能时，默认将 PlantConnectome 边压缩为高密度证据单元：`source/id + edge/inter_type或edge_disamb + target + PMID/publication`。通常不提取或展开 `basis`、`source_extracted_definition`、`target_extracted_definition`，除非用户要求核查或边语义不清。

功能总结建议默认先提取 **50 条边**（例如 `--max-edges 50`，`--max-entities 1` 起步），只基于这些高密度证据单元归纳功能。如果功能相关信息明显不足，再逐步扩大到 **100、200** 条边，而不是一开始展开全部边或递归追踪 target node 的 PMID。不要深究 target node 自身对应多少 PMID，除非用户明确要求。

### 4. 一步式查询

当输入是 AGI ID 或 TAIR 只返回一个明确候选时，可直接：

```bash
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" full AT1G62360 --max-entities 3
```

如果 geneID 查询结果很少或明显噪声较多，可同时使用 TAIR 确认的长别名/全名辅助查询：

```bash
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" full AT1G62360 \
  --include-aliases --max-alias-queries 2 --max-entities 3
```

当输入是多义 symbol，脚本会返回 `status: ambiguous`，这时需要先让用户选择。

若用户已确认 geneID，可强制指定：

```bash
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" full STM --gene-id AT1G62360 --max-entities 3
```

### 5. 获取文献片段（可选）

PlantConnectome 详情页的 PubMed 弹窗使用：

```text
POST https://plant.connectome.tools/process-text-withoutapi
{"p_source": "12070095_abstract"}
```

脚本支持：

```bash
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" plant AT1G62360 --snippets 5
```

默认不抓 snippet，避免输出过长。

## 推荐回答结构

对用户总结时建议使用科研论文式证据标注：

1. **TAIR 确认结果**：先给出 geneID、gene model、aliases，并说明身份以 TAIR geneID 为准。
2. **结论逐条标注参考依据**：每条功能结论后用角标编号标注来源，例如 `ICE1 是低温响应上游 bHLH 转录因子，可调控 CBF/DREB1A-COR 通路。<sup>1,2</sup>`。不要把所有参考集中放在段末而不对应具体结论。
3. **参考来源列表**：回答末尾附 `参考来源`，列出编号、来源类型和证据：
   - `TAIR: ATxGxxxxx description / keywords / phenotypes`；
   - `PlantConnectome: PMID, relation, target, basis, p_source（若有）`；
   - 若使用 Expression Atlas 或其他数据库，也列出数据库、endpoint/实验类型和关键字段。
4. **PlantConnectome KG 证据**：优先引用与结论直接对应的高密度证据单元，格式为 `source + edge + target + PMID`；通常不展开 `basis` 或 target node 的其他 PMID，除非用户要求核查或边语义不清。
5. **功能归纳**：用 TAIR 描述作为身份和基础功能锚点，用 PlantConnectome 的 PMID/关系边扩展；不要仅依据关系频次下结论。
6. **证据限制**：PlantConnectome 为自动抽取知识图谱，重要结论需回查 PMID 原文；回答中应区分“数据库注释支持”和“PlantConnectome 自动抽取支持”。

## 脚本用法

```bash
python3 scripts/query_arabidopsis_gene_function.py --help
python3 scripts/query_arabidopsis_gene_function.py tair "STM"
python3 scripts/query_arabidopsis_gene_function.py full AT1G62360 --max-entities 3
python3 scripts/query_arabidopsis_gene_function.py full STM --gene-id AT1G62360 --max-entities 3
python3 scripts/query_arabidopsis_gene_function.py plant AT1G62360 --snippets 3
```

常用参数：

```text
--format json|summary       输出格式，默认 json
--max-candidates N          TAIR 候选返回数量，默认 10
--gene-id AGI               用户已确认的 AGI ID，用于跳过歧义选择
--max-entities N            PlantConnectome 预览实体数量，默认 3
--max-edges N               每个 PlantConnectome 实体最多保留 KG 边数，默认 200
--snippets N                为前 N 个 p_source 拉取文献片段，默认 0
--include-aliases           full 模式下额外用 TAIR 确认的长别名/全名查 PlantConnectome
--max-alias-queries N       最多辅助查询几个长别名/全名，默认 2
--timeout SECONDS           HTTP 超时，默认 60
```

## 验证命令

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/arabidopsis-gene-function
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" tair AT1G62360 --format summary
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" tair STM --format summary
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" full AT1G62360 --max-entities 1 --max-edges 20 --format summary
python3 "$SKILL_DIR/scripts/query_arabidopsis_gene_function.py" full AT1G62360 --include-aliases --max-alias-queries 1 --max-entities 1 --max-edges 20 --format summary
```

预期：

- `AT1G62360` 应唯一解析为 SHOOT MERISTEMLESS / STM。
- `STM` 应提示至少两个精确 alias 候选，需要用户确认。
- `full AT1G62360` 应能返回 PlantConnectome 关系和 PMID。
- 若 geneID 查询的 PlantConnectome 结果明显围绕 `BUM` 等别名噪声，可使用 `--include-aliases` 对 `SHOOT MERISTEMLESS` 等 TAIR 确认长别名做辅助查询，并在回答中区分 geneID 主查询与别名辅助查询。

## 注意事项

- TAIR 对 `curl` 默认 User-Agent 可能返回 403；脚本固定使用浏览器 User-Agent。
- TAIR 的 `searchType: exact` 过滤主要在前端完成；脚本本地会基于 `gene_name` 和 `other_names` 做精确候选判断。
- PlantConnectome 没有发现公开 OpenAPI/Swagger；当前实现基于其网页内嵌数据结构，若网站前端改版需要更新解析逻辑。
- 对 ICE1/AT3G26744 这类高连接实体，预览页通常很快，但第一个详情页可能超过 4 MB，读取完整 HTML 可能需要 2–3 分钟；`--max-edges` 只限制解析后保留的边数，不能减少网页下载量。建议用 `--timeout 300`，并将终端命令超时设为 500 秒以上，或先用 `--max-entities 1` 测试。
- 如果 PlantConnectome 对 geneID 无结果，可尝试用 TAIR 确认的完整名称（如 `SHOOT MERISTEMLESS`）人工补查，但最终仍必须回到 geneID 校验，避免 symbol 重名。
