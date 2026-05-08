---
name: gffread-export-cds-pep
description: 使用 gffread 从基因组 FASTA 和 GFF3/GTF 注释中导出 CDS 与蛋白 FASTA；包含 conda/mamba 安装、标准运行命令、代表转录本注意事项与结果校验。
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [gffread, cds, pep, fasta, gff3, gtf, conda, mamba, bioconda, genome]
    related_skills: []
---

# gffread 导出 CDS 与蛋白序列

适用于：已有 `${GENOME_FASTA}` 和 `${ANNOTATION_GFF3}`，需要批量导出 `${CDS_OUT}` 与 `${PEP_OUT}`。

## 适用前提

- `${GENOME_FASTA}`：参考基因组 FASTA
- `${ANNOTATION_GFF3}`：注释文件（GFF3 或 GTF）
- 注释中应包含 transcript/mRNA 以及 CDS 关系（Parent/ID 正确）
- 染色体/contig 名称必须与 FASTA 和注释一致

## 输入变量

- `GENOME_FASTA`：基因组 FASTA 路径
- `ANNOTATION_GFF3`：注释文件路径
- `CDS_OUT`：输出 CDS FASTA 路径
- `PEP_OUT`：输出蛋白 FASTA 路径
- `ENV_NAME`：conda/mamba 环境名，默认可设为 `gffread`
- `PKG_MGR`：包管理器（`mamba` / `micromamba` / `conda`），由环境可用者决定
- `ANNOTATION_REP_GFF3`（可选）：代表转录本注释；若目标是“每个基因一条序列”，优先用它

## 1. 安装 gffread

优先使用 `mamba` / `micromamba`，其次使用 `conda`。

### 1.1 选择可用的包管理器
```bash
if command -v micromamba >/dev/null 2>&1; then
  PKG_MGR=micromamba
elif command -v mamba >/dev/null 2>&1; then
  PKG_MGR=mamba
elif command -v conda >/dev/null 2>&1; then
  PKG_MGR=conda
else
  echo "No conda/mamba-style package manager found" >&2
  exit 1
fi
```

### 1.2 创建环境并安装
```bash
${PKG_MGR} create -y -n "${ENV_NAME}" -c conda-forge -c bioconda gffread
```

### 1.3 检查版本
```bash
${PKG_MGR} run -n "${ENV_NAME}" gffread --version
```

> 若使用 shared micromamba 且创建环境时报路径权限问题，可先设置可写的 `MAMBA_ROOT_PREFIX` 再执行创建。

## 2. 导出 CDS 与蛋白序列

### 2.1 基本命令
```bash
${PKG_MGR} run -n "${ENV_NAME}" gffread "${ANNOTATION_GFF3}" \
  -g "${GENOME_FASTA}" \
  -x "${CDS_OUT}" \
  -y "${PEP_OUT}"
```

### 2.2 典型输出含义
- `${CDS_OUT}`：按 transcript/mRNA 组装得到的 CDS 序列
- `${PEP_OUT}`：由 CDS 翻译得到的蛋白序列
- 输出 header 通常沿用注释中的 transcript/mRNA ID

## 3. 代表转录本与全量转录本

- 若 `${ANNOTATION_GFF3}` 是**全量注释**，gffread 通常按**转录本/isoform**导出，多个 isoform 会得到多个 FASTA 记录。
- 若希望得到“**每个基因一条 CDS/蛋白**”，应先把注释过滤成代表转录本版本，再把该文件赋给 `${ANNOTATION_GFF3}`。
- 代表转录本文件可以直接作为输入，不必在本技能中重新筛选。

## 4. 结果校验

### 4.1 基础检查
```bash
test -s "${CDS_OUT}"
test -s "${PEP_OUT}"
```

### 4.2 序列条数检查
```bash
grep -c '^>' "${CDS_OUT}"
grep -c '^>' "${PEP_OUT}"
```

通常情况下，CDS 与蛋白的条数应一致。

### 4.3 常见异常
- FASTA 与 GFF3 的序列名称不一致
- 注释缺少 `mRNA/transcript` 或 `CDS`
- `Parent` / `ID` 关系错误
- 输入注释只含 gene 级信息，没有可拼接的 CDS

## 5. 常见坑

- `gffread` 输出的是**转录本级**序列，不是 gene 级；要 gene 级结果需先过滤代表转录本。
- 如果注释有多个剪接异构体，输出会多于基因数。
- 若下游工具不接受终止符 `*`，应在后处理阶段统一清理。
- 若 `gffread` 报 contig mismatch，先检查 `${GENOME_FASTA}` 的 header 与 `${ANNOTATION_GFF3}` 的 seqid 是否一致。

## 6. 推荐执行模板

```bash
# 1) 选择包管理器
if command -v micromamba >/dev/null 2>&1; then
  PKG_MGR=micromamba
elif command -v mamba >/dev/null 2>&1; then
  PKG_MGR=mamba
elif command -v conda >/dev/null 2>&1; then
  PKG_MGR=conda
else
  echo "No conda/mamba-style package manager found" >&2
  exit 1
fi

# 2) 安装
${PKG_MGR} create -y -n "${ENV_NAME}" -c conda-forge -c bioconda gffread

# 3) 导出
${PKG_MGR} run -n "${ENV_NAME}" gffread "${ANNOTATION_GFF3}" -g "${GENOME_FASTA}" -x "${CDS_OUT}" -y "${PEP_OUT}"

# 4) 校验
grep -c '^>' "${CDS_OUT}"
grep -c '^>' "${PEP_OUT}"
```
