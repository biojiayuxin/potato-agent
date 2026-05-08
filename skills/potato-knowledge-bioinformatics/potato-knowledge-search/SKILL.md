---
name: potato-knowledge-search
description: 检索马铃薯文献 RAG 知识数据库的技能。遇到需要查找马铃薯相关知识数据库、Potato Knowledge Hub 文献证据、DOI、论文标题或原文片段时使用；返回 rank、score、title、doi 与 text，并基于检索片段进行归纳。
version: 1.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, knowledge-search, rag, literature, retrieval, doi, Potato-Knowledge-Hub]
    related_skills: [literature-review, potato-gene-search]
required_commands:
  - python3
---

# Potato Knowledge Search

## 触发条件

当用户需要检索 **马铃薯相关知识数据库** 或 **Potato Knowledge Hub 文献 RAG 数据库** 时使用本技能。典型场景包括：

- 用户明确说“查马铃薯知识库 / 搜马铃薯文献数据库 / 查 RAG 数据库 / Potato Knowledge Hub”；
- 询问 potato / *Solanum tuberosum* 的基因、性状、病害、栽培、生理、组学、育种、品种或胁迫响应等问题，并希望获得文献依据；
- 需要 DOI、论文标题、RAG 原文片段、相关论文证据或可追溯出处；
- 需要先从马铃薯知识库检索证据，再进行归纳、证据整理或回答。

简言之：**遇到需要查找马铃薯相关知识数据库时，优先使用本技能。**

## 不适用范围

- 本技能不是通用联网搜索，也不是完整系统综述工具；它返回的是 RAG 检索片段，不保证覆盖所有文献。
- 对非马铃薯、非植物科学或需要最新新闻/网页事实的问题，优先使用相应 web/research 工具。
- 不要编造 RAG JSON 中没有的 DOI、作者、期刊、年份或结论。

## API 合约

部署服务：

```text
POST https://www.potato-ai.top/api/rag/search
```

默认请求体：

```json
{
  "query": "text to retrieve",
  "top_k_retrieve": 200,
  "top_k_rerank": 20
}
```

典型响应：

```json
{
  "success": true,
  "query": "text to retrieve",
  "results": [
    {
      "rank": 1,
      "score": 0.9984,
      "doi": "10.xxxx/xxxxx",
      "title": "Paper title",
      "text": "Retrieved literature snippet"
    }
  ]
}
```

## 推荐工作流

1. **优先使用配套脚本调用 API**，不要手写重复 curl 逻辑。
2. 默认使用：`top_k_retrieve=200`、`top_k_rerank=20`；连通性测试可临时降低到 `20/1` 或 `20/2`。
3. 若结果交给下游工具继续处理，保留默认 JSON 输出。
4. 若用户需要可读回答，基于 JSON 结果整理：`rank`、`title`、`doi`、`score`、关键 `text` 片段。
5. 多个结果来自同一 DOI/标题时，最终回答可合并去重，但不要丢失原始 rank 信息。
6. 如果 API 失败或无结果，明确说明“RAG 未返回可用结果/接口错误”，不要凭空补文献。

## 脚本用法

在 Hermes 环境中，脚本位于本技能目录的 `scripts/query_potato_rag.py`。本服务器通常可直接运行：

```bash
python3 /mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/potato-knowledge-search/scripts/query_potato_rag.py \
  "potato late blight resistance"
```

在技能目录内也可用相对路径：

```bash
python3 scripts/query_potato_rag.py "potato late blight resistance"
```

常用参数：

```bash
python3 scripts/query_potato_rag.py "query text" \
  --top-k-retrieve 200 \
  --top-k-rerank 20 \
  --base-url https://www.potato-ai.top \
  --format json
```

可选输出格式：

```bash
# 面向人类阅读的摘要
python3 scripts/query_potato_rag.py "potato tuber dormancy genes" --format summary

# TSV，便于表格处理
python3 scripts/query_potato_rag.py "potato starch biosynthesis" --format tsv
```

`--base-url` 也可通过环境变量 `POTATO_RAG_BASE_URL` 设置。脚本仅依赖 Python 标准库。

## 回答规范

面向用户输出时建议使用以下结构：

```text
检索问题：...
RAG 返回 N 条结果。代表性证据：
1. 标题：...
   DOI：...
   score：...
   片段：...
2. ...

基于这些片段，可以概括为：...
```

注意：

- `score` 是 RAG 相关性分数，不等同于论文质量或证据强度。
- DOI 缺失时写“未返回 DOI”，不要自动补 DOI。
- 标题缺失时写“未返回标题”，不要根据片段猜标题。
- 文献片段可能是局部上下文，做强结论前应提示“基于检索片段”。
- 如果用户要求“完整综述/系统综述”，可先用本技能获得马铃薯领域证据，再结合通用文献综述技能扩展检索。

## 故障处理

- 当前服务器没有 `python` 命令时，使用 `python3`。
- 如果请求超时，可增加 `--timeout` 或降低 `--top-k-retrieve/--top-k-rerank` 做测试。
- 如果返回非 JSON、HTTP 错误或 `success=false`，把错误信息反馈给用户，并停止编造后续文献结论。

## 验证命令

安装或修改后可执行：

```bash
python3 scripts/query_potato_rag.py --help
python3 scripts/query_potato_rag.py "potato late blight resistance" --top-k-retrieve 20 --top-k-rerank 1
```
