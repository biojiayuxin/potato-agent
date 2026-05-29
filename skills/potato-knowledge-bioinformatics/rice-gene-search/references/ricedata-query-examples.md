# RiceData 查询示例

RiceData 基因查询入口：

```text
https://www.ricedata.cn/gene/accessions_switch.aspx
```

返回格式是 HTML 表格，不是 JSON。

## 1. 按基因符号查询

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=Xa21&genenm=&cloned=false&located=false&chro='
```

预期可得到 `Xa21` 的 RiceData GeneID、RAP_Locus、MSU_Locus、NCBI_Locus 和注释。

## 2. 按 RAP locus 查询

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=Os11g0559200&genenm=&cloned=false&located=false&chro='
```

适合用户输入 `OsXXgXXXXXXX` 形式的 RAP/IRGSP 基因号。

## 3. 按 MSU/RGAP locus 查询

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=LOC_Os11g35500&genenm=&cloned=false&located=false&chro='
```

适合用户输入 `LOC_OsXXgXXXXX` 形式的 MSU/RGAP 基因号。

## 4. 按注释关键词查询

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=&genenm=Cytochrome%20P450&cloned=false&located=false&chro='
```

适合用户输入的是功能注释、蛋白家族或中文/英文描述，而不是明确基因号。

## 5. 限定染色体或候选类型

```bash
curl -L 'https://www.ricedata.cn/gene/accessions_switch.aspx?para=Xa21&genenm=&cloned=true&located=false&chro=11'
```

参数说明：

| 参数 | 示例 | 含义 |
|---|---|---|
| `para` | `Xa21` | 基因名、符号或登录号 |
| `genenm` | `Cytochrome P450` | 基因名称或注释关键词 |
| `cloned` | `true` / `false` | 是否只查已克隆基因 |
| `located` | `true` / `false` | 是否只查已定位基因 |
| `chro` | `1`-`12` | 染色体；空值表示全部 |

## 6. 文献检索关键词示例

RiceData 确认基因符号后，用 literature-review 检索：

```text
Xa21 rice
pi21 rice
OsRLK8 rice
```

输出功能总结时，优先附 PMID 或 DOI。
