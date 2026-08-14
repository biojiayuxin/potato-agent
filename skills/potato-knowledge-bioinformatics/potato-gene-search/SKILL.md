---
name: potato-gene-search
description: 查询 Potato Knowledge Hub 基因 API，用于马铃薯 DMv8.2 基因模糊检索与详情获取；支持 Gene ID、symbol、reported ID、转录本、domain、UniProt 相似性、参考文献、基因组坐标和序列。脚本返回本技能职责范围内的 API JSON，由 AI 判断和整理结果。
version: 1.2.3
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, DMv8, gene-search, Potato-Knowledge-Hub, bioinformatics]
    related_skills: [potato-gene-function-prediction, potato-knowledge-search, gffread-export-cds-pep]
prerequisites:
  commands: [python3]
---

# Potato Gene Search

使用 Potato Knowledge Hub 的基因 API 查询马铃薯 DMv8.2 基因。适用于根据基因符号、reported ID、历史注释 ID、局部 ID 或 DMv8.2 gene ID 检索候选基因，并进一步获取基因详情。

## 何时使用

- 用户询问马铃薯 / potato / *Solanum tuberosum* 基因信息。
- 用户给出基因符号，如 `PYL8`、`StCDF1`、`NAC`，希望找到对应 DMv8.2 基因。
- 用户给出 reported ID / 历史 ID，如 `LOC102580526`、`PGSC0003DMG...`、`Soltu.DM...`、`St_E4-63...`。
- 用户给出明确 DMv8.2 gene ID，如 `DM8.2_chr06G09000`，希望查看坐标、domain、转录本、UniProt 相似性、参考文献或序列。

## API 选择规则

1. **输入是明确 DMv8.2 gene ID**（例如 `DM8.2_chr06G09000`）且用户要详情：直接调用 `details`。
2. **输入是 symbol、reported ID、历史 ID、partial ID 或不确定关键词**：先调用 `search`。
3. 用户要求“查详情”但只给 symbol / reported ID：先 `search`，再根据候选选择 `gene_id` 后调用 `details`。
4. 候选选择优先级：
   - `gene_id` 与查询精确匹配；
   - `symbol` 中逗号分隔 token 与查询精确匹配（不区分大小写）；
   - `ID_reported` 中 token 与查询精确匹配；
   - 否则使用 API 返回顺序的第一条 / 最高分结果。
5. 如果多个候选都合理，默认展示前 3 条候选及分数，并说明当前详情基于 top hit；只有当选择会明显改变结论时再询问用户。

## 推荐脚本调用

Hermes 加载技能时会将 `${HERMES_SKILL_DIR}` 展开为当前技能的绝对目录。不要写死部署账号下的技能安装路径。

### 模糊检索 / symbol 检索

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene.py" search "PYL8"
```

省略子命令时默认按 `search` 处理：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene.py" "PYL8"
```

### 详情查询

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene.py" details DM8.2_chr06G09000
```

脚本只负责发送请求并返回本技能职责范围内的 API JSON，不判断响应中的基因、文献或序列字段是否有效，也不添加或改写返回字段。AI 必须结合完整响应判断查询是否成功，并按用户需求整理结果。

## 输出规则

### 面向用户的 search 结果

默认展示：

- `gene_id`
- `symbol`
- `ID_reported`
- `score`

通常展示 top 3 即可，除非用户要求完整列表。

### 面向用户的 details 结果

默认摘要以下字段：

- `ID` / `gene_id`
- `symbols`
- `ID_reported`
- `transID_repre`
- `transID_alt`
- `domain`
- `coordinates`
- `ls_uniprot` 简要数量或前几条
- `ref_info` 中的文献标题、DOI 或年份摘要；该字段可能是 JSON 字符串，由 AI 解析
- `cds`、`pep`、`genomic`、`promoter` 是否存在及序列长度

**强制规则：** 用户没有明确要求时，不要在回答中回显 `cds`、`pep`、`genomic`、`promoter` 的完整内容。

## API 参考

Gene search:

```text
GET https://www.potato-ai.top/api/gene_search?q=<query>
```

Gene details:

```text
GET https://www.potato-ai.top/api/gene_details?id=<DMv8.2_gene_id>
```

`ref_info` 可能由 API 返回为 JSON 字符串。脚本不解析或改写它，由 AI 根据实际响应处理。

## 脚本参数

```text
--base-url URL       默认 https://www.potato-ai.top，也可用 POTATO_GENE_BASE_URL 覆盖
--timeout SECONDS    HTTP 超时时间，默认 60
search QUERY         按关键词检索候选基因
details GENE_ID      查询 DMv8.2 基因详情
```

## 验证命令

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene.py" search "PYL8"
python3 "${HERMES_SKILL_DIR}/scripts/query_potato_gene.py" details DM8.2_chr06G09000
```

API 有时会把“找不到数据”的说明放在普通字段中而不是顶层 `error`，必须由 AI 阅读完整响应后判断，脚本不做语义推断。

## 本地数据库降级方案（API 502/不可用时）

如果 `https://www.potato-ai.top/api/gene_search` 或 `gene_details` 返回 502、超时或暂时不可用，但任务只需要 **symbol → DMv8 gene ID / reported ID** 映射，可在本服务器读取 Potato Knowledge Hub 的本地 SQLite 备份作为降级来源。不要把个人目录写成通用默认值；通过 `POTATO_GENE_DB` 显式提供数据库路径：

```bash
export POTATO_GENE_DB="${POTATO_GENE_DB:?set POTATO_GENE_DB to the local genes.db path}"
python3 - <<'PY'
import os
import sqlite3
p = os.environ["POTATO_GENE_DB"]
cur = sqlite3.connect(p).cursor()
for q in ['BEL5','POTH1','FDL1','SP6A','ABL1','AST1']:
    print('\n###', q)
    rows=cur.execute("""
        select gene_id,gene_symbol,ID_reported,refs,descriptions
        from new_genes
        where coalesce(gene_symbol,'') like ?
           or coalesce(ID_reported,'') like ?
           or coalesce(refs,'') like ?
           or coalesce(descriptions,'') like ?
        limit 20
    """, tuple(['%'+q+'%']*4)).fetchall()
    for r in rows:
        print(r[0], r[1], r[2])
PY
```

本地库表结构：`new_genes(gene_id, gene_symbol, ID_reported, refs, descriptions)`。该方式适合核对基因号与历史 ID；不要把它等同于完整 `details` API，因为 domain、文献题名、序列等辅助表可能不在同一路径。

若需要坐标，可用 DMv8.2 GFF3 中的代表转录本验证：

```bash
GFF=/mnt/data/public_data/Genomes/DMv8/raw_8.2/DMv8.2.repre.gff3
# DM8C10G26150 -> DM8.2_chr10G26150；在 mRNA 行查 Parent=DM8.2_chr10G26150
```

注意 ID 版本：SQLite 常用 `DM8C10G26150`，DMv8.2 GFF3/FASTA 常用 `DM8.2_chr10G26150` / `DM8.2_chr10G26150.1`。

## 注意事项

- 当前环境可能没有 `python` 命令，示例统一使用 `python3`。
- API 返回的序列和参考文献信息可能很长；不要无条件塞进最终回答。
- `ID_reported` 经常包含多个历史版本 ID，向用户展示时可适当截断，但保留关键匹配项。
- 如果 Potato Knowledge Hub API 暂时不可用，应优先尝试本地 SQLite 降级方案；若本地库也不可用，再报告连接或 HTTP 错误，不要编造基因信息。
