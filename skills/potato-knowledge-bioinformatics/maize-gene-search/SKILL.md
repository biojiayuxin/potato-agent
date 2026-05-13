---
name: maize-gene-search
description: 玉米基因检索技能：查找基因名对应的 NAM 5.0 基因号，并基于本地 MaizeGDB 官方数据查询结构域、GO、UniProt 和功能描述；必要时结合 literature-review 进一步检索功能证据。
version: 1.0.0
metadata:
  hermes:
    tags: [maize, corn, Zea_mays, B73, NAM5, MaizeGDB, gene-search, GO, UniProt, InterPro]
    related_skills: [literature-review]
---

# 玉米基因检索（MaizeGDB 本地官方数据）

## 适用场景

当用户需要查询玉米 / maize / corn / Zea mays B73 NAM 5.0 的：

- 基因名、经典 locus、别名对应的 `Zm00001eb` 基因号；
- 已知基因号对应的结构域、GO、UniProt、InterPro、EnTAP 等注释；
- 基因功能描述与进一步文献证据。

## 核心原则

1. **基因名 → 基因号，以及基因号 → 结构域 / GO / UniProt 等信息，使用 MaizeGDB 官方数据。**
   本地保存位置：

   ```text
   /mnt/data/public_data/Genomes/Other_species/Maize/
   ```

2. **查找基因功能描述时，先用 MaizeGDB 本地数据把基因名解析到基因号，再查功能描述。**
   如需更深入的功能证据、已发表实验结果或跨物种背景，可进一步加载并结合 `literature-review` 技能检索文献。

3. 完整数据来源、下载命令、文件清单和注意事项见：

   ```text
   /mnt/data/public_data/Genomes/Other_species/Maize/README.md
   ```

## 常用本地文件

| 查询目的 | 首选文件 |
|---|---|
| 基因名 / 经典 locus → 基因号 | `Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.genemodel_locus.txt` |
| 基因名 / 基因号 → 综合功能描述、GO | `Zm00001eb.1.fulldata.txt` |
| GO / gene products / locus 信息 | `Zm-B73-REFERENCE-NAM-5.0-GMs-GOTerms.csv` |
| 坐标、Alias、UniProt、GO、canonical transcript | `Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3` |
| 结构域 / InterPro / Pfam | `Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.interproscan.tsv` |
| EnTAP 综合注释 | `Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1_entap_results.tsv` |
| UniProt GO | `Zm-B73-REFERENCE-NAM.UniProt.GO.tab` |
| UniProt 蛋白描述 | `Zm-B73-REFERENCE-NAM.UniProt.proteins.tab` |

## 推荐查询流程

```bash
cd /mnt/data/public_data/Genomes/Other_species/Maize

# 1) 基因名/经典 locus → NAM 5.0 基因号
awk -F '\t' 'BEGIN{IGNORECASE=1} NR>1 && $2=="o2" {print $1"\t"$2}' \
  Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.genemodel_locus.txt

# 2) 基因名/经典 locus → 基因号 + 名称/功能/GO
awk -F '\t' 'BEGIN{IGNORECASE=1} $11=="o2" || $12=="o2" {print $2"\t"$11"\t"$12"\t"$13}' \
  Zm00001eb.1.fulldata.txt

# 3) 基因号 → 综合信息
awk -F '\t' '$2=="Zm00001eb301570" {print}' Zm00001eb.1.fulldata.txt

# 4) 基因号 → 结构域 / InterPro / Pfam（按转录本前缀匹配）
awk -F '\t' '$1 ~ /^Zm00001eb301570_/ {print}' \
  Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.interproscan.tsv

# 5) 基因号 → UniProt GO
awk -F '\t' '$1=="Zm00001eb301570" {print}' Zm-B73-REFERENCE-NAM.UniProt.GO.tab

# 6) 基因号 → GFF3 中的 Alias / UniProt / GO / 坐标
awk -F '\t' '$3=="gene" && $9 ~ /ID=Zm00001eb301570(;|$)/ {print}' \
  Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3
```

## 已验证经典基因名示例

| 基因名 | NAM 5.0 基因 ID | 功能/名称 |
|---|---|---|
| `tb1` | `Zm00001eb054440` | teosinte branched1；bHLH/TCP transcription factor |
| `ra1` | `Zm00001eb312340` | ramosa1；zinc-finger transcription factor |
| `su1` | `Zm00001eb174590` | sugary1；isoamylase1 |
| `o2` | `Zm00001eb301570` | opaque endosperm2；BZIP/O2 protein |
| `bt2` | `Zm00001eb176800` | brittle endosperm2；ADP glucose pyrophosphorylase small subunit |
| `sh1` | `Zm00001eb374090` | shrunken1；sucrose synthase |
| `wx1` | `Zm00001eb378140` | waxy1；starch synthase / NDP-glucose-starch glucosyltransferase |

## 何时结合 literature-review

如果用户的问题超出本地注释范围，例如：

- 某基因的已发表实验功能；
- 调控网络、表型、突变体、表达证据；
- 跨物种同源基因功能推断；
- 需要写综述或引用文献；

则在本地 MaizeGDB 数据确认基因号和基础注释后，再加载 `literature-review` 技能继续检索。
