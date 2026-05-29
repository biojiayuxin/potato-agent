---
name: rice-gene-search
description: 根据用户输入的水稻基因号或基因名，先用 RiceData 查询 RAP/MSU/NCBI 基因号、基因名与注释，再用 literature-review 检索“基因名 rice”文献并总结功能证据（附 PMID/DOI）。
version: 2.0.0
metadata:
  hermes:
    tags: [rice, Oryza_sativa, RiceData, gene-search, RAP, MSU, RGAP, PMID, DOI]
    related_skills: [literature-review]
---

# 水稻基因检索与功能证据总结

## 定位

当用户输入水稻基因名、基因符号或基因号时，用本技能完成两件事：

1. 通过 RiceData 查询该输入对应的水稻基因基础信息：基因名/符号、RAP 基因号、MSU/RGAP 基因号、NCBI 基因号和页面注释。
2. 优先以确认到的基因名/符号和 `rice` 为关键词，使用 `literature-review` 技能检索文献；如果该候选只有基因号、没有基因名/符号，则使用基因号和 `rice` 检索。概括该基因在水稻中的已报道功能，并附 PMID 或 DOI。

## 适用输入

- 基因符号/基因名：如 `Xa21`、`pi21`、`OsRLK8`、`D14`。
- RAP 基因号/locus：如 `Os11g0559200`。不要用 RAP 转录本号（如 `Os11t0559200-01`）直接查询 RiceData URL，通常查不到结果。
- MSU/RGAP 基因号/locus：如 `LOC_Os11g35500`。不要用带转录本/模型后缀的编号（如 `LOC_Os11g35500.1`）直接查询 RiceData URL，通常查不到结果。
- NCBI 基因号及其它基因级登录号。
- 注释关键词：如 `Cytochrome P450`、`细胞色素P450`。

## 核心流程

### 1. 用 RiceData 查询基础信息

主查询入口：

```text
https://www.ricedata.cn/gene/accessions_switch.aspx
```

默认把用户输入作为 `para` 查询。注意：RiceData 这个 URL 查询应使用**基因级编号**，不要直接使用转录本号；转录本号通常查不到结果。若用户给的是明显的转录本号，先还原/替换为对应的基因号后再查询，例如 `Os11t0559200-01` 应改为 `Os11g0559200`，`LOC_Os11g35500.1` 应改为 `LOC_Os11g35500`。

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=Xa21&genenm=&cloned=false&located=false&chro='
```

如果用户输入明显是功能描述或注释关键词，可作为 `genenm` 查询：

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=&genenm=Cytochrome%20P450&cloned=false&located=false&chro='
```

返回结果是 HTML 表格，不是 JSON。提取以下列即可：

| 字段 | 说明 |
|---|---|
| `GeneID` | RiceData 内部基因编号 |
| `基因名称或注释` | RiceData 给出的中文/英文注释 |
| `基因符号` | 基因名或符号 |
| `RAP_Locus` | RAP-DB/IRGSP locus ID |
| `MSU_Locus或其它` | MSU/RGAP `LOC_Os...` 或其它 locus |
| `NCBI_Locus` | NCBI Gene locus |
| `cDNAs` / `RefSeq_Locus` / `Uniprots` | 其它数据库登录号 |

若返回多个候选，先展示候选表；后续文献检索优先使用最匹配用户输入的候选基因符号/基因名。不要凭空合并多个候选。

### 2. 用 literature-review 检索功能文献

加载并使用 `literature-review` 技能。

推荐关键词优先级：

1. 若 RiceData 结果中有明确基因符号或基因名，优先使用：

```text
<基因符号或基因名> rice
```

2. 若候选只有基因号、没有明确基因名/符号，则使用可用基因号检索；优先用 RAP_Locus，其次用 MSU/RGAP locus 或其它登录号：

```text
<基因号> rice
```

示例：

```text
Xa21 rice
OsRLK8 rice
pi21 rice
Os11g0559200 rice
LOC_Os11g35500 rice
```

检索时优先保留：

- 题名、摘要或关键词明确包含目标基因名/符号和 rice / Oryza sativa 的文献；
- 有 PMID 或 DOI 的文献；
- 原始功能研究优先于综述；
- 与 RiceData 候选基因号一致或能互相印证的文献。

### 3. 汇总功能描述

根据文献证据，输出简洁功能总结。每条关键功能结论尽量附至少一个 PMID 或 DOI。没有查到 PMID/DOI 时明确写“未检出 PMID/DOI”，不要编造。

## 推荐输出格式

```markdown
## 查询结果

输入：<用户输入>

| GeneID | 基因符号 | RAP_Locus | MSU_Locus/其它 | NCBI_Locus | RiceData 注释 |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |

## 功能总结

<2-5 句概括该基因在水稻中的主要功能、通路/性状、已报道表型或调控作用。>

## 文献证据

| 结论 | 文献 | PMID | DOI |
|---|---|---|---|
| ... | ... | ... | ... |

## 注意

<如存在多个候选、证据不足、同名基因歧义，在这里说明。>
```

## 简洁 Python 解析示例

```python
import pandas as pd
import urllib.parse

base = "https://www.ricedata.cn/gene/accessions_switch.aspx"
params = {
    "para": "Xa21",
    "genenm": "",
    "cloned": "false",
    "located": "false",
    "chro": "",
}
url = base + "?" + urllib.parse.urlencode(params)
tables = pd.read_html(url)
result = tables[0]
print(result)
```

更多查询示例见 `references/ricedata-query-examples.md`。

## 注意事项

- RiceData 查询入口返回 HTML 页面；不要期待 JSON API。
- 结果可能有广告链接或“买抗体/买突变体”等页面文本，输出时去除这些无关内容。
- 如果一个输入返回多个候选，先报告候选，不要擅自认定唯一基因。
- 功能总结必须来自文献检索结果；没有文献证据时只能报告 RiceData 注释，不能扩写为已验证功能。
- PMID/DOI 必须来自检索结果或论文页面元数据；不确定时写“未检出”。
