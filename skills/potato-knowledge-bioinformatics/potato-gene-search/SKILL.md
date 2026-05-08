---
name: potato-gene-search
description: 查询 Potato Knowledge Hub 基因 API，用于马铃薯 DMv8 基因模糊检索与详情获取；支持 Gene ID、symbol、reported ID、转录本、domain、表达、UniProt 相似性、参考文献、基因组坐标和按需序列输出。details 默认不返回 cds/pep/genomic/promoter 完整序列，避免上下文膨胀。
version: 1.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, DMv8, gene-search, Potato-Knowledge-Hub, bioinformatics]
    related_skills: [potato-knowledge-search, extract-cds-pep-from-gff3, gffread-export-cds-pep]
prerequisites:
  commands: [python3]
---

# Potato Gene Search

使用 Potato Knowledge Hub 的基因 API 查询马铃薯 DMv8 基因。适用于根据基因符号、reported ID、历史注释 ID、局部 ID 或 DMv8 gene ID 检索候选基因，并进一步获取基因详情。

## 何时使用

- 用户询问马铃薯 / potato / *Solanum tuberosum* 基因信息。
- 用户给出基因符号，如 `PYL8`、`StCDF1`、`NAC`，希望找到对应 DMv8 基因。
- 用户给出 reported ID / 历史 ID，如 `LOC102580526`、`PGSC0003DMG...`、`Soltu.DM...`、`St_E4-63...`。
- 用户给出明确 DMv8 gene ID，如 `DM8C06G10190`，希望查看坐标、domain、转录本、表达、UniProt 相似性、参考文献或序列。

## API 选择规则

1. **输入是明确 DMv8 gene ID**（例如 `DM8C06G10190`）且用户要详情：直接调用 `details`。
2. **输入是 symbol、reported ID、历史 ID、partial ID 或不确定关键词**：先调用 `search`。
3. 用户要求“查详情”但只给 symbol / reported ID：先 `search`，再根据候选选择 `gene_id` 后调用 `details`。
4. 候选选择优先级：
   - `gene_id` 与查询精确匹配；
   - `symbol` 中逗号分隔 token 与查询精确匹配（不区分大小写）；
   - `ID_reported` 中 token 与查询精确匹配；
   - 否则使用 API 返回顺序的第一条 / 最高分结果。
5. 如果多个候选都合理，默认展示前 3 条候选及分数，并说明当前详情基于 top hit；只有当选择会明显改变结论时再询问用户。

## 推荐脚本调用

加载技能后，优先使用 `skill_view` 返回的 `skill_dir`，或使用本地默认路径：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/potato-gene-search
```

### 模糊检索 / symbol 检索

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" search "PYL8"
```

省略子命令时默认按 `search` 处理：

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" "PYL8"
```

### 详情查询：默认不返回完整序列

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" details DM8C06G10190
```

默认输出会移除以下完整序列字段，避免污染上下文：

```text
cds, pep, genomic, promoter
```

脚本会保留 `sequence_summary`，用于说明这些序列是否存在、FASTA header 和序列长度；同时给出 `sequence_fields_omitted`。

### 只有用户明确要求时才输出完整序列

用户明确要求 CDS、蛋白、基因组序列、启动子序列、FASTA 或完整 sequence 时，再使用：

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" details DM8C06G10190 --include-sequences
```

只输出部分序列字段时使用：

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" details DM8C06G10190 --include-sequences --sequence-fields cds,pep
```

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
- `ls_exp` 简要说明
- `ref_info_parsed` 中的文献标题、DOI 或年份摘要
- `sequence_summary`，只报告序列长度，不输出完整序列

**强制规则：** 用户没有明确要求时，不要在回答中回显 `cds`、`pep`、`genomic`、`promoter` 的完整内容。

## API 参考

Gene search:

```text
GET https://www.potato-ai.top/api/gene_search?q=<query>
```

Gene details:

```text
GET https://www.potato-ai.top/api/gene_details?id=<DMv8_gene_id>
```

`ref_info` 由 API 返回为 JSON 字符串。脚本保留原始 `ref_info`，并在可解析时增加 `ref_info_parsed` 列表。

## 脚本参数

```text
--base-url URL       默认 https://www.potato-ai.top，也可用 POTATO_GENE_BASE_URL 覆盖
--timeout SECONDS    HTTP 超时时间，默认 60
search QUERY         按关键词检索候选基因
search --max-results N QUERY
                    只保留前 N 条 search 结果

details GENE_ID      查询详情，默认移除完整序列字段
details --include-sequences GENE_ID
                    保留完整 cds/pep/genomic/promoter 序列字段
details --include-sequences --sequence-fields cds,pep GENE_ID
                    只保留指定完整序列字段，其余序列字段仍省略
```

## 验证命令

```bash
python3 "$SKILL_DIR/scripts/query_potato_gene.py" search "PYL8"
python3 "$SKILL_DIR/scripts/query_potato_gene.py" details DM8C06G10190
python3 "$SKILL_DIR/scripts/query_potato_gene.py" details DM8C06G10190 --include-sequences --sequence-fields cds,pep
```

验证默认详情输出中不应包含顶层 `cds`、`pep`、`genomic`、`promoter` 字段；显式 `--include-sequences` 后才应返回对应字段。

## 本地数据库降级方案（API 502/不可用时）

如果 `https://www.potato-ai.top/api/gene_search` 或 `gene_details` 返回 502、超时或暂时不可用，但任务只需要 **symbol → DMv8 gene ID / reported ID** 映射，可在本服务器读取 Potato Knowledge Hub 的本地 SQLite 备份作为降级来源：

```bash
DB=/mnt/data/jiayuxin/potato_knowledge_hub/260330-add_visit_map/scripts/search_genes/genes.db
python3 - <<'PY'
import sqlite3
p='/mnt/data/jiayuxin/potato_knowledge_hub/260330-add_visit_map/scripts/search_genes/genes.db'
cur=sqlite3.connect(p).cursor()
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

本地库表结构：`new_genes(gene_id, gene_symbol, ID_reported, refs, descriptions)`。该方式适合核对基因号与历史 ID；不要把它等同于完整 `details` API，因为 domain、表达、文献题名、序列等辅助表可能不在同一路径。

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
