---
name: maize-genome-resources
description: 玉米 (Zea mays B73) 基因组资源快速查询与下载；覆盖 Ensembl Plants REST API 基因查询、MaizeGDB 数据站文件结构、NAM 5.0 文件清单与下载方法。
version: 1.0.0
metadata:
  hermes:
    tags: [maize, corn, B73, NAM5, EnsemblPlants, MaizeGDB, genome, cross-species]
    related_skills: [literature-review, gffread-export-cds-pep, slurm-for-long-running-tasks]
---

# 玉米基因组资源 (Maize B73 NAM 5.0)

快速查询玉米基因信息、下载基因组与注释数据。

## 一、基因查询：Ensembl Plants REST API（首选）

**这是最便捷、无 Cloudflare 阻挡的玉米基因查询方式。**

玉米基因组版本：**Zm-B73-REFERENCE-NAM-5.0**（B73 品系，GCA_902167145.1）

### 符号 → 基因 ID

```bash
# 基因符号 → Ensembl gene ID
curl -s 'https://rest.ensembl.org/xrefs/symbol/zea_mays/{GENE_SYMBOL}?content-type=application/json'
# 例: tb1 → Zm00001eb054440, ra1 → Zm00001eb312340, su1 → Zm00001eb174590
```

### 基因 ID → 详细信息

```bash
curl -s -H 'Content-Type: application/json' 'https://rest.ensembl.org/lookup/id/{GENE_ID}?expand=1'
# 返回: 位置、转录本、CDS、蛋白、组装版本等
```

### 直系同源查询

```bash
curl -s 'https://rest.ensembl.org/homology/id/{GENE_ID}?type=orthologues;content-type=application/json'
```

### 组装信息

```bash
curl -s 'https://rest.ensembl.org/info/assembly/zea_mays?content-type=application/json'
```

> **注意**: 部分经典短符号（bt2, o2, sh1, wx1）在 Ensembl NAM 5.0 注释中可能未注册，需用全名或其他 ID 搜索。

---

## 二、基因 ID 前缀识别

| 前缀 | 版本 | 说明 |
|------|------|------|
| `Zm00001eb` | NAM 5.0 ✅ | 当前正式注释 (`eb` = 带 `b` 的正式版) |
| `Zm00001e` | NAM 5.0 初步 | ❌ 废弃！README 明确说不要用 |
| `Zm00001d` | GRAMENE 4.0 (B73 v4) | 上一代版本 |
| `GRMZM2G` | RefGen_v3 | 经典遗产 |

---

## 三、MaizeGDB 数据下载

### 站点可达性

| URL | 状态 | 说明 |
|-----|------|------|
| `www.maizegdb.org` | ⚠️ Cloudflare | 主站需浏览器 |
| `api.maizegdb.org` | ⚠️ Cloudflare | API 需浏览器 |
| **`download.maizegdb.org`** | ✅ 畅通 | **wget/curl 直连！** |
| `maizemine.maizegdb.org` | ⚠️ Cloudflare | MaizeMine 需浏览器 |

### B73 版本目录

```
download.maizegdb.org/
  ├── B73_RefGen_v1/                     (遗产)
  ├── B73_RefGen_v2/                     (遗产)
  ├── B73_RefGen_v3/                     (遗产)
  ├── Zm-B73-REFERENCE-GRAMENE-4.0/      (v4, Zm00001d)
  ├── Zm-B73-REFERENCE-NAM-5.0/          (v5, Zm00001eb) ← 当前
  ├── All_gene_model_GFF/                ← GFF3 在这里！
  └── GeneFunction_and_Expression/        ← 功能注释
```

### ⚠️ 关键坑：GFF3 不在 NAM 5.0 目录里！

- GFF3 注释：`All_gene_model_GFF/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz`
- NAM 5.0 目录只有 `.nc.gff3.gz`（非编码基因注释），主 GFF3 不在此目录
- 拼接 URL 时切记 GFF3 用 `${BASE}/All_gene_model_GFF/` 前缀

### NAM 5.0 核心文件清单

```
Zm-B73-REFERENCE-NAM-5.0.fa.gz                              (~645 MB) 基因组
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.cds.fa.gz              (~17 MB)  全量 CDS
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.protein.fa.gz          (~10 MB)  全量蛋白
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.canonical.cds.fa.gz    (~14 MB)  Canonical CDS
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.canonical.cdna.fa.gz   (~19 MB)  Canonical cDNA
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.cdna.fa.gz             (~28 MB)  全量 cDNA
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gene.fa.gz             (~52 MB)  基因区段序列
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1_entap_results.tsv.gz   (~12 MB)  EnTAP 功能注释
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.interproscan.tsv.gz    (~3.5 MB) InterProScan
Zm-B73-REFERENCE-NAM-5.0-GMs-GOTerms.csv.gz                 (~3.6 MB) GO Terms
Zm00001eb.1.fulldata.txt.gz                                 (~3 MB)   全量基因数据表
Zm-B73-REFERENCE-NAM.UniProt.GO.tab.gz                      (~0.4 MB) UniProt GO
Zm-B73-REFERENCE-NAM.UniProt.proteins.tab.gz                (~2.4 MB) UniProt 蛋白
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.canonical_transcripts.gz (~0.1 MB) Canonical 转录本
Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.genemodel_locus.txt.gz (~0.1 MB) 基因-locus 映射
```

**MaizeGDB 已预提取 CDS/蛋白/cDNA，通常无需手动 gffread**。如需自定义提取才用 gffread 方案。

---

## 四、推荐下载方案

```bash
BASE_URL="https://download.maizegdb.org"
NAM5_URL="${BASE_URL}/Zm-B73-REFERENCE-NAM-5.0"
GFF_URL="${BASE_URL}/All_gene_model_GFF"

# 基因组
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0.fa.gz"

# GFF3 注释（⚠️ 不在 NAM5 目录！）
wget -c "${GFF_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz"

# 预提取序列
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.cds.fa.gz"
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.protein.fa.gz"
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.canonical.cds.fa.gz"

# 功能注释
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1_entap_results.tsv.gz"
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.interproscan.tsv.gz"
wget -c "${NAM5_URL}/Zm-B73-REFERENCE-NAM-5.0-GMs-GOTerms.csv.gz"
wget -c "${NAM5_URL}/Zm00001eb.1.fulldata.txt.gz"
```

总数据量 ~760MB，建议用 Slurm 后台下载。

---

## 五、与其他资源的交叉

- `literature-review`：跨物种文献查询与直系同源比对
- `gffread-export-cds-pep`：如需自定义提取 CDS/蛋白可用此技能
- `slurm-for-long-running-tasks`：下载大文件时提交 Slurm 作业
