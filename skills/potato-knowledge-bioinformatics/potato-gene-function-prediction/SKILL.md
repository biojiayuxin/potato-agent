---
name: potato-gene-function-prediction
description: 当用户想快速知道某个明确马铃薯基因可能具有什么功能时，查询 Potato Agent 基因功能预测接口，返回 predictedFunction、reliabilityGrade、gradeReason 和支撑证据。仅接受唯一的 DMv8.2 gene ID；数据库更新可能滞后，详细功能介绍、最新文献证据或进一步功能推测需结合 potato-gene-search、potato-knowledge-search 或 search-gene-function。
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, gene-function, function-prediction]
    related_skills: [potato-gene-search, potato-knowledge-search, search-gene-function]
prerequisites:
  commands: [python3]
---

# Potato Gene Function Prediction

## Overview

使用 Potato Agent 的只读预测接口，快速查询一个明确马铃薯基因可能具有的功能。接口返回预测功能、可靠性等级、等级原因，以及直接马铃薯证据、同源基因证据和表达证据等结构化信息。

这是快速预测入口，不是完整的基因功能分析流程。预测数据库可能落后于最新注释和文献；不得将预测结果直接写成实验事实。

## When to Use

使用本技能：

- 用户询问某个马铃薯基因“可能是什么功能”“预测功能是什么”或“快速看一下功能”；
- 用户需要对一个明确基因做快速、初步功能判断；
- 用户明确要求查看 Potato Agent 的功能预测、可靠性等级或预测证据。

不要单独使用本技能：

- 用户只提供 symbol、reported ID、历史 ID、DMv8.1 ID 或其他非 DMv8.2 编号；先用 `potato-gene-search` 解析到唯一 DMv8.2 gene ID；
- 用户要查询坐标、转录本、domain、表达详情、参考文献列表或序列；使用 `potato-gene-search`；
- 用户需要详细功能介绍、最新研究证据或文献片段；结合 `potato-knowledge-search`；
- 用户要求系统性同源分析、结构域分析、序列比对或完整功能推测；使用 `search-gene-function`；
- 查询对象不是马铃薯基因。

## Input Resolution

查询参数必须是一个完整、唯一、以 `DM8.2` 开头的 DMv8.2 gene ID，例如：

```text
DM8.2_chr01G26640
```

处理其他输入时：

1. 使用 `potato-gene-search` 检索用户给出的 symbol 或历史编号。
2. 核对候选是否唯一，并取得标准 DMv8.2 gene ID。
3. 候选不唯一时向用户说明歧义，不要自行猜测。
4. 不要把转录本 ID（例如带 `.1` 后缀）直接传给本接口。

## Query Workflow

1. 确认用户要的是快速功能预测，而不是完整功能分析。
2. 解析并核对唯一 DMv8.2 gene ID。
3. 使用 `--predict_summary` 查询接口，默认选择 JSON 输出供后续分析。
4. 核对响应中的 `geneId`、`transcriptId`、`predictedFunction`、`reliabilityGrade`、`gradeReason` 和 `evidence`。
5. 分开说明预测结论和各类证据，不得补造未返回的信息。
6. 提醒用户数据库可能更新滞后；需要详细结论时继续调用相关技能。

## Script

Hermes 加载技能时会将 `${HERMES_SKILL_DIR}` 展开为当前技能的绝对目录。先查看帮助：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" --help
```

推荐使用 JSON：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" \
  --predict_summary "DM8.2_chr01G26640" \
  --format json
```

面向人工快速查看时使用 summary：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" \
  --predict_summary "DM8.2_chr01G26640" \
  --format summary
```

`--format summary` 只在脚本本地整理接口已经返回的字段，不调用额外接口或模型。

关键参数：

```text
--predict_summary GENE_ID  必填；一个完整 DMv8.2 gene ID。
--format json|summary      输出格式，默认 json。
--timeout N                HTTP 超时秒数，默认 60。
--base-url URL             覆盖预测服务地址，仅用于测试或调试。
--max-text-chars N         summary 中每个长字段的最大字符数，0 表示不截断。
--evidence-limit N         summary 中最多展示的表达证据条数，默认 5。
```

服务地址也可通过 `POTATO_GENE_FUNCTION_BASE_URL` 覆盖。

## API Contract

默认服务地址：

```text
https://potato-agent.ynnu.edu.cn
```

脚本调用：

```text
GET /api/v1/genes/{gene_id}/description/evidence
```

成功的 JSON 至少包含：

```json
{
  "geneId": "DM8.2_chr01G26640",
  "transcriptId": "DM8.2_chr01G26640.1",
  "predictedFunction": "...",
  "reliabilityGrade": "...",
  "gradeReason": "...",
  "evidence": {},
  "success": true
}
```

脚本在请求前校验 gene ID；接口返回后校验必需字符串字段和 `evidence` 对象。成功返回退出码 `0`，接口或响应错误返回 `1`，参数错误返回 `2`。

## Evidence Interpretation

- 将 `predictedFunction` 称为“预测功能”或“推测功能”。
- 同时报告 `reliabilityGrade` 和 `gradeReason`，不要只摘取功能描述。
- 仅引用 `evidence` 中实际存在的直接马铃薯证据、同源证据、表达证据和文献标识。
- 同源基因功能只能作为推测依据，不能直接赋予目标马铃薯基因。
- 表达量支持组织或条件相关性，不单独证明具体分子功能。
- 即使可靠性等级较高，也不能把预测改写成已实验验证的事实。
- 回答末尾说明数据库可能更新滞后，并指出进一步查询路径。

## Failure Handling

- gene ID 格式不合法：先用 `potato-gene-search` 核对并解析 ID。
- 接口返回 `404`：说明该 ID 本次未获得预测结果；不能断言该基因没有功能。
- 接口超时、返回非 JSON 或缺少必需字段：报告具体错误，不要自行补全预测。
- 预测结果为空或信息不足：使用 `potato-gene-search` 查询基因注释，并用 `potato-knowledge-search` 检索文献和知识图谱证据。
- 用户要求深入分析：转入 `search-gene-function` 的系统流程。

## Common Pitfalls

1. **把 `--predict_summary` 写成其他参数名。** 必须使用下划线形式的 `--predict_summary`。
2. **直接提交 symbol 或旧版 gene ID。** 先通过 `potato-gene-search` 得到唯一 DMv8.2 gene ID。
3. **只复述 `predictedFunction`。** 同时报告可靠性、等级原因和证据边界。
4. **忽略数据库时效性。** 明确提示预测库可能落后于最新注释与文献。
5. **把同源或表达证据当作直接功能验证。** 按证据类型分别陈述并限制结论强度。
6. **把本技能当作完整功能分析。** 详细介绍和进一步推测应结合相关技能。

## Verification Checklist

- [ ] 输入是唯一、完整的 DMv8.2 gene ID。
- [ ] 命令使用 Hermes 展开的 `${HERMES_SKILL_DIR}`。
- [ ] 查询参数准确写为 `--predict_summary`。
- [ ] 响应包含预测功能、可靠性等级、等级原因和 evidence 对象。
- [ ] 回答将预测、直接证据、同源证据和表达证据分开解释。
- [ ] 未编造接口没有返回的 DOI、文献或功能结论。
- [ ] 已提示数据库可能更新滞后。
- [ ] 详细查询需求已路由到 `potato-gene-search`、`potato-knowledge-search` 或 `search-gene-function`。

## Verification Commands

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" --help
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" --predict_summary "DM8.2_chr01G26640" --format json
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene_function_prediction.py" --predict_summary "DM8.2_chr01G26640" --format summary
```
