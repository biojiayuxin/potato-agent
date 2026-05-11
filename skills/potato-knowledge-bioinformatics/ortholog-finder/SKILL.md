---
name: ortholog-finder
description: 基于 genome-synteny 前置 MCScan/jcvi 比对步骤寻找两个基因组间直系同源候选基因；复用 GFF3 转 BED、FASTA 链接、ID 检查与 catalog ortholog，并将 .last.filtered 按最佳匹配过滤为 .one_to_one.best.tsv。
version: 0.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [ortholog, jcvi, mcscan, diamond, synteny, protein, gff3, potato]
---

# Ortholog Finder（复用 genome-synteny 前置比对）

## 触发条件

当用户要求：

- 在两个马铃薯或植物基因组之间寻找直系同源/同源候选基因对；
- 已有 GFF3 + 蛋白 FASTA 或 CDS FASTA，需要产出可复用的一对一直系同源候选基因表；
- 需要 MCScan/jcvi 的前置序列相似性过滤结果，并进一步去除一对多和多对一冲突；

## 核心原则

1. 本技能复用 `genome-synteny` 的前置步骤：
   - GFF3 转 BED；
   - FASTA 标准化链接为 `<prefix>.pep` 或 `<prefix>.cds`；
   - 检查 BED 第 4 列基因 ID 是否存在于 FASTA header；
   - 运行 `python -m jcvi.compara.catalog ortholog` 生成 `results/<speciesA>.<speciesB>.last.filtered`；
   - 将 `.last.filtered` 按最佳匹配优先级过滤为最终结果 `results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv`。
2. 默认不继续做 `screen --simple`、`.anchors.simple` 和绘图。
3. 一对一最佳过滤采用贪心策略：先按 `bitscore` 高、`identity` 高、`alignment_length` 长、`evalue` 小、`mismatches + gaps` 少、原始行号靠前排序；从高到低保留基因对，若 query 或 subject 已经在已保留结果中出现，则剔除该冲突行。

## 推荐项目结构

```text
config.yaml
Snakefile
logs/
results/
work/
```

从模板初始化：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/ortholog-finder
WORK=/mnt/data/potato_agent/work/ortholog_<speciesA>_<speciesB>
mkdir -p "$WORK"
cp "$SKILL_DIR/templates/config.yaml" "$WORK/config.yaml"
cp "$SKILL_DIR/templates/Snakefile" "$WORK/Snakefile"
```

然后编辑 `config.yaml` 中两个物种的 `prefix`、`gff3`、`fasta`、`gff_type`、`gff_key`、`mcscan.dbtype` 和 `mcscan.cscore`。

## 参数建议

- `mcscan.dbtype: prot`：默认推荐，输入蛋白 FASTA，使用 `diamond_blastp`；
- `mcscan.dbtype: nucl`：输入 CDS FASTA，可按需要调整比对软件；
- `mcscan.cscore: 0.9`：近缘马铃薯基因组默认值；
- `mcscan.cscore: 0.99`：更接近严格一对一，但可能丢掉弱同源；
- `mcscan.cpus`：按机器资源设置。

执行前应告知用户计划使用的 `--cscore`。

## 运行

本技能使用模板化 Snakemake 流程。运行前先确认模板文件存在，并确保 `config.yaml` 中的 `env_prefix` 环境包含 `jcvi`、`snakemake`，蛋白模式下还需要 `diamond`。

```bash
test -s "$SKILL_DIR/templates/config.yaml"
test -s "$SKILL_DIR/templates/Snakefile"
```

若 `config.yaml` 中的 `env_prefix` 未设置，默认使用：

```bash
ENV=/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi
```

检查运行环境；若缺少 Snakemake 或 diamond，则优先补装到该环境：

```bash
ENV="$(python3 - <<'PY'
from pathlib import Path
import yaml
fallback = '/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi'
cfg = Path('config.yaml')
if cfg.exists():
    data = yaml.safe_load(cfg.read_text()) or {}
    print(data.get('env_prefix') or fallback)
else:
    print(fallback)
PY
)"
export PATH="$ENV/bin:$PATH"

if ! command -v snakemake >/dev/null 2>&1; then
  /opt/micromamba/bin/micromamba install -y -p "$ENV" \
    -c conda-forge -c bioconda snakemake
fi
if ! command -v diamond >/dev/null 2>&1; then
  /opt/micromamba/bin/micromamba install -y -p "$ENV" \
    -c conda-forge -c bioconda diamond
fi

python -m jcvi.compara.catalog ortholog -h >/dev/null
snakemake --version
```

Snakemake 路径：

```bash
cd "$WORK"
snakemake -n --cores 1
snakemake --cores 16 --printshellcmds --rerun-incomplete
```

长任务优先结合 `slurm-for-long-running-tasks` 提交 Slurm 后台运行。

## 输出

主要输出：

```text
results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv
results/id_check.tsv
```

辅助输出：

```text
results/<speciesA>.<speciesB>.last
results/<speciesA>.<speciesB>.last.filtered
logs/mcscan_ortholog.log
logs/one_to_one_best.log
```

`.one_to_one.best.tsv` 是最终结果，每个 query 和每个 subject 在该文件中最多出现一次。

## 结果验证

完成后至少检查：

```bash
wc -l results/<speciesA>.<speciesB>.last.filtered results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv
head -n 5 results/id_check.tsv
cat logs/one_to_one_best.log
```

确认最终结果一对一唯一性：

```bash
python - <<'PY'
from collections import Counter
path = 'results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv'
qs, ss = [], []
with open(path) as f:
    for line in f:
        if line.strip():
            a = line.rstrip('\n').split('\t')
            qs.append(a[0]); ss.append(a[1])
print('pairs', len(qs))
print('duplicated_query_ids', sum(v > 1 for v in Counter(qs).values()))
print('duplicated_subject_ids', sum(v > 1 for v in Counter(ss).values()))
PY
```

报告给用户：

- `id_check.tsv` 中两个物种 BED/FASTA ID 是否完全匹配；
- `.last.filtered` 原始候选行数；
- `.one_to_one.best.tsv` 最终保留行数与删除的冲突行数；
- 使用的 `dbtype`、`align_soft`、`cscore`、线程数；
- 若结果为空或极少，优先检查 ID 是否一致、FASTA 类型是否与 `dbtype` 匹配、`cscore` 是否过严。
